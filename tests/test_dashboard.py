from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from credusage.domain.models import (
    Availability,
    Connection,
    ConnectionHealth,
    ConnectionPreferences,
    ConnectionState,
    Freshness,
    IdentityBinding,
    IntegrationDescriptor,
    Provenance,
    QuotaKind,
    QuotaObservation,
    QuotaWindow,
    RefreshOutcome,
    SourcePolicy,
    UsageSnapshot,
)
from credusage.ui.dashboard import (
    ResetRefreshTracker,
    build_dashboard,
    overview_status,
    present_limit,
    primary_observation,
)

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
CONNECTION = Connection("one", "openai.codex")


def observation(key="five", **changes):
    return replace(
        QuotaObservation(
            "one",
            0,
            key,
            "Codex 5h",
            Provenance("codex.app-server", SourcePolicy.DOCUMENTED, "1", NOW, NOW),
            used_percent=Decimal("0.5"),
            availability=Availability.KNOWN,
            window=QuotaWindow(duration_seconds=18000, resets_at=NOW + timedelta(hours=2)),
            fresh_until=NOW + timedelta(minutes=5),
        ),
        **changes,
    )


def state(connection=CONNECTION, observations=(), **changes):
    snapshot = UsageSnapshot(
        connection.connection_id,
        connection.integration_id,
        connection.generation,
        IdentityBinding(),
        tuple(observations),
        NOW,
        plan="Plus",
    )
    return replace(
        ConnectionState(connection, snapshot, RefreshOutcome(health=ConnectionHealth.CONNECTED)),
        **changes,
    )


def dashboard(*states, pinned=None, demo=False):
    descriptor = IntegrationDescriptor("openai.codex", "openai", "Codex")
    return build_dashboard(tuple(states), (descriptor,), NOW, pinned, demo)


def codex(view):
    return next(p for p in view.providers if p.integration_id == "openai.codex")


def test_four_coding_providers_only_and_order():
    shown = dashboard(state(Connection("g", "google.gemini_app")))
    assert [p.name for p in shown.providers] == ["Antigravity", "Codex", "Claude Code", "Cursor"]
    assert all(not p.contexts for p in shown.providers)


def test_percent_fraction_overage_and_unknown_are_not_guessed():
    assert present_limit(observation(), NOW).percent_used == Decimal("0.5")
    assert present_limit(observation(used_percent=Decimal("105")), NOW).percent_used == 105
    unknown = present_limit(observation(used_percent=None), NOW)
    assert unknown.percent_used is None
    assert unknown.usage == "Usage unknown"
    assert present_limit(observation(availability=Availability.UNLIMITED), NOW).percent_used is None


def test_actual_used_limit_can_derive_percent_without_changing_quantity():
    shown = present_limit(
        observation(used_percent=None, used=Decimal("2"), limit=Decimal("8")), NOW
    )
    assert shown.percent_used == 25
    assert any("Limit: 8%" == fact for fact in shown.facts)
    assert (
        present_limit(
            observation(used_percent=None, used=Decimal("2"), limit=Decimal("0")), NOW
        ).percent_used
        is None
    )


def test_five_hours_wins_then_shortest_actual_window_and_never_credit():
    short = observation("short", window=QuotaWindow(duration_seconds=3600))
    five = observation()
    weekly = observation("weekly", window=QuotaWindow(duration_seconds=604800))
    credit = observation("credit", kind=QuotaKind.CREDIT, window=QuotaWindow(duration_seconds=60))
    assert primary_observation((weekly, short, five, credit)) == five
    assert primary_observation((weekly, credit, short)) == short
    assert present_limit(short, NOW).window_label == "1h window"
    assert present_limit(weekly, NOW).window_label == "7d window"
    assert primary_observation((credit,)) is None
    assert (
        present_limit(observation(window=QuotaWindow()), NOW).window_label == "Window not reported"
    )


def test_primary_ties_preserve_source_order_and_distinct_pools():
    first, second = observation("model-a"), observation("model-b")
    shown = codex(dashboard(state(observations=(first, second)))).selected
    assert shown.primary.key == "model-a"
    assert len(shown.limits) == 2


def test_connection_selection_uses_enabled_pin_then_order_without_aggregation():
    earlier = replace(
        CONNECTION, connection_id="earlier", preferences=ConnectionPreferences(order=-1)
    )
    later = replace(CONNECTION, connection_id="later")
    disabled = replace(CONNECTION, connection_id="disabled", enabled=False)
    states = (
        state(later, (observation(used_percent=Decimal(80)),)),
        state(earlier, (observation(used_percent=Decimal(20)),)),
        state(disabled),
    )
    assert codex(dashboard(*states)).selected.connection_id == "earlier"
    assert codex(dashboard(*states, pinned="later")).selected.primary.percent_used == 80
    assert codex(dashboard(*states, pinned="disabled")).selected.connection_id == "earlier"
    assert len(codex(dashboard(*states)).contexts) == 3


def test_health_freshness_and_verification_remain_distinct():
    obs = observation(fresh_until=NOW)
    shown = codex(
        dashboard(
            state(
                observations=(obs,),
                outcome=RefreshOutcome(health=ConnectionHealth.TRANSPORT_FAILURE),
            )
        )
    ).selected
    assert shown.primary.freshness == Freshness.STALE
    assert shown.primary.percent_used == Decimal("0.5")
    assert not shown.verified
    assert "Stale" in overview_status(shown)
    assert "Refresh failed" in overview_status(shown)


def test_reset_expiry_preserves_old_usage_and_absolute_reset():
    reset = NOW - timedelta(seconds=1)
    shown = present_limit(
        observation(window=QuotaWindow(duration_seconds=18000, resets_at=reset)), NOW
    )
    assert shown.freshness == Freshness.EXPIRED
    assert shown.percent_used == Decimal("0.5")
    assert shown.resets_at == reset
    assert shown.reset_label == "Reset passed · awaiting source"
    assert present_limit(observation(), NOW).reset_label == "Resets in 2h 0m"


def test_missing_context_and_missing_reset_are_explicit():
    assert overview_status(None) == "Configure in Settings"
    assert codex(dashboard(state())).selected.primary is None
    assert (
        present_limit(observation(window=QuotaWindow(duration_seconds=18000)), NOW).reset_label
        == "Reset not reported"
    )


def test_available_observation_does_not_get_an_unavailable_message():
    shown = codex(dashboard(state(observations=(observation(),)))).selected
    assert shown.message == ""


def test_reset_tracker_coalesces_limits_and_only_requests_each_reset_once():
    tracker = ResetRefreshTracker()
    due = observation(window=QuotaWindow(resets_at=NOW))
    states = (state(observations=(due, replace(due, bucket_key="other"))),)
    assert tracker.due(states, NOW - timedelta(seconds=1)) == ()
    assert tracker.due(states, NOW) == ("one",)
    assert tracker.due(states, NOW + timedelta(minutes=1)) == ()
    changed = (
        state(observations=(replace(due, window=QuotaWindow(resets_at=NOW + timedelta(hours=1))),)),
    )
    assert tracker.due(changed, NOW) == ()
    assert tracker.due(changed, NOW + timedelta(hours=1)) == ("one",)
    assert tracker.due((state(replace(CONNECTION, enabled=False), (due,)),), NOW) == ()


def test_demo_keeps_synthetic_identity_and_visible_demo_label():
    demo_state = state(Connection("demo", "example.demo"), (observation(),))
    shown = dashboard(demo_state, demo=True)
    assert shown.demo
    assert shown.providers[-1].name == "Demo fixtures"
    assert all(p.selected is None for p in shown.providers[:4])
