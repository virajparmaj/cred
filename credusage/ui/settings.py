"""Scrollable native settings. Cocoa is loaded only when the window is shown."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from credusage.domain.models import Connection
from credusage.formatting import percentage

_native = None
_instance = None
WINDOW_WIDTH_SCALE = 0.8


def _load_native():
    global _native
    if _native is not None:
        return _native
    import AppKit as A
    import objc

    class CredUsageSettingsDelegate(A.NSObject):
        owner = objc.ivar()

        def performAction_(self, sender):
            self.owner._callbacks[int(sender.tag())]()

        def windowWillClose_(self, notification):
            global _instance
            _instance = None

    _native = (A, CredUsageSettingsDelegate)
    return _native


def show_settings(controller, on_change=None):
    global _instance
    if _instance is None:
        _instance = SettingsWindow(controller, on_change)
    _instance.window.makeKeyAndOrderFront_(None)
    A, _ = _load_native()
    A.NSApp.activateIgnoringOtherApps_(True)
    return _instance


class SettingsWindow:
    def __init__(self, controller, on_change=None):
        self.controller = controller
        self.on_change = on_change or (lambda: None)
        self.A, Delegate = _load_native()
        self.delegate = Delegate.alloc().init()
        self.delegate.owner = self
        self._callbacks = {}
        self.controls = {}
        self._build()

    def _rect(self, x, y, width, height):
        return self.A.NSMakeRect(x * WINDOW_WIDTH_SCALE, y, width * WINDOW_WIDTH_SCALE, height)

    def label(self, parent, text, y, *, x=24, width=674, height=20, bold=False):
        A = self.A
        field = A.NSTextField.alloc().initWithFrame_(self._rect(x, y, width, height))
        field.setStringValue_(str(text))
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(True)
        field.setFont_((A.NSFont.boldSystemFontOfSize_ if bold else A.NSFont.systemFontOfSize_)(12))
        field.cell().setWraps_(True)
        parent.addSubview_(field)
        return field

    def field(self, parent, value, y, *, x=202, width=490):
        field = self.A.NSTextField.alloc().initWithFrame_(self._rect(x, y, width, 24))
        field.setStringValue_(str(value if value is not None else ""))
        parent.addSubview_(field)
        return field

    def check(self, parent, text, value, y, *, x=24, width=670):
        A = self.A
        button = A.NSButton.alloc().initWithFrame_(self._rect(x, y, width, 24))
        button.setButtonType_(A.NSSwitchButton)
        button.setTitle_(text)
        button.setState_(1 if value else 0)
        parent.addSubview_(button)
        return button

    def button(self, parent, text, callback, x, y, width=130):
        button = self.A.NSButton.alloc().initWithFrame_(self._rect(x, y, width, 30))
        button.setTitle_(text)
        button.setBezelStyle_(1)
        button.setControlSize_(self.A.NSControlSizeSmall)
        button.setFont_(self.A.NSFont.systemFontOfSize_(12))
        tag = len(self._callbacks) + 1
        self._callbacks[tag] = callback
        button.setTag_(tag)
        button.setTarget_(self.delegate)
        button.setAction_("performAction:")
        parent.addSubview_(button)
        return button

    def _build(self):
        A = self.A
        config = self.controller.config
        descriptors = self.controller.registry.descriptors()
        by_id = {descriptor.integration_id: descriptor for descriptor in descriptors}
        connections = sorted(
            config.connections, key=lambda item: (item.preferences.order, item.label.casefold())
        )
        style = (
            A.NSWindowStyleMaskTitled
            | A.NSWindowStyleMaskClosable
            | A.NSWindowStyleMaskMiniaturizable
            | A.NSWindowStyleMaskResizable
        )
        self.window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            self._rect(0, 0, 746, 690), style, A.NSBackingStoreBuffered, False
        )
        self.window.setTitle_("CredUsage Settings" + (" · Demo" if config.demo else ""))
        self.window.setMinSize_((746 * WINDOW_WIDTH_SCALE, 500))
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self.delegate)
        content = self.window.contentView()
        scroll = A.NSScrollView.alloc().initWithFrame_(self._rect(0, 76, 746, 614))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
        height = 580 + len(connections) * 354
        document = A.NSView.alloc().initWithFrame_(self._rect(0, 0, 722, height))
        scroll.setDocumentView_(document)
        content.addSubview_(scroll)
        self.document = document
        y = height - 36
        self.label(document, "Connections and passive collection", y, bold=True)
        y -= 38
        self.label(document, "Add local context", y, width=170)
        self.integration_picker = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            self._rect(202, y - 3, 310, 28), False
        )
        self._integration_ids = [
            descriptor.integration_id
            for descriptor in descriptors
            if (descriptor.integration_id == "example.demo") == config.demo
        ]
        self.integration_picker.addItemsWithTitles_(
            [by_id[key].display_name for key in self._integration_ids]
        )
        document.addSubview_(self.integration_picker)
        self.button(document, "Add", self._add, 540, y - 3, 100)
        y -= 48
        self.label(
            document,
            "Local contexts use vendor-owned sign-ins. Adding a context does not switch vendor accounts.",
            y,
            height=36,
        )
        y -= 48
        for connection in connections:
            descriptor = by_id.get(connection.integration_id)
            title = descriptor.display_name if descriptor else connection.integration_id
            self.label(document, f"{title} · {connection.label}", y, height=34, bold=True)
            y -= 34
            controls = {"connection": connection}
            controls["enabled"] = self.check(
                document, "Enable this connection", connection.enabled, y
            )
            controls["experimental"] = self.check(
                document,
                "Allow undocumented source (only where implemented)",
                connection.preferences.experimental,
                y,
                x=248,
                width=450,
            )
            y -= 34
            for key, label, value in (
                ("label", "Display label", connection.label),
                ("refresh", "Refresh seconds", connection.preferences.refresh_seconds),
                ("order", "Display order", connection.preferences.order),
                ("executable", "CLI executable path", connection.preferences.executable),
                ("source_path", "Telemetry source path", connection.preferences.source_path),
            ):
                self.label(document, label, y + 2, width=174)
                controls[key] = self.field(document, value, y)
                if key == "refresh":
                    controls[key].setToolTip_("Refresh interval in seconds (minimum 300).")
                y -= 31
            controls["notifications"] = self.check(
                document,
                "Allow verified, fresh quota notifications for this connection",
                connection.preferences.notifications,
                y,
            )
            y -= 31
            evidence = (
                f"{descriptor.source_policy.value.replace('_', ' ')} · {descriptor.validation.value.replace('_', ' ')}"
                if descriptor
                else "Source unknown"
            )
            self.label(document, evidence, y, height=30)
            y -= 31
            actions = self.controller.actions(connection.connection_id)
            if actions:
                self.button(
                    document,
                    "Source setup / help",
                    lambda cid=connection.connection_id: self._source_help(cid),
                    24,
                    y - 3,
                    190,
                )
            self.button(
                document,
                "Disconnect",
                lambda cid=connection.connection_id: self._disconnect(cid),
                230,
                y - 3,
                130,
            )
            self.button(
                document,
                "Refresh",
                lambda cid=connection.connection_id: self._refresh(cid),
                390,
                y - 3,
                100,
            )
            self.controls[connection.connection_id] = controls
            y -= 65
        self.label(document, "Preferences", y, bold=True)
        y -= 32
        self.button(document, "Refresh all", self._refresh, 24, y - 3, 130)
        self.button(document, "About CredUsage", self._about, 178, y - 3, 160)
        y -= 36
        self.label(document, "Preferred context", y + 3, width=174)
        self.pin_picker = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            self._rect(202, y - 3, 490, 28), False
        )
        self._pin_choices = [(None, None)]
        pin_titles = ["Automatic · first enabled context"]
        for state in self.controller.states():
            descriptor = by_id.get(state.connection.integration_id)
            name = descriptor.display_name if descriptor else state.connection.integration_id
            self._pin_choices.append((state.connection.connection_id, None))
            pin_titles.append(f"{name} · {state.connection.label}")
            for observation in state.snapshot.observations if state.snapshot else ():
                self._pin_choices.append((state.connection.connection_id, observation.bucket_key))
                pin_titles.append(f"{name} · {state.connection.label} · {observation.label}")
        selected = (config.pinned_connection_id, config.pinned_bucket_key)
        if selected[0] is not None and selected not in self._pin_choices:
            self._pin_choices.append(selected)
            pin_titles.append("Saved preferred context · awaiting source reading")
        self.pin_picker.addItemsWithTitles_(pin_titles)
        if selected in self._pin_choices:
            self.pin_picker.selectItemAtIndex_(self._pin_choices.index(selected))
        document.addSubview_(self.pin_picker)
        y -= 36
        self.notifications = self.check(
            document,
            "Enable quota notifications (verified fresh observations only)",
            config.notifications,
            y,
        )
        y -= 32
        self.label(document, "Thresholds (% used)", y + 3, width=174)
        self._threshold_values = config.thresholds
        self._threshold_display = ", ".join(percentage(value) for value in config.thresholds)
        self.thresholds = self.field(document, self._threshold_display, y)
        y -= 35
        self.login = self.check(
            document,
            "Launch CredUsage at login (applies at next sign-in)",
            config.launch_at_login,
            y,
        )
        if config.demo:
            self.login.setEnabled_(False)
        y -= 42
        self.label(
            document,
            "No inference or automatic sign-in. Refresh respects source cooldowns. Notifications use five-point hysteresis.",
            y,
            height=40,
        )
        self.status = self.label(
            content, "Changes apply when you click Save.", 44, width=698, height=26
        )
        self.button(content, "Logs", self._logs, 20, 8, 85)
        self.button(content, "Diagnostics…", self._diagnostics, 110, 8, 125)
        self.button(content, "Reset preferences", self._reset, 242, 8, 160)
        self.button(content, "Cancel", lambda: self.window.close(), 500, 8, 105)
        self.button(content, "Save", self._save, 615, 8, 105)
        self.window.center()
        document.scrollPoint_((0, height))

    def _message(self, message):
        self.status.setStringValue_(str(message))

    def _save(self):
        try:
            thresholds = tuple(
                sorted(
                    {
                        Decimal(value.strip())
                        for value in self.thresholds.stringValue().split(",")
                        if value.strip()
                    }
                )
            )
            if self.thresholds.stringValue() == self._threshold_display:
                thresholds = self._threshold_values
            if not thresholds or any(
                not value.is_finite() or value <= 0 or value > 100 for value in thresholds
            ):
                raise ValueError("Thresholds must be percentages greater than 0 and at most 100.")
            updated = []
            for controls in self.controls.values():
                connection = controls["connection"]
                label = controls["label"].stringValue().strip()
                if not label or len(label) > 120:
                    raise ValueError("Connection labels must contain 1–120 characters.")
                refresh = int(controls["refresh"].stringValue())
                if not 300 <= refresh <= 86400:
                    raise ValueError("Refresh intervals must be between 300 and 86400 seconds.")
                order = int(controls["order"].stringValue())
                executable = controls["executable"].stringValue().strip() or None
                source_path = controls["source_path"].stringValue().strip() or None
                if executable and not Path(executable).is_absolute():
                    raise ValueError("CLI executable paths must be absolute.")
                if source_path and not Path(source_path).is_absolute():
                    raise ValueError("Telemetry source paths must be absolute.")
                preferences = replace(
                    connection.preferences,
                    refresh_seconds=refresh,
                    experimental=bool(controls["experimental"].state()),
                    notifications=bool(controls["notifications"].state()),
                    order=order,
                    executable=executable,
                    source_path=source_path,
                )
                updated.append(
                    replace(
                        connection,
                        label=label,
                        enabled=bool(controls["enabled"].state()),
                        preferences=preferences,
                    )
                )
            for connection in updated:
                self.controller.update_connection(connection)
            self.controller.set_preferences(
                thresholds=thresholds,
                notifications=bool(self.notifications.state()),
                launch_at_login=(
                    bool(self.login.state()) if not self.controller.config.demo else False
                ),
            )
            self.controller.pin(*self._pin_choices[int(self.pin_picker.indexOfSelectedItem())])
            self.on_change()
            self._message("Saved. Source changes require a fresh observation.")
        except (ValueError, InvalidOperation, OSError) as error:
            self._message(str(error))

    def _add(self):
        index = int(self.integration_picker.indexOfSelectedItem())
        if not 0 <= index < len(self._integration_ids):
            return
        connection = Connection(
            uuid4().hex, self._integration_ids[index], label="New local context"
        )
        self.controller.update_connection(connection)
        self._reopen()

    def _disconnect(self, connection_id):
        self.controller.disconnect(connection_id)
        self._reopen()

    def _source_help(self, connection_id):
        A = self.A
        actions = self.controller.actions(connection_id)
        if not actions:
            return
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_("Source setup and provider actions")
        alert.setInformativeText_(
            "Select an action. Login remains owned by the vendor tool. Claude telemetry setup is documented in the included README and CLI help."
        )
        for action in actions:
            alert.addButtonWithTitle_(action.label)
        alert.addButtonWithTitle_("Cancel")
        choice = int(alert.runModal()) - 1000
        if 0 <= choice < len(actions):
            self._message(self.controller.run_action(connection_id, actions[choice]))

    def _logs(self):
        from credusage.infrastructure.macos_actions import ActionRunner

        runner = ActionRunner(self.controller.root, demo=self.controller.config.demo)
        try:
            self._message(runner.open_logs())
        finally:
            runner.close()

    def _refresh(self, connection_id=None):
        self.controller.refresh(connection_id, reason="manual")
        self._message("Refresh requested. Source cooldowns still apply.")
        self.on_change()

    def _about(self):
        from credusage import __version__

        alert = self.A.NSAlert.alloc().init()
        alert.setMessageText_("CredUsage")
        alert.setInformativeText_(
            f"Version {__version__}\nPassive, local AI allowance monitoring.\n"
            "Provider readings retain their source, freshness, and account-verification status.\n"
            "Built with AppKit and PyObjC. See THIRD_PARTY_NOTICES.md for attribution."
        )
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def _diagnostics(self):
        panel = self.A.NSSavePanel.savePanel()
        panel.setNameFieldStringValue_("credusage-diagnostics.json")
        if panel.runModal() == self.A.NSModalResponseOK:
            try:
                self.controller.export_diagnostics(Path(panel.URL().path()))
                self._message("Exported redacted diagnostics.")
            except OSError:
                self._message("Could not write diagnostics at that location.")

    def _reset(self):
        alert = self.A.NSAlert.alloc().init()
        alert.setMessageText_("Reset CredUsage preferences?")
        alert.setInformativeText_(
            "This resets connection settings. Vendor apps and their credentials are unchanged."
        )
        alert.addButtonWithTitle_("Reset preferences")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() == 1000:
            self.controller.reset_preferences()
            self._reopen()

    def _reopen(self):
        global _instance
        self.window.close()
        _instance = None
        self.on_change()
        show_settings(self.controller, self.on_change)
