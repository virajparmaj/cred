"""Regression evidence from the user's real CLI 1.4.0 /usage response."""

import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from credusage.domain.contracts import CollectRequest
from credusage.domain.errors import CollectionError, ErrorCode
from credusage.domain.models import (
    Availability,
    Capability,
    Connection,
    ConnectionState,
    Freshness,
    IdentityBinding,
    QuotaState,
    UsageSnapshot,
)
from credusage.domain.validation import validate_snapshot
from credusage.infrastructure.processes import CommandOutput
from credusage.providers.antigravity import AntigravityConnector, parse_usage
from credusage.ui.dashboard import present_context
from credusage.ui.detail_layout import quota_groups

FIXTURE = Path(__file__).parent / "fixtures/antigravity-1.4.0-usage.json"
NOW = datetime(2026, 10, 16, 16, 0, tzinfo=timezone.utc)


class FakeClock:
    def now(self):
        return NOW

    def monotonic(self):
        return time.monotonic()


def request(integration, **kwargs):
    return CollectRequest(
        Connection("test-connection", integration, **kwargs),
        NOW,
        time.monotonic() + 5,
        source_epoch=7,
    )


def payload():
    return json.loads(FIXTURE.read_bytes(), parse_float=Decimal)


def snapshot(result):
    return UsageSnapshot(
        result.connection_id,
        "google.antigravity",
        result.generation,
        result.binding,
        result.observations,
        NOW,
    )


def groups(result):
    state = ConnectionState(request("google.antigravity").connection, snapshot(result))
    return quota_groups("google.antigravity", present_context(state, None, NOW))


def test_real_usage_preserves_fraction_precision_windows_reset_and_identity():
    req = request("google.antigravity", binding=IdentityBinding(subject_id="old", verified=True))
    result = parse_usage(payload(), req, tool_version="1.4.0")
    validate_snapshot(snapshot(result))
    assert result.source_epoch == 7 and len(result.observations) == 4
    assert result.binding == IdentityBinding()
    assert Capability.QUOTAS in result.capabilities
    assert [item.used_percent for item in result.observations] == [
        Decimal("2.85746455192566"),
        Decimal("7.35445618629456"),
        Decimal("40.28559923171997"),
        Decimal("0"),
    ]
    assert [item.window.duration_seconds for item in result.observations] == [604800, 18000] * 2
    assert result.observations[0].window.resets_at.isoformat() == "2026-10-23T22:00:18+00:00"
    for item in result.observations:
        assert not item.binding.is_verified
        assert item.freshness(NOW) == Freshness.FRESH
        assert item.freshness(NOW + timedelta(minutes=10)) == Freshness.STALE
        assert item.freshness(item.window.resets_at) == Freshness.EXPIRED
        assert item.provenance.tool_version == "1.4.0"
    gemini, third_party = groups(result)
    assert (gemini.label, third_party.label) == ("Gemini Models", "Claude and GPT models")
    assert gemini.short.percent_used == Decimal("7.35445618629456")
    assert gemini.long.percent_used == Decimal("2.85746455192566")
    assert third_party.short.percent_used == 0
    assert third_party.long.percent_used == Decimal("40.28559923171997")


def test_missing_window_cannot_borrow_from_another_group():
    data = payload()
    data["command"]["data"]["groups"][0]["buckets"].pop()
    gemini, third_party = groups(parse_usage(data, request("google.antigravity")))
    assert gemini.short is None and gemini.long.duration_seconds == 604800
    assert third_party.short.duration_seconds == 18000


def test_bucket_ids_are_stable_when_display_labels_and_order_change():
    data = payload()
    before = parse_usage(data, request("google.antigravity"))
    for group in data["command"]["data"]["groups"]:
        group["buckets"].reverse()
        for bucket in group["buckets"]:
            bucket["name"] = "Changed display text"
    after = parse_usage(data, request("google.antigravity"))
    assert {o.bucket_key for o in before.observations} == {o.bucket_key for o in after.observations}
    assert [(g.short.key, g.long.key) for g in groups(before)] == [
        (g.short.key, g.long.key) for g in groups(after)
    ]


@pytest.mark.parametrize(
    "remaining,state",
    [(None, QuotaState.UNKNOWN), (0, QuotaState.EXHAUSTED), (1, QuotaState.AVAILABLE)],
)
def test_unknown_and_boundary_readings(remaining, state):
    data = payload()
    bucket = data["command"]["data"]["groups"][0]["buckets"][0]
    bucket.update(remaining_fraction=remaining, reset_time=None, window="future-window")
    obs = parse_usage(data, request("google.antigravity")).observations[0]
    assert obs.state == state
    assert obs.availability == (Availability.UNKNOWN if remaining is None else Availability.KNOWN)
    assert obs.window.duration_seconds is None and obs.window.resets_at is None
    assert obs.used_percent == (None if remaining is None else Decimal(100 * (1 - remaining)))


@pytest.mark.parametrize("remaining", [-1, 1.01, True, "NaN", "Infinity", {}, []])
def test_malformed_fractions_are_rejected(remaining):
    data = payload()
    data["command"]["data"]["groups"][0]["buckets"][0]["remaining_fraction"] = remaining
    with pytest.raises(ValueError):
        parse_usage(data, request("google.antigravity"))


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(status="ERROR"),
        lambda data: data["command"].update(name="prompt"),
        lambda data: data["command"].update(data={}),
        lambda data: data["command"]["data"].update(groups=[]),
        lambda data: data["command"]["data"]["groups"].append(data["command"]["data"]["groups"][0]),
        lambda data: data["command"]["data"]["groups"][0]["buckets"].append(
            data["command"]["data"]["groups"][0]["buckets"][0]
        ),
        lambda data: data["command"]["data"]["groups"][0]["buckets"][0].update(
            reset_time="2026-10-17T03:00:18"
        ),
        lambda data: data["command"]["data"]["groups"][0]["buckets"][0].pop("remaining_fraction"),
    ],
)
def test_schema_changes_are_rejected_atomically(change):
    data = payload()
    change(data)
    with pytest.raises(ValueError):
        parse_usage(data, request("google.antigravity"))


def test_collector_accepts_only_structured_usage_and_reports_safe_errors(monkeypatch):
    monkeypatch.setattr(
        "credusage.providers.antigravity.find_executable", lambda *args: "/fake/agy"
    )

    class Runner:
        raw = FIXTURE.read_bytes()
        code = 0
        calls = []

        def run(self, argv, deadline, **kwargs):
            self.calls.append(argv)
            return (
                CommandOutput(0, b"1.4.0")
                if "--version" in argv
                else CommandOutput(self.code, self.raw)
            )

    runner = Runner()
    connector = AntigravityConnector(FakeClock(), runner=runner)
    result = connector.collect(request("google.antigravity"))
    assert len(result.observations) == 4
    assert runner.calls == [
        ("/fake/agy", "--version"),
        ("/fake/agy", "-p", "/usage", "--output-format", "json"),
    ]
    assert result.observations[0].provenance.tool_version == "1.4.0"
    for raw in (
        b"not-json",
        b'[{"secret":"not exported"}]',
        b'{"status":"SUCCESS","response":"97%"}',
    ):
        runner.raw = raw
        with pytest.raises(CollectionError) as error:
            connector.collect(request("google.antigravity"))
        assert error.value.diagnostic.code == ErrorCode.SOURCE_SCHEMA_CHANGED
        assert "secret" not in error.value.diagnostic.message
    runner.code = 1
    with pytest.raises(CollectionError) as error:
        connector.collect(request("google.antigravity"))
    assert error.value.diagnostic.code == ErrorCode.SOURCE_UNAVAILABLE
