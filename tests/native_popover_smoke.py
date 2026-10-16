"""Manual WindowServer smoke: python tests/native_popover_smoke.py (synthetic data only)."""

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import AppKit as A

from credusage.controller import Controller
from credusage.domain.models import (
    ActionKind,
    Availability,
    Connection,
    ConnectionAction,
    ConnectionHealth,
    ConnectionState,
    IdentityBinding,
    IntegrationDescriptor,
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
from credusage.ui.app import NativeShell
from credusage.ui.dashboard import PROVIDERS
from credusage.ui.settings import show_settings

now = datetime.now(timezone.utc)
source = Provenance(
    "synthetic.native-qa", SourcePolicy.DOCUMENTED, "1", now, now, ObservationBasis.DEMO
)
states = []
for index, (integration, name, _) in enumerate(PROVIDERS):
    connection = Connection(f"qa-{index}", integration, label="Synthetic visual QA")
    observations = ()
    if integration in ("openai.codex", "anthropic.claude"):
        observations = tuple(
            QuotaObservation(
                connection.connection_id,
                0,
                (
                    f"codex:standard:{'primary' if key == 'five' else 'secondary'}"
                    if integration == "openai.codex"
                    else key
                ),
                label,
                source,
                used_percent=Decimal(value),
                availability=Availability.KNOWN,
                window=QuotaWindow(
                    duration_seconds=seconds, resets_at=now + timedelta(hours=hours)
                ),
                fresh_until=now + timedelta(minutes=10),
            )
            for key, label, value, seconds, hours in (
                ("five", "5-hour allowance", "62.5" if index == 2 else "58", 18000, 2),
                ("weekly", "Weekly allowance", "29" if index == 2 else "10", 604800, 72),
            )
        )
    if integration == "openai.codex":
        observations += (
            replace(observations[0], bucket_key="codex:spark:primary", used_percent=Decimal("25")),
            replace(
                observations[1], bucket_key="codex:spark:secondary", used_percent=Decimal("90")
            ),
            replace(
                observations[0],
                bucket_key="codex:standard:credits",
                label="Codex credits",
                kind=QuotaKind.CREDIT,
                unit=Unit.CREDITS,
                used_percent=None,
                remaining=Decimal("2301.4767765"),
                credit_unit="Codex credits",
            ),
        )
    if integration == "anthropic.claude":
        observations = tuple(
            replace(
                item,
                provenance=replace(
                    source,
                    source_kind=SourcePolicy.LOCAL_TELEMETRY,
                    event_at=None,
                    basis=ObservationBasis.TELEMETRY,
                ),
            )
            for item in observations
        )
    snapshot = UsageSnapshot(
        connection.connection_id, integration, 0, IdentityBinding(), observations, now, plan="Demo"
    )
    states.append(
        ConnectionState(
            connection,
            snapshot,
            RefreshOutcome(
                attempted_at=now,
                health=(
                    ConnectionHealth.CONNECTED
                    if observations
                    else ConnectionHealth.SOURCE_UNAVAILABLE
                ),
                message=(
                    "Synthetic visual QA only."
                    if observations
                    else "Numerical usage is unavailable. Configure this source in Settings."
                ),
            ),
        )
    )
# A second context validates selection without sharing values.
other = Connection("other", "openai.codex", label="Second synthetic context")
second_obs = (
    QuotaObservation(
        "other",
        0,
        "five",
        "5-hour allowance",
        source,
        used_percent=Decimal("10"),
        availability=Availability.KNOWN,
        window=QuotaWindow(duration_seconds=18000, resets_at=now + timedelta(hours=1)),
        fresh_until=now + timedelta(minutes=10),
    ),
)
states.append(
    ConnectionState(
        other, UsageSnapshot("other", "openai.codex", 0, IdentityBinding(), second_obs, now)
    )
)


class FakeController:
    config = SimpleNamespace(pinned_connection_id="qa-1", demo=True)
    registry = SimpleNamespace(
        descriptors=lambda: tuple(
            IntegrationDescriptor(i, i.split(".")[0], n) for i, n, _ in PROVIDERS
        )
    )
    clock = SimpleNamespace(now=lambda: now)

    def __init__(self):
        self.requests = []
        self.closed = False
        self.opened = []

    def states(self):
        return tuple(states)

    def refresh(self, connection_id=None, reason="manual"):
        self.requests.append((connection_id, reason))

    def drain_notifications(self):
        return ()

    def actions(self, connection_id):
        integration = next(
            s.connection.integration_id
            for s in states
            if s.connection.connection_id == connection_id
        )
        url, label = {
            "openai.codex": ("https://chatgpt.com/codex/settings/usage", "Open Codex usage"),
            "anthropic.claude": ("https://claude.ai/settings/usage", "Open Claude usage"),
            "cursor.editor": ("https://cursor.com/dashboard", "Open Cursor"),
            "google.antigravity": ("https://antigravity.google/", "Open Antigravity"),
        }[integration]
        return (ConnectionAction("dashboard", label, ActionKind.OPEN_URL, url),)

    def run_action(self, connection_id, action):
        self.opened.append((connection_id, action.action_id))
        return "Opened provider page (mock)."

    def close(self):
        self.closed = True


controller = FakeController()
shell = NativeShell(controller)
shell.usage.popover.setAnimates_(False)
checks = {}
qa = Path(".qa/compact-popover")
qa.mkdir(exist_ok=True)


def capture(view, name):
    view.displayIfNeeded()
    bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
    bitmap.representationUsingType_properties_(
        A.NSBitmapImageFileTypePNG, {}
    ).writeToFile_atomically_(str((qa / name).resolve()), True)


def assert_no_scroll_and_fit(view):
    def no_scroll(node):
        assert not isinstance(node, A.NSScrollView)
        for child in node.subviews():
            no_scroll(child)

    no_scroll(view)
    bounds = view.bounds()
    assert (bounds.size.width, bounds.size.height) == (200, 260)
    assert tuple(shell.usage.popover.contentSize()) == (250, 325)
    assert tuple(view.frame().size) == (250, 325)
    material = next(v for v in view.subviews() if isinstance(v, A.NSVisualEffectView))
    assert material.material() == A.NSVisualEffectMaterialPopover
    assert material.blendingMode() == A.NSVisualEffectBlendingModeWithinWindow
    neutral = shell.usage.accent("openai.codex").colorUsingColorSpace_(
        A.NSColorSpace.deviceRGBColorSpace()
    )
    assert abs(neutral.redComponent() - neutral.greenComponent()) < 0.01
    assert abs(neutral.greenComponent() - neutral.blueComponent()) < 0.01
    buttons = {v.title(): v for v in view.subviews() if isinstance(v, A.NSButton)}
    assert tuple(buttons["Settings…"].frame().origin) == (8, 239)
    assert tuple(buttons["Quit"].frame().origin) == (153, 239)
    assert tuple(buttons["Quit"].frame().size) == (39, 18)
    for child in view.subviews():
        frame = child.frame()
        assert frame.origin.x >= -1 and frame.origin.y >= -1
        assert frame.origin.x + frame.size.width <= bounds.size.width + 1, (
            type(child),
            frame,
            bounds,
        )
        assert frame.origin.y + frame.size.height <= bounds.size.height + 1, (
            type(child),
            frame,
            bounds,
        )
        # Native button/picker internals manage their own clipped text geometry.
        # Check the frames we own, and recursively forbid scroll views everywhere.


def inspect(timer):
    try:
        shell.toggle()
        assert shell.usage.popover.isShown()
        checks["native_popover_opened"] = True
        assert shell.status_item.button().title() == ""
        checks["icon_only_menu_bar"] = True
        assert shell.usage.popover.appearance() is None
        for theme, appearance in [
            ("light", A.NSAppearanceNameAqua),
            ("dark", A.NSAppearanceNameDarkAqua),
        ]:
            shell.application.setAppearance_(A.NSAppearance.appearanceNamed_(appearance))
            # Emulate the menu bar's system appearance without changing the Mac's theme.
            shell.usage.popover.setAppearance_(A.NSAppearance.appearanceNamed_(appearance))
            shell.usage.overview()
            shell.usage.render(force=True)
            assert shell.usage.is_dark() == (theme == "dark")
            assert_no_scroll_and_fit(shell.usage.controller.view())
            rows = [v for v in shell.usage.focusables if isinstance(v, shell.usage.Row)]
            assert len(rows) == 4
            assert all(tuple(row.frame().size) == (188, 43) for row in rows)
            capture(shell.usage.controller.view(), f"overview-{theme}.png")
            shell.usage.group_keys.pop("qa-1", None)
            # Second provider row is Codex, triggered through target/action.
            shell.usage.focusables[1].performClick_(None)
            assert shell.usage.provider_id == "openai.codex"
            capture(shell.usage.controller.view(), f"details-{theme}.png")
            assert_no_scroll_and_fit(shell.usage.controller.view())
            credit = next(
                v
                for v in shell.usage.controller.view().subviews()
                if isinstance(v, A.NSTextField) and v.stringValue() == "2,301.48"
            )
            assert "2301.4767765" in credit.accessibilityLabel()
            pools = next(
                v
                for v in shell.usage.controller.view().subviews()
                if isinstance(v, A.NSPopUpButton) and v.accessibilityLabel() == "Quota pool"
            )
            pools.selectItemAtIndex_(
                next(
                    i for i, g in enumerate(shell.usage.current_groups()) if g.key == "codex:spark"
                )
            )
            shell.usage.pool_changed(pools)
            assert shell.usage.current_group().key == "codex:spark"
            picker = next(
                v
                for v in shell.usage.controller.view().subviews()
                if isinstance(v, A.NSPopUpButton)
            )
            picker.selectItemAtIndex_(
                next(
                    i
                    for i, c in enumerate(shell.usage.current_provider().contexts)
                    if c.connection_id == "other"
                )
            )
            shell.usage.context_changed(picker)
            assert shell.usage.context_id == "other"
            assert (
                "10% used"
                in next(
                    c for c in shell.usage.current_provider().contexts if c.connection_id == "other"
                )
                .limits[0]
                .usage
            )
            shell.usage.focusables[0].performClick_(None)
            assert shell.usage.provider_id is None
        checks["native_light_dark_material_and_row_back_context_selection"] = True
        shell.usage.popover.setAppearance_(None)
        for integration, name, _ in PROVIDERS:
            shell.usage.detail(integration)
            assert_no_scroll_and_fit(shell.usage.controller.view())
            capture(shell.usage.controller.view(), name.replace(" ", "-").lower() + "-dark.png")
            if integration == "anthropic.claude":
                assert shell.usage.current_group().short.freshness.value == "undated"
            metrics = [
                v
                for v in shell.usage.controller.view().subviews()
                if isinstance(v, A.NSTextField) and v.stringValue().endswith("% used")
            ]
            assert all(
                v.attributedStringValue().size().width <= v.frame().size.width - 4 for v in metrics
            )
        checks["provider_specific_panels_no_scroll"] = True
        original_dashboard = shell.usage.dashboard
        demo = replace(
            original_dashboard.providers[1],
            integration_id="example.demo",
            name="Demo fixtures",
            contexts=(),
            selected_id=None,
        )
        shell.usage.update(
            replace(original_dashboard, providers=original_dashboard.providers + (demo,))
        )
        shell.usage.overview()
        assert_no_scroll_and_fit(shell.usage.controller.view())
        next(v for v in shell.usage.focusables if v.title() == "Demo fixtures…").performClick_(None)
        assert shell.usage.provider_id == "example.demo"
        assert_no_scroll_and_fit(shell.usage.controller.view())
        shell.usage.update(original_dashboard)
        checks["demo_entry_fits_without_changing_the_four_provider_rows"] = True
        shell.usage.detail("openai.codex")
        assert shell.usage.current_group().key == "codex:spark"
        controller.clock = SimpleNamespace(now=lambda: now + timedelta(minutes=1))
        shell.tick()
        assert shell.usage.current_group().key == "codex:spark"
        checks["pool_selection_preserved_after_update"] = True
        original_size = shell.usage.available_size
        shell.usage.available_size = lambda: (400, 420)
        shell.usage.render(force=True)
        assert_no_scroll_and_fit(shell.usage.controller.view())
        assert shell.usage.geometry.compact and shell.usage.geometry.height == 260
        capture(shell.usage.controller.view(), "details-compact.png")
        shell.usage.available_size = original_size
        shell.usage.render(force=True)
        checks["fixed_geometry_independent_of_screen_and_navigation"] = True
        stale_picker = next(
            v
            for v in shell.usage.controller.view().subviews()
            if isinstance(v, A.NSPopUpButton) and v.accessibilityLabel() == "Provider context"
        )
        stale_picker.selectItemAtIndex_(
            next(
                i
                for i in range(stale_picker.numberOfItems())
                if stale_picker.itemAtIndex_(i).representedObject() == "other"
            )
        )
        original_state = states[1]
        long_pool = "scope-name-" * 16
        long_observation = replace(
            original_state.snapshot.observations[0],
            bucket_key=f"codex:{long_pool}:primary",
            label=("Long quota label " * 16)[:240],
        )
        states[1] = replace(
            original_state,
            connection=replace(
                original_state.connection, label=("AAA " + "Long context " * 10)[:120]
            ),
            snapshot=replace(
                original_state.snapshot,
                observations=original_state.snapshot.observations + (long_observation,),
            ),
        )
        shell.usage.group_keys["qa-1"] = f"codex:{long_pool}"
        shell.tick()
        assert_no_scroll_and_fit(shell.usage.controller.view())
        long_picker = next(
            v
            for v in shell.usage.controller.view().subviews()
            if isinstance(v, A.NSPopUpButton) and v.accessibilityLabel() == "Quota pool"
        )
        assert long_picker.toolTip() == long_pool
        capture(shell.usage.controller.view(), "details-long-labels.png")
        shell.usage.context_changed(stale_picker)
        assert shell.usage.context_id == "other"
        states[1] = original_state
        shell.usage.context_id = None
        shell.usage.group_keys["qa-1"] = "codex:spark"
        shell.tick()
        checks["long_labels_and_stale_selector_identity"] = True
        refresh = next(
            v
            for v in shell.usage.focusables
            if isinstance(v, A.NSButton) and v.title() == "Refresh"
        )
        refresh.performClick_(None)
        assert controller.requests[-1] == ("qa-1", "manual")
        shell.usage.open_dashboard()
        assert controller.opened == []  # Demo guard, no external page.
        controller.config.demo = False
        shell.usage.render(force=True)
        dashboard = next(
            v
            for v in shell.usage.focusables
            if isinstance(v, A.NSButton) and v.title() == "Dashboard"
        )
        dashboard.performClick_(None)
        assert controller.opened == [("qa-1", "dashboard")]
        controller.config.demo = True
        checks["quick_actions_selected_context_and_demo_guard"] = True
        # Send native key events through NSWindow, exercising the actual responder chain.
        shell.usage.overview()
        window = shell.usage.controller.view().window()
        window.makeFirstResponder_(shell.usage.focusables[0])

        def key(code, chars):
            event = A.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                A.NSEventTypeKeyDown,
                (0, 0),
                0,
                0,
                window.windowNumber(),
                None,
                chars,
                chars,
                False,
                code,
            )
            window.sendEvent_(event)

        key(48, "\t")
        assert window.firstResponder() == shell.usage.focusables[1]
        key(36, "\r")
        assert shell.usage.provider_id == "openai.codex"
        key(53, "\x1b")
        A.NSRunLoop.mainRunLoop().runUntilDate_(A.NSDate.dateWithTimeIntervalSinceNow_(0.1))
        assert not shell.usage.popover.isShown()
        checks["native_tab_return_escape"] = True
        shell.toggle()
        assert shell.usage.provider_id is None
        checks["escape_close_and_reopen_overview"] = True
        outside = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(200, 200, 160, 100),
            A.NSWindowStyleMaskTitled,
            A.NSBackingStoreBuffered,
            False,
        )
        outside.setReleasedWhenClosed_(False)
        outside.setTitle_("CredUsage isolated outside-click test")
        outside.makeKeyAndOrderFront_(None)
        event = A.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
            A.NSEventTypeLeftMouseDown, (40, 40), 0, 0, outside.windowNumber(), None, 1, 1, 1
        )
        shell.application.postEvent_atStart_(event, False)
        deadline = A.NSDate.dateWithTimeIntervalSinceNow_(0.2)
        while deadline.timeIntervalSinceNow() > 0:
            pending = shell.application.nextEventMatchingMask_untilDate_inMode_dequeue_(
                A.NSEventMaskAny, deadline, A.NSDefaultRunLoopMode, True
            )
            if pending:
                shell.application.sendEvent_(pending)
        assert not shell.usage.popover.isShown()
        outside.close()
        checks["outside_click_dismissal"] = True
        shell.toggle()
        checks["row_accessibility"] = all(
            row.accessibilityLabel() for row in shell.usage.focusables[:4]
        )
        assert checks["row_accessibility"]
        real = Controller(Path(".qa/popover-settings").resolve(), demo=True)
        real.pin(real.config.connections[0].connection_id, "not-yet-reported")
        shell.usage.close()
        settings = show_settings(real)
        assert settings.window.isVisible() and settings.pin_picker.numberOfItems() > 0
        settings._save()
        assert settings.status.stringValue().startswith("Saved.")
        assert real.config.pinned_bucket_key == "not-yet-reported"
        settings._refresh()
        capture(settings.window.contentView(), "settings.png")
        settings.window.close()
        real.close()
        checks["separate_settings_save_and_refresh"] = True
        checks["refresh_triggered_on_open"] = any(
            reason == "menu" for _, reason in controller.requests
        )
        checks["errors"] = []
    except BaseException as error:
        import traceback

        traceback.print_exc()
        checks["errors"] = [repr(error)]
    finally:
        (qa / "native-smoke.json").write_text(json.dumps(checks, indent=2))
        print(json.dumps(checks), flush=True)
        shell.shutdown()
        shell.application.stop_(None)
        wake = A.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            A.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0
        )
        shell.application.postEvent_atStart_(wake, True)


# Retain the timer block and let WindowServer finish status item registration.
timer = A.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, False, inspect)
shell.application.run()
raise SystemExit(bool(checks.get("errors", ["test did not complete"])))
