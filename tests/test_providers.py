from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from credusage.domain.contracts import CollectRequest, DiscoveryContext
from credusage.domain.errors import CollectionError, ErrorCode
from credusage.domain.models import (
    Availability,
    Connection,
    ConnectionPreferences,
    Freshness,
    IdentityBinding,
    Unit,
)
from credusage.infrastructure.processes import CommandOutput
from credusage.providers import built_in_connectors
from credusage.providers.antigravity import AntigravityConnector
from credusage.providers.claude import (
    ClaudeConnector,
    ingest_statusline,
    normalize_statusline,
    parse_telemetry,
    telemetry_path,
)
from credusage.providers.codex import CodexConnector, parse_rate_limits
from credusage.providers.example import ExampleConnector

NOW = datetime(2026, 10, 16, 16, 0, tzinfo=timezone.utc)


class FakeClock:
    def now(self):
        return NOW

    def monotonic(self):
        return time.monotonic()


def request(integration="openai.codex", **kwargs):
    return CollectRequest(
        Connection("test-connection", integration, **kwargs),
        NOW,
        time.monotonic() + 5,
        source_epoch=7,
    )


def source(**extra):
    return {
        "limitId": "codex",
        "limitName": "Codex",
        "planType": "plus",
        "primary": {"usedPercent": "0.5", "windowDurationMins": 300, "resetsAt": 2000000000},
        "secondary": {"usedPercent": 101, "windowDurationMins": 4320, "resetsAt": 2000100000},
        **extra,
    }


def payload(**extra):
    return {
        "accountId": "tenant-1",
        "rateLimitsByLimitId": {
            "codex": source(),
            "image": {
                "limitId": "image",
                "primary": {"usedPercent": 25, "windowDurationMins": 60, "resetsAt": None},
            },
        },
        **extra,
    }


def test_codex_prefers_multi_bucket_and_keeps_nonweekly_window_and_percentage_scale():
    result = parse_rate_limits(payload(rateLimits={"primary": {"usedPercent": 99}}), request())
    assert len(result.observations) == 3
    primary, secondary, image = result.observations
    assert primary.used_percent == Decimal("0.5")
    assert secondary.used_percent == 101
    assert secondary.window.duration_seconds == 259200
    assert "72h" in secondary.label
    assert image.window.resets_at is None
    assert result.binding.account_id == "tenant-1" and not result.binding.is_verified
    assert result.source_epoch == 7


def test_codex_legacy_and_null_values_do_not_manufacture_capacity():
    result = parse_rate_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": None, "windowDurationMins": None, "resetsAt": None},
                "secondary": None,
            }
        },
        request(),
    )
    (observation,) = result.observations
    assert observation.used_percent is None
    assert observation.availability == Availability.UNKNOWN
    assert not result.binding.is_verified


def test_codex_empty_map_does_not_resurrect_legacy_snapshot():
    assert not parse_rate_limits(
        payload(rateLimitsByLimitId={}, rateLimits=source()), request()
    ).observations


@pytest.mark.parametrize(
    "value", [True, False, "NaN", "Infinity", float("nan"), {}, [], "nonsense", -1]
)
def test_codex_invalid_percentage_rejected(value):
    body = {"rateLimits": source(primary={"usedPercent": value})}
    with pytest.raises(ValueError):
        parse_rate_limits(body, request())


def test_codex_credit_precision_unlimited_and_no_currency_guessing():
    body = {
        "rateLimits": source(
            credits={
                "hasCredits": True,
                "isUnlimited": False,
                "balance": "12.12345678901234567890123456789",
            }
        )
    }
    credit = parse_rate_limits(body, request()).observations[-1]
    assert credit.remaining == Decimal("12.12345678901234567890123456789")
    assert credit.unit == Unit.CREDITS and credit.currency is None
    body["rateLimits"]["credits"] = {"hasCredits": False, "isUnlimited": True, "balance": None}
    credit = parse_rate_limits(body, request()).observations[-1]
    assert credit.remaining is None and credit.availability == Availability.UNLIMITED


def test_codex_credit_negative_overage_preserved():
    result = parse_rate_limits({"rateLimits": source(credits={"balance": "-2.10"})}, request())
    assert result.observations[-1].remaining == Decimal("-2.10")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"rateLimitsByLimitId": []},
        {"rateLimits": []},
        {"rateLimitsByLimitId": {"bad": source()}},
        {"rateLimits": source(primary={"usedPercent": 1, "resetsAt": True})},
        {"rateLimits": source(credits={"hasCredits": 1})},
    ],
)
def test_codex_schema_changes_are_rejected(body):
    with pytest.raises(ValueError):
        parse_rate_limits(body, request())


def test_codex_configured_tenant_mismatch_is_rejected():
    with pytest.raises(CollectionError) as error:
        parse_rate_limits(
            payload(),
            request(
                binding=IdentityBinding(subject_id="person", account_id="other", verified=True)
            ),
        )
    assert error.value.diagnostic.code == ErrorCode.ACCOUNT_MISMATCH


class FakeRpc:
    def __init__(self, argv, on_notification=None):
        self.argv, self.callback, self.epoch, self.calls = argv, on_notification, 1, []
        self.accounts = [
            {"account": {"type": "chatgpt", "email": "same@example.invalid", "planType": "plus"}}
        ] * 2

    def start(self, deadline):
        pass

    def request(self, method, params, deadline):
        self.calls.append((method, params))
        return self.accounts.pop(0) if method == "account/read" else payload()

    def close(self):
        self.epoch += 1


def connector(monkeypatch, callback=None):
    monkeypatch.setattr("credusage.providers.codex.find_executable", lambda *args: "/fake/codex")
    adapter = CodexConnector(FakeClock(), callback, transport_factory=FakeRpc)
    return adapter


def test_codex_collect_only_passive_vendor_reads_and_push_invalidates(monkeypatch):
    invalidations = []
    adapter = connector(monkeypatch, invalidations.append)
    result = adapter.collect(request())
    assert adapter._transport.calls == [
        ("account/read", {"refreshToken": False}),
        ("account/rateLimits/read", {}),
        ("account/read", {"refreshToken": False}),
    ]
    assert adapter._transport.argv == ("/fake/codex", "app-server", "--listen", "stdio://")
    assert not result.binding.is_verified
    adapter._notification("account/rateLimits/updated", {"usedPercent": 100})
    assert invalidations == ["test-connection"]
    adapter._notification("unrelated", {})
    assert invalidations == ["test-connection"]


def test_codex_account_change_during_read_rejected(monkeypatch):
    adapter = connector(monkeypatch)
    transport = FakeRpc(())
    transport.accounts[1] = {
        "account": {"type": "chatgpt", "email": "changed@example.invalid", "planType": "plus"}
    }
    adapter._transport, adapter._executable = transport, "/fake/codex"
    with pytest.raises(CollectionError) as error:
        adapter.collect(request())
    assert error.value.diagnostic.code == ErrorCode.ACCOUNT_MISMATCH


@pytest.mark.parametrize(
    "account,code",
    [(None, ErrorCode.AUTH_REQUIRED), ({"type": "apiKey"}, ErrorCode.UNSUPPORTED_AUTH_MODE)],
)
def test_codex_no_credentials_or_api_key_does_not_request_usage(monkeypatch, account, code):
    adapter = connector(monkeypatch)
    transport = FakeRpc(())
    transport.accounts[0] = {"account": account}
    adapter._transport, adapter._executable = transport, "/fake/codex"
    with pytest.raises(CollectionError) as error:
        adapter.collect(request())
    assert error.value.diagnostic.code == code
    assert len(transport.calls) == 1


def test_codex_process_epoch_change_rejects_results(monkeypatch):
    adapter = connector(monkeypatch)
    transport = FakeRpc(())
    original = transport.request

    def changing(method, params, deadline):
        result = original(method, params, deadline)
        if method == "account/rateLimits/read":
            transport.epoch += 1
        return result

    transport.request = changing
    adapter._transport, adapter._executable = transport, "/fake/codex"
    with pytest.raises(CollectionError) as error:
        adapter.collect(request())
    assert error.value.diagnostic.code == ErrorCode.ACCOUNT_MISMATCH


def raw_claude(**extra):
    return json.dumps(
        {
            "session_id": "session-fixture",
            "version": "2.1.80",
            "rate_limits": {
                "five_hour": {"used_percentage": 0.5, "resets_at": 2000000000},
                "seven_day": {"used_percentage": 95, "resets_at": 2000100000},
            },
            **extra,
        }
    ).encode()


def test_claude_ingestion_is_private_minimal_undated_and_generation_bound(tmp_path):
    req = request("anthropic.claude")
    path = ingest_statusline(
        req.connection,
        raw_claude(
            access_token="secret",
            transcript_path="/private/raw",
            timestamp=1234,
            other={"secret": "token"},
        ),
        tmp_path,
        NOW,
    )
    data = json.loads(path.read_text())
    assert set(data) == {
        "schema_version",
        "connection_id",
        "generation",
        "ingested_at",
        "event_at",
        "session_id",
        "version",
        "buckets",
    }
    assert "secret" not in path.read_text() and "transcript" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    result = ClaudeConnector(FakeClock(), tmp_path).collect(req)
    assert result.observations[0].used_percent == Decimal("0.5")
    assert result.observations[0].freshness(NOW) == Freshness.UNDATED
    assert result.observations[0].provenance.event_at is None
    assert not result.binding.is_verified
    assert result.source_epoch == 7
    later = replace(req, connection=replace(req.connection, generation=1))
    with pytest.raises(CollectionError) as error:
        ClaudeConnector(FakeClock(), tmp_path).collect(later)
    assert error.value.diagnostic.code == ErrorCode.ACCOUNT_MISMATCH


def test_claude_repeated_undated_payload_and_restart_do_not_gain_event_date(tmp_path):
    req = request("anthropic.claude")
    ingest_statusline(req.connection, raw_claude(), tmp_path, NOW)
    original = ClaudeConnector(FakeClock(), tmp_path).collect(req).observations[0]
    assert (
        ClaudeConnector(FakeClock(), tmp_path).collect(req).observations[0].provenance.fetched_at
        == NOW
    )
    ingest_statusline(req.connection, raw_claude(), tmp_path, NOW + timedelta(minutes=1))
    repeated = ClaudeConnector(FakeClock(), tmp_path).collect(req).observations[0]
    assert original.provenance.event_at is repeated.provenance.event_at is None
    assert repeated.freshness(NOW + timedelta(minutes=2)) == Freshness.UNDATED


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b"{}",
        b"x" * (64 * 1024 + 1),
        b'{"rate_limits":{"five_hour":{"used_percentage":true}}}',
        b'{"rate_limits":{"five_hour":{"used_percentage":"NaN"}}}',
        b'{"rate_limits":{"five_hour":{"resets_at":"2026-01-01T00:00:00Z"}}}',
    ],
)
def test_claude_rejects_bad_input_without_writing(tmp_path, raw):
    with pytest.raises(ValueError):
        ingest_statusline(request("anthropic.claude").connection, raw, tmp_path, NOW)
    assert not list(tmp_path.rglob("*.json"))


def test_claude_missing_and_malformed_inbox_are_safe_diagnostics(tmp_path):
    req = request("anthropic.claude")
    adapter = ClaudeConnector(FakeClock(), tmp_path)
    with pytest.raises(CollectionError) as error:
        adapter.collect(req)
    assert error.value.diagnostic.code == ErrorCode.SOURCE_UNAVAILABLE
    path = telemetry_path(req.connection.connection_id, tmp_path)
    path.parent.mkdir()
    path.write_text('{"secret":"dont-print-me"}')
    with pytest.raises(CollectionError) as error:
        adapter.collect(req)
    assert error.value.diagnostic.code == ErrorCode.INVALID_LOCAL_DATA
    assert "dont-print-me" not in str(error.value.diagnostic)


def test_claude_null_and_unknown_buckets_remain_unknown_or_absent(tmp_path):
    raw = b'{"rate_limits":{"five_hour":{"used_percentage":null,"resets_at":null},"unverified_model":{"used_percentage":99}}}'
    req = request("anthropic.claude")
    ingest_statusline(req.connection, raw, tmp_path, NOW)
    (obs,) = ClaudeConnector(FakeClock(), tmp_path).collect(req).observations
    assert obs.used_percent is None and obs.availability == Availability.UNKNOWN


class FakeRunner:
    def __init__(self, version=b"Antigravity CLI 1.2.15", code=0):
        self.version, self.calls, self.code = version, [], code

    def run(self, argv, deadline, **kwargs):
        self.calls.append(argv)
        return CommandOutput(
            self.code, self.version if "--version" in argv else b'{"unknown": "unverified"}'
        )


def test_antigravity_version_gate_and_exact_passive_command(monkeypatch):
    monkeypatch.setattr(
        "credusage.providers.antigravity.find_executable", lambda *args: "/fake/agy"
    )
    runner = FakeRunner()
    adapter = AntigravityConnector(FakeClock(), runner=runner)
    with pytest.raises(CollectionError) as error:
        adapter.collect(request("google.antigravity"))
    assert error.value.diagnostic.code == ErrorCode.SOURCE_SCHEMA_CHANGED
    assert runner.calls == [
        ("/fake/agy", "--version"),
        ("/fake/agy", "-p", "/usage", "--output-format", "json"),
    ]


@pytest.mark.parametrize(
    "version,code",
    [(b"1.2.14", ErrorCode.UNSUPPORTED_CAPABILITY), (b"invalid", ErrorCode.SOURCE_SCHEMA_CHANGED)],
)
def test_antigravity_unsupported_version_never_issues_usage(monkeypatch, version, code):
    monkeypatch.setattr(
        "credusage.providers.antigravity.find_executable", lambda *args: "/fake/agy"
    )
    runner = FakeRunner(version)
    with pytest.raises(CollectionError) as error:
        AntigravityConnector(runner=runner).collect(request("google.antigravity"))
    assert error.value.diagnostic.code == code and len(runner.calls) == 1


def test_registry_keeps_consumer_products_separate_and_demo_disabled(tmp_path):
    registry = built_in_connectors(root=tmp_path)
    ids = {adapter.descriptor.integration_id for adapter in registry}
    assert ids == {
        "openai.codex",
        "anthropic.claude",
        "google.antigravity",
        "cursor.editor",
        "openai.chatgpt",
        "google.gemini_app",
    }
    for adapter in registry:
        if adapter.descriptor.integration_id in {
            "cursor.editor",
            "openai.chatgpt",
            "google.gemini_app",
        }:
            with pytest.raises(CollectionError) as error:
                adapter.collect(request(adapter.descriptor.integration_id))
            assert error.value.diagnostic.code == ErrorCode.UNSUPPORTED_CAPABILITY
            assert adapter.actions(request().connection)
    with pytest.raises(CollectionError):
        ExampleConnector().collect(request("example.demo"))
    demo = ExampleConnector(FakeClock(), enabled=True)
    observations = demo.collect(request("example.demo")).observations
    assert len(observations) == 5
    assert observations[0].shared_pool_id == observations[1].shared_pool_id
    assert observations[0].sharing_evidence
    assert observations[2].used_percent is None
    with pytest.raises(CollectionError):
        demo.collect(replace(request("example.demo"), reason="demo-failure"))


def test_reported_fetch_does_not_invent_provider_event_time_or_rolling_identity():
    observation = parse_rate_limits(payload(), request()).observations[0]
    assert observation.provenance.event_at is None
    assert observation.provenance.fetched_at == NOW
    assert observation.freshness(NOW) == Freshness.FRESH
    assert observation.window.instance_id is None


def test_claude_moving_reset_preserves_rolling_bucket_identity(tmp_path):
    req = request("anthropic.claude")
    ingest_statusline(req.connection, raw_claude(), tmp_path, NOW)
    original = ClaudeConnector(root=tmp_path).collect(req).observations[0]
    changed = json.loads(raw_claude())
    changed["rate_limits"]["five_hour"]["resets_at"] += 60
    ingest_statusline(req.connection, json.dumps(changed).encode(), tmp_path, NOW)
    newer = ClaudeConnector(root=tmp_path).collect(req).observations[0]
    assert original.window.instance_id is newer.window.instance_id is None
    assert original.bucket_key == newer.bucket_key


def test_codex_login_is_explicit_vendor_command_action(monkeypatch):
    from credusage.domain.models import ActionKind, Capability

    adapter = connector(monkeypatch)
    login = next(
        action
        for action in adapter.actions(request().connection)
        if action.kind == ActionKind.LOGIN
    )
    assert login.target == "/fake/codex" and login.argv == ("login",)
    assert Capability.LOGIN in adapter.descriptor.capabilities


def test_codex_callback_distinguishes_account_change_from_quota_push(monkeypatch):
    notifications = []
    adapter = connector(
        monkeypatch, lambda key, account_changed=False: notifications.append((key, account_changed))
    )
    adapter.collect(request())
    adapter._notification("account/rateLimits/updated", {})
    adapter._notification("account/updated", {})
    assert notifications == [("test-connection", False), ("test-connection", True)]


def test_codex_push_during_collection_is_coalesced_without_discarding_result(monkeypatch):
    from threading import Event

    from credusage.core.coordinator import RefreshCoordinator
    from credusage.core.registry import ConnectorRegistry
    from credusage.domain.models import ConnectionHealth

    monkeypatch.setattr("credusage.providers.codex.find_executable", lambda *args: "/fake/codex")
    ready = Event()
    accepted = []

    class Repository:
        def load(self, connection):
            return None

        def save(self, snapshot):
            accepted.append(snapshot)

    class PushRpc(FakeRpc):
        def request(self, method, params, deadline):
            if method == "account/rateLimits/read":
                self.callback("account/rateLimits/updated", {"usedPercent": 100})
            return super().request(method, params, deadline)

    coordinator = None

    def invalidate(key, account_changed=False):
        if account_changed:
            coordinator.invalidate(key)
        else:
            coordinator.request_refresh(key, reason="push")

    def changed(states):
        if any(state.snapshot is not None for state in states):
            ready.set()

    adapter = CodexConnector(FakeClock(), invalidate, transport_factory=PushRpc)
    coordinator = RefreshCoordinator(
        ConnectorRegistry((adapter,)),
        (request().connection,),
        Repository(),
        clock=FakeClock(),
        on_change=changed,
        jitter=lambda value: 0,
    )
    try:
        coordinator.request_refresh(reason="startup")
        assert ready.wait(3), "A quota push must not perpetually discard the active read"
        (state,) = coordinator.states()
        assert state.outcome.health == ConnectionHealth.CONNECTED
        assert state.snapshot.observations[0].used_percent == Decimal("0.5")
        assert len(accepted) == 1
        assert coordinator._epochs[state.connection.connection_id] == 0
    finally:
        coordinator.close()


def test_demo_registry_never_constructs_real_providers_or_resources(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo must not construct real provider resources")

    monkeypatch.setattr("credusage.providers.CodexConnector", forbidden)
    monkeypatch.setattr("credusage.providers.ClaudeConnector", forbidden)
    monkeypatch.setattr("credusage.providers.AntigravityConnector", forbidden)
    monkeypatch.setattr("credusage.providers.dashboard_connectors", forbidden)
    providers = built_in_connectors(clock=FakeClock(), root=tmp_path, demo=True)
    assert len(providers) == 1
    assert isinstance(providers[0], ExampleConnector)
    assert providers[0].enabled
    assert providers[0].descriptor.integration_id == "example.demo"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("source_type", ["fifo", "symlink", "directory", "device", "oversized"])
def test_claude_rejects_nonregular_or_unsafe_source_without_blocking(tmp_path, source_type):
    import os

    path = tmp_path / "inbox.json"
    if source_type == "fifo":
        os.mkfifo(path)
    elif source_type == "symlink":
        regular = tmp_path / "actual.json"
        regular.write_text("{}")
        path.symlink_to(regular)
    elif source_type == "directory":
        path.mkdir()
    elif source_type == "device":
        path = Path("/dev/zero")
        if not path.exists():
            pytest.skip("Device fixture unavailable")
    else:
        path.write_bytes(b"x" * (64 * 1024 + 1))
    req = request("anthropic.claude", preferences=ConnectionPreferences(source_path=str(path)))
    started = time.monotonic()
    with pytest.raises(CollectionError) as error:
        ClaudeConnector(root=tmp_path).collect(req)
    assert error.value.diagnostic.code == ErrorCode.INVALID_LOCAL_DATA
    assert time.monotonic() - started < 1


def test_claude_rejects_symlink_swap_at_open(monkeypatch, tmp_path):
    import errno

    path = tmp_path / "inbox.json"
    path.write_text("{}")

    def replaced_with_symlink(*args, **kwargs):
        raise OSError(errno.ELOOP, "sensitive-path")

    monkeypatch.setattr("credusage.providers.claude.os.open", replaced_with_symlink)
    req = request("anthropic.claude", preferences=ConnectionPreferences(source_path=str(path)))
    with pytest.raises(CollectionError) as error:
        ClaudeConnector(root=tmp_path).collect(req)
    assert error.value.diagnostic.code == ErrorCode.INVALID_LOCAL_DATA
    assert "sensitive-path" not in error.value.diagnostic.message


def test_window_kind_requires_source_evidence_and_is_independent_of_duration(tmp_path):
    from credusage.domain.models import WindowKind

    codex_observations = parse_rate_limits(payload(), request()).observations
    assert all(item.window.kind == WindowKind.UNKNOWN for item in codex_observations)
    req = request("anthropic.claude")
    ingest_statusline(req.connection, raw_claude(), tmp_path, NOW)
    short, weekly = ClaudeConnector(root=tmp_path).collect(req).observations
    assert short.window.kind == WindowKind.ROLLING
    assert weekly.window.kind == WindowKind.UNKNOWN
    assert weekly.window.duration_seconds == 604800
