from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from credusage.domain.models import (
    ActionKind,
    Availability,
    Connection,
    ConnectionAction,
    ConnectionState,
    Freshness,
    IdentityBinding,
    ObservationBasis,
    Provenance,
    QuotaKind,
    QuotaObservation,
    QuotaWindow,
    RefreshOutcome,
    SourcePolicy,
    Unit,
    UsageSnapshot,
)
from credusage.ui.compact_details import credit_label, dashboard_action, duration_name, window_title
from credusage.ui.dashboard import present_context, present_limit
from credusage.ui.detail_layout import (
    DETAIL_HEIGHT,
    OVERVIEW_HEIGHT,
    WINDOW_WIDTH,
    detail_geometry,
    group_freshness,
    quota_groups,
    remaining_percent,
    selected_group,
)
from credusage.ui.popover import UsagePopover

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def limit(key, duration=18000, **changes):
    return replace(
        QuotaObservation(
            "one",
            0,
            key,
            "Original provider label",
            Provenance("codex.app-server", SourcePolicy.DOCUMENTED, "1", NOW, NOW),
            used_percent=Decimal("58"),
            availability=Availability.KNOWN,
            window=QuotaWindow(duration_seconds=duration, resets_at=NOW + timedelta(hours=2)),
            fresh_until=NOW + timedelta(minutes=10),
        ),
        **changes,
    )


def context(*observations, integration="openai.codex"):
    c = Connection("one", integration)
    state = ConnectionState(
        c,
        UsageSnapshot("one", integration, 0, IdentityBinding(), tuple(observations), NOW),
        RefreshOutcome(attempted_at=NOW),
    )
    return present_context(state, None, NOW)


def test_codex_groups_do_not_borrow_windows_or_credits_from_another_pool():
    credit = limit(
        "codex:standard:credits",
        None,
        kind=QuotaKind.CREDIT,
        unit=Unit.CREDITS,
        used_percent=None,
        remaining=Decimal("12.123456789"),
        credit_unit="Codex credits",
    )
    c = context(
        limit("codex:spark:secondary", 604800, used_percent=Decimal("90")),
        limit("codex:standard:primary"),
        credit,
        limit("codex:spark:primary", used_percent=Decimal("25")),
        limit("codex:standard:secondary", 604800, used_percent=Decimal("10")),
    )
    groups = quota_groups("openai.codex", c)
    standard = next(g for g in groups if g.key == "codex:standard")
    spark = next(g for g in groups if g.key == "codex:spark")
    assert (standard.short.percent_used, standard.long.percent_used) == (58, 10)
    assert standard.credits.remaining == Decimal("12.123456789")
    assert (spark.short.percent_used, spark.long.percent_used) == (25, 90)
    assert spark.credits is None
    assert selected_group(groups, "codex:standard", c.primary_key) == standard
    assert selected_group(tuple(reversed(groups)), "codex:standard", c.primary_key) == standard


def test_colons_in_pool_identity_and_missing_primary_are_preserved():
    c = context(limit("codex:scope:model:secondary", 259200))
    groups = quota_groups("openai.codex", c)
    assert len(groups) == 1 and groups[0].key == "codex:scope:model"
    assert groups[0].short is None
    assert duration_name(groups[0].long) == "3d"
    assert selected_group(groups, "removed-pool", c.primary_key) == groups[0]


def test_claude_uses_actual_durations_and_preserves_undated_telemetry():
    obs = limit(
        "claude:five_hour",
        provenance=Provenance(
            "statusline", SourcePolicy.LOCAL_TELEMETRY, "1", NOW, None, ObservationBasis.TELEMETRY
        ),
    )
    c = context(obs, limit("claude:seven_day", 604800), integration="anthropic.claude")
    group = quota_groups("anthropic.claude", c)[0]
    assert (duration_name(group.short), duration_name(group.long)) == ("5h", "7d")
    assert group.short.freshness == Freshness.UNDATED
    assert group_freshness(group) == "Undated"


def test_every_extra_allowance_and_credit_remains_selectable():
    observations = [limit(f"feature-{i}", (i + 1) * 3600) for i in range(7)]
    observations.append(
        limit(
            "credits",
            None,
            kind=QuotaKind.CREDIT,
            used_percent=None,
            remaining=Decimal("-2.1"),
            unit=Unit.CREDITS,
        )
    )
    c = context(*observations)
    groups = quota_groups("openai.codex", c)
    assert {item.key for group in groups for item in group.readings} == {
        o.bucket_key for o in observations
    }
    assert quota_groups("cursor.editor", None) == ()
    assert quota_groups("google.antigravity", context()) == ()


def test_remaining_fraction_overage_unknown_and_unlimited():
    assert remaining_percent(
        present_limit(limit("one", used_percent=Decimal("0.5")), NOW)
    ) == Decimal("99.5")
    assert remaining_percent(present_limit(limit("one", used_percent=Decimal("105")), NOW)) == 0
    assert remaining_percent(present_limit(limit("one", used_percent=None), NOW)) is None
    assert (
        remaining_percent(present_limit(limit("one", availability=Availability.UNLIMITED), NOW))
        is None
    )
    assert remaining_percent(None) is None


def test_expired_group_keeps_usage_and_reports_expiry():
    expired = limit(
        "codex:standard:primary", window=QuotaWindow(duration_seconds=18000, resets_at=NOW)
    )
    group = quota_groups(
        "openai.codex", context(expired, limit("codex:standard:secondary", 604800))
    )[0]
    assert group.short.percent_used == 58
    assert group_freshness(group) == "Expired"
    assert group_freshness(None) == "Unavailable"


def test_refresh_dates_are_structured_without_parsing_strings():
    c = context(limit("codex:standard:primary"))
    assert c.last_success_at == NOW
    assert c.last_checked_at == NOW
    assert c.limits[0].source_id == "codex.app-server"
    assert c.limits[0].fetched_at == NOW


def test_layout_uses_points_and_allocates_room_for_selectors_and_feedback():
    assert (WINDOW_WIDTH, OVERVIEW_HEIGHT, DETAIL_HEIGHT) == (256, 320, 392)
    for rows in (0, 1, 2):
        for feedback in (False, True):
            g = detail_geometry(selector_rows=rows, feedback=feedback)
            assert g.width == 256
            assert g.height == 392 + rows * 32 + (32 if feedback else 0)
            assert g.cards_y == 128 + rows * 32
            assert g.cards_y + 104 <= g.stats_y - 8
            assert g.stats_y + 36 <= g.source_y
            assert g.source_y + 30 <= g.actions_y
            assert g.actions_y + 24 <= g.feedback_y
            assert g.footer_y + 40 == g.height
            if feedback:
                assert g.feedback_y + 32 == g.footer_y


def test_window_titles_use_actual_reported_durations():
    assert window_title(present_limit(limit("five"), NOW), "Short term") == "5-hour"
    assert window_title(present_limit(limit("week", 604800), NOW), "Long term") == "Weekly"
    assert window_title(present_limit(limit("three", 259200), NOW), "Long term") == "3d"
    assert window_title(None, "Long term") == "Long term"


def test_overview_update_uses_oldest_successful_visible_numeric_reading():
    from credusage.ui.dashboard import DashboardView, ProviderView

    fresh = context(limit("one"))
    older = replace(fresh, last_success_at=NOW - timedelta(minutes=20))
    unknown = context(limit("unknown", used_percent=None))
    disabled = replace(fresh, enabled=False, last_success_at=NOW - timedelta(days=1))

    def provider(name, c):
        return ProviderView(name, name, "", (c,), c.connection_id)

    ui = UsagePopover.__new__(UsagePopover)
    ui.dashboard = DashboardView(
        tuple(
            provider(name, c)
            for name, c in (
                ("first", fresh),
                ("second", older),
                ("unknown", unknown),
                ("disabled", disabled),
                ("example.demo", replace(older, last_success_at=NOW - timedelta(days=2))),
            )
        )
    )
    assert ui.overview_updated_at() == older.last_success_at
    ui.dashboard = DashboardView((provider("unknown", unknown), provider("disabled", disabled)))
    assert ui.overview_updated_at() is None


def owner_for_actions(c, *, demo=False):
    calls = []
    dashboard = ConnectionAction(
        "dashboard",
        "Open Codex usage",
        ActionKind.OPEN_URL,
        "https://chatgpt.com/codex/settings/usage",
    )
    login = ConnectionAction("login", "Sign in", ActionKind.LOGIN, "codex", ("login",))
    controller = SimpleNamespace(
        config=SimpleNamespace(demo=demo),
        actions=lambda cid: (login, dashboard),
        refresh=lambda cid, reason: calls.append(("refresh", cid, reason)),
        run_action=lambda cid, action: calls.append(("dashboard", cid, action))
        or "Opened provider page.",
    )
    owner = UsagePopover.__new__(UsagePopover)
    owner.shell = SimpleNamespace(controller=controller, tick=lambda: None)
    owner.current_context = lambda: c
    owner.render = lambda **kwargs: None
    owner.action_message = ""
    return owner, calls


def test_quick_actions_use_selected_connection_and_only_dashboard_action():
    c = context(limit("codex:standard:primary"))
    owner, calls = owner_for_actions(c)
    owner.refresh_detail()
    owner.open_dashboard()
    assert calls[0] == ("refresh", "one", "manual")
    assert calls[1][0:2] == ("dashboard", "one")
    assert calls[1][2].action_id == "dashboard"
    assert dashboard_action(owner.shell.controller, None) is None


def test_quick_action_guards_and_failure_feedback():
    c = context(limit("one"))
    owner, calls = owner_for_actions(replace(c, loading=True), demo=True)
    owner.refresh_detail()
    owner.open_dashboard()
    assert calls == []
    owner.current_context = lambda: replace(c, enabled=False)
    owner.refresh_detail()
    assert calls == []
    owner.shell.controller.config.demo = False

    def fail(cid, action):
        raise OSError("sensitive details must not be displayed")

    owner.shell.controller.run_action = fail
    owner.open_dashboard()
    assert "could not be opened" in owner.action_message
    assert "sensitive" not in owner.action_message

    def fail_refresh(cid, reason):
        raise OSError("sensitive source path")

    owner.current_context = lambda: c
    owner.shell.controller.refresh = fail_refresh
    owner.refresh_detail()
    assert "could not be requested" in owner.action_message
    assert "sensitive" not in owner.action_message


def test_compact_credit_display_keeps_precision_in_source_record():
    balance = Decimal("2301.4767765")
    assert credit_label(balance) == "2,301.48"
    assert balance == Decimal("2301.4767765")
    assert credit_label(Decimal("12.5")) == "12.5"
    assert credit_label(Decimal("0")) == "0"
    assert credit_label(Decimal("0.004")) == "<0.01"
    assert credit_label(Decimal("-0.004")) == "−<0.01"
    assert credit_label(None) == "Not reported"
