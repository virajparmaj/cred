from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from credusage.domain.models import (
    Availability,
    Connection,
    ConnectionHealth,
    ConnectionState,
    IdentityBinding,
    IntegrationDescriptor,
    ObservationBasis,
    Provenance,
    QuotaObservation,
    QuotaState,
    QuotaWindow,
    RefreshOutcome,
    SourcePolicy,
    Unit,
    UsageSnapshot,
)
from credusage.ui.presentation import build_menu, present_bucket, usage_label

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
BINDING = IdentityBinding(subject_id="person-1", verified=True)
CONNECTION = Connection("one", "codex", label="A local context")
DESCRIPTOR = IntegrationDescriptor("codex", "openai", "Codex")


def observation(**changes):
    return replace(
        QuotaObservation(
            "one",
            0,
            "five",
            "Five hours",
            Provenance("codex-app-server", SourcePolicy.DOCUMENTED, "1", NOW, NOW),
            binding=BINDING,
            used_percent=Decimal("32.5"),
            availability=Availability.KNOWN,
            window=QuotaWindow(instance_id="window-1", resets_at=NOW + timedelta(hours=2)),
            fresh_until=NOW + timedelta(minutes=5),
        ),
        **changes,
    )


def state(*observations, health=ConnectionHealth.CONNECTED):
    return ConnectionState(
        CONNECTION,
        UsageSnapshot("one", "codex", 0, BINDING, tuple(observations), NOW),
        RefreshOutcome(health=health, message="Safe source status"),
    )


def test_missing_usage_is_unknown_not_zero():
    unknown = observation(used_percent=None, used=None, remaining=None)
    assert usage_label(unknown) == "Usage unknown"
    empty = build_menu((ConnectionState(CONNECTION),), (DESCRIPTOR,), NOW)
    assert "Usage unknown" in " ".join(empty.connections[0].details)
    assert "Not connected" in empty.connections[0].title
    assert "0%" not in str(empty)


def test_percent_display_rounds_without_rescaling():
    assert usage_label(observation(used_percent=Decimal("0.5"))) == "1% used"
    assert usage_label(observation(used_percent=Decimal("105"))) == "105% used"


def test_health_and_freshness_remain_separate_and_textual():
    stale = observation(fresh_until=NOW - timedelta(seconds=1))
    menu = build_menu((state(stale),), (DESCRIPTOR,), NOW)
    assert "Connected" in menu.connections[0].title
    assert "stale" in menu.connections[0].buckets[0].title
    assert "33% used" in menu.connections[0].buckets[0].title
    failed = build_menu(
        (state(stale, health=ConnectionHealth.TRANSPORT_FAILURE),), (DESCRIPTOR,), NOW
    )
    assert "Refresh failed" in failed.connections[0].title
    assert "stale" in failed.connections[0].buckets[0].title


def test_expired_reading_remains_historical_without_refill():
    expired = observation(window=QuotaWindow(resets_at=NOW))
    shown = present_bucket(expired, NOW)
    assert "33% used" in shown.title
    assert "expired historical reading" in shown.title
    assert any("Expected reset passed; verification needed" in text for text in shown.details)


def test_pin_reflects_usage_and_freshness():
    menu = build_menu((state(observation()),), (DESCRIPTOR,), NOW, "one", "five")
    assert menu.title == "Codex: 33% used · 2h 0m"
    assert menu.connections[0].buckets[0].pinned
    stale = build_menu((state(observation(fresh_until=NOW)),), (DESCRIPTOR,), NOW, "one", "five")
    assert stale.title.endswith("stale")


def test_basis_identity_source_and_time_are_visible_without_color():
    shown = present_bucket(observation(binding=IdentityBinding()), NOW)
    text = " ".join(shown.details)
    for expected in (
        "Source: codex-app-server",
        "Basis: provider reported",
        "As of:",
        "Fetched:",
        "unverified local context",
        "Window:",
    ):
        assert expected in text


def test_undated_telemetry_is_not_presented_as_fresh():
    provenance = Provenance(
        "statusline", SourcePolicy.LOCAL_TELEMETRY, "1", NOW, basis=ObservationBasis.TELEMETRY
    )
    shown = present_bucket(observation(provenance=provenance), NOW)
    assert "undated" in shown.title
    assert "As of: source time unknown" in shown.details


def test_currency_and_credits_do_not_hide_units_or_round():
    money = observation(
        used_percent=None, unit=Unit.CURRENCY, remaining=Decimal("0.012345"), currency="USD"
    )
    assert usage_label(money) == "USD 0.012345 remaining"
    credit = observation(
        used_percent=None,
        unit=Unit.CREDITS,
        remaining=Decimal("1.2300"),
        credit_unit="model credits",
    )
    assert usage_label(credit) == "1.23 model credits remaining"


def test_qualitative_and_unlimited_are_not_fabricated_percentages():
    qualitative = observation(used_percent=None, state=QuotaState.LOW)
    assert usage_label(qualitative) == "Low (reported; quantity unknown)"
    assert (
        usage_label(observation(used_percent=None, availability=Availability.UNLIMITED))
        == "Unlimited (reported)"
    )


def test_long_labels_and_shared_pool_evidence_survive():
    label = "Very long provider quota " * 8
    shown = present_bucket(
        observation(label=label, shared_pool_id="pool-1", sharing_evidence="Documented pool"), NOW
    )
    assert shown.title.startswith(label)
    assert "Shared pool: pool-1" in shown.details
    assert "Sharing evidence: Documented pool" in shown.details


def test_demo_and_disabled_context_are_explicit():
    disabled = replace(state(observation()), connection=replace(CONNECTION, enabled=False))
    menu = build_menu((disabled,), (DESCRIPTOR,), NOW, demo=True)
    assert menu.title == "CredUsage demo"
    assert "disabled" in menu.connections[0].title
