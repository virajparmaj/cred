"""Native, accessible AppKit views. Cocoa is loaded only by the GUI entry point."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from credusage.domain.models import Freshness
from credusage.ui.dashboard import overview_status
from credusage.ui.detail_layout import FOOTER_HEIGHT, OVERVIEW_HEIGHT, WINDOW_WIDTH
from credusage.ui.presentation import countdown, percentage

ASSETS = Path(__file__).resolve().parent.parent / "assets"
_native = None


def native_classes():
    global _native
    if _native is not None:
        return _native
    import AppKit as A
    import objc

    class CredUsageFlippedView(A.NSView):
        def isFlipped(self):
            return True

    class CredUsagePopoverController(A.NSViewController):
        owner = objc.ivar()

        def cancelOperation_(self, sender):
            self.owner.close()

        def moveLeft_(self, sender):
            self.owner.overview()

    class CredUsageActionButton(A.NSButton):
        owner = objc.ivar()
        filled = objc.ivar(type=objc._C_BOOL)

        def drawRect_(self, rect):
            if self.filled:
                alpha = 0.16 if self.isHighlighted() else 0.10 if self.isEnabled() else 0.04
                A.NSColor.labelColor().colorWithAlphaComponent_(alpha).setFill()
                A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    self.bounds(), 6, 6
                ).fill()
            objc.super(CredUsageActionButton, self).drawRect_(rect)

        def acceptsFirstResponder(self):
            return True

        def keyDown_(self, event):
            self.owner.keyboard_navigation = True
            self.setNeedsDisplay_(True)
            code = event.keyCode()
            if code in (36, 49, 76):
                self.performClick_(self)
            elif code == 53:
                self.owner.close()
            elif code in (48, 125, 126):
                backwards = code == 126 or bool(event.modifierFlags() & A.NSEventModifierFlagShift)
                self.owner.move_focus(self, backwards)
            elif code == 123:
                self.owner.overview()
            else:
                objc.super(CredUsageActionButton, self).keyDown_(event)

    class CredUsageContextPicker(A.NSPopUpButton):
        owner = objc.ivar()

        def acceptsFirstResponder(self):
            return True

        def keyDown_(self, event):
            if event.keyCode() == 48:
                self.owner.move_focus(
                    self, bool(event.modifierFlags() & A.NSEventModifierFlagShift)
                )
            elif event.keyCode() == 53:
                self.owner.close()
            else:
                objc.super(CredUsageContextPicker, self).keyDown_(event)

    class CredUsageRowButton(CredUsageActionButton):
        hovered = objc.ivar(type=objc._C_BOOL)
        tracking = objc.ivar()

        def isFlipped(self):
            return True

        def acceptsFirstResponder(self):
            return True

        def hitTest_(self, point):
            return self if A.NSPointInRect(point, self.frame()) else None

        def updateTrackingAreas(self):
            if self.tracking is not None:
                self.removeTrackingArea_(self.tracking)
            options = (
                A.NSTrackingMouseEnteredAndExited
                | A.NSTrackingActiveInKeyWindow
                | A.NSTrackingInVisibleRect
            )
            self.tracking = A.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), options, self, None
            )
            self.addTrackingArea_(self.tracking)
            objc.super(CredUsageRowButton, self).updateTrackingAreas()

        def mouseEntered_(self, event):
            self.owner.keyboard_navigation = False
            focused = self.window().firstResponder() if self.window() else None
            if isinstance(focused, CredUsageRowButton):
                focused.setNeedsDisplay_(True)
            self.hovered = True
            self.setNeedsDisplay_(True)

        def mouseExited_(self, event):
            self.hovered = False
            self.setNeedsDisplay_(True)

        def becomeFirstResponder(self):
            self.setNeedsDisplay_(True)
            return True

        def resignFirstResponder(self):
            self.setNeedsDisplay_(True)
            return True

        def drawRect_(self, rect):
            if self.hovered or self.isHighlighted():
                A.NSColor.labelColor().colorWithAlphaComponent_(0.055).setFill()
                A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    A.NSInsetRect(self.bounds(), 0, 1), 6, 6
                ).fill()
            focused = self.window() and self.window().firstResponder() == self
            if focused and self.owner.keyboard_navigation:
                # A small neutral keyboard cue avoids a persistent selected service card.
                A.NSColor.secondaryLabelColor().setFill()
                A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    A.NSMakeRect(1, 18, 2, 20), 1, 1
                ).fill()

    class CredUsageProgressView(A.NSView):
        fraction = objc.ivar(type=objc._C_DBL)
        accent = objc.ivar()
        historical = objc.ivar(type=objc._C_BOOL)

        def drawRect_(self, rect):
            A.NSColor.labelColor().colorWithAlphaComponent_(0.10).setFill()
            A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 2, 2).fill()
            if self.fraction > 0:
                color = (
                    self.accent.colorWithAlphaComponent_(0.45) if self.historical else self.accent
                )
                color.setFill()
                bounds = self.bounds()
                bounds.size.width *= self.fraction
                A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 2, 2).fill()

    _native = (
        A,
        CredUsageFlippedView,
        CredUsagePopoverController,
        CredUsageActionButton,
        CredUsageRowButton,
        CredUsageProgressView,
        CredUsageContextPicker,
    )
    return _native


class UsagePopover:
    def __init__(self, shell):
        self.shell = shell
        (
            self.A,
            self.View,
            Controller,
            self.Action,
            self.Row,
            self.Progress,
            self.Picker,
        ) = native_classes()
        self.controller = Controller.alloc().init()
        self.controller.owner = self
        self.popover = self.A.NSPopover.alloc().init()
        self.popover.setBehavior_(self.A.NSPopoverBehaviorTransient)
        self.popover.setAnimates_(True)
        self.popover.setContentViewController_(self.controller)
        self.popover.setContentSize_((WINDOW_WIDTH, OVERVIEW_HEIGHT))
        self.popover.setDelegate_(shell.delegate)
        self.provider_id = None
        self.context_id = None
        self.dashboard = None
        self._last_render = None
        self.callbacks = {}
        self.focusables = []
        self.group_keys = {}
        self.action_message = ""
        self.geometry = None
        self.keyboard_navigation = False

    def is_dark(self):
        appearance = self.popover.appearance() or self.controller.view().effectiveAppearance()
        return (
            appearance.bestMatchFromAppearancesWithNames_(
                [self.A.NSAppearanceNameAqua, self.A.NSAppearanceNameDarkAqua]
            )
            == self.A.NSAppearanceNameDarkAqua
        )

    def close(self):
        self.popover.close()

    def overview(self):
        self.provider_id = self.context_id = None
        self.render(force=True)

    def detail(self, provider_id):
        self.provider_id = provider_id
        self.context_id = None
        self.action_message = ""
        self.render(force=True)

    def context_changed(self, sender):
        provider = self.current_provider()
        selected = sender.selectedItem().representedObject() if sender.selectedItem() else None
        if provider and any(context.connection_id == selected for context in provider.contexts):
            self.context_id = selected
            self.action_message = ""
            self.render(force=True)

    def current_provider(self):
        return next(
            (p for p in self.dashboard.providers if p.integration_id == self.provider_id), None
        )

    def update(self, dashboard):
        self.dashboard = dashboard
        if self.popover.isShown():
            self.render()

    def rect(self, x, y, width, height):
        return self.A.NSMakeRect(x, y, width, height)

    def text(
        self,
        parent,
        value,
        x,
        y,
        width,
        *,
        size=12,
        height=20,
        bold=False,
        weight=None,
        monospaced=False,
        secondary=False,
        wrap=False,
        alignment=None,
    ):
        A = self.A
        field = A.NSTextField.labelWithString_(str(value))
        field.setFrame_(self.rect(x, y, width, height))
        weight = (
            weight
            if weight is not None
            else (A.NSFontWeightSemibold if bold else A.NSFontWeightRegular)
        )
        font = (
            A.NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight)
            if monospaced
            else A.NSFont.systemFontOfSize_weight_(size, weight)
        )
        field.setFont_(font)
        field.setTextColor_(
            A.NSColor.secondaryLabelColor() if secondary else A.NSColor.labelColor()
        )
        field.setToolTip_(str(value))
        field.setSelectable_(False)
        field.setAccessibilityElement_(not isinstance(parent, self.Row))
        field.cell().setWraps_(wrap)
        if not wrap:
            field.cell().setLineBreakMode_(A.NSLineBreakByTruncatingTail)
            field.setToolTip_(str(value))
        if alignment is not None:
            field.setAlignment_(alignment)
        parent.addSubview_(field)
        return field

    def button(self, parent, title, callback, x, y, width, height=24, *, symbol=None, filled=False):
        A = self.A
        button = self.Action.alloc().initWithFrame_(self.rect(x, y, width, height))
        button.setTitle_(title)
        button.setBordered_(False)
        button.setFont_(A.NSFont.systemFontOfSize_(13))
        button.setImageHugsTitle_(True)
        if symbol in ("chevron.left", "gearshape"):
            button.setAlignment_(A.NSTextAlignmentLeft)
        button.setFocusRingType_(A.NSFocusRingTypeExterior)
        button.setContentTintColor_(A.NSColor.labelColor())
        button.filled = filled
        if symbol:
            image = A.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
            image = image.imageWithSymbolConfiguration_(
                A.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
                    13, A.NSFontWeightRegular
                )
            )
            button.setImage_(image)
            button.setImagePosition_(
                A.NSImageRight if symbol in ("arrow.up.right", "power") else A.NSImageLeft
            )
        self.bind(button, callback)
        parent.addSubview_(button)
        return button

    def bind(self, button, callback):
        tag = len(self.callbacks) + 1
        self.callbacks[tag] = callback
        button.setTag_(tag)
        button.owner = self
        button.setTarget_(self.shell.delegate)
        button.setAction_("performAction:")
        self.focusables.append(button)

    def move_focus(self, current, backwards=False):
        enabled = [view for view in self.focusables if view.isEnabled()]
        index = enabled.index(current)
        target = enabled[(index + (-1 if backwards else 1)) % len(enabled)]
        current.window().makeFirstResponder_(target)

    def accent(self, integration):
        # Resolve while the requested appearance is current, before the new root is attached.
        # Retaining a dynamic NSColor here can otherwise carry a dark fill into light mode.
        appearance = self.popover.appearance() or self.controller.view().effectiveAppearance()
        colors = []
        appearance.performAsCurrentDrawingAppearance_(
            lambda: colors.append(
                self.A.NSColor.labelColor()
                .colorUsingColorSpace_(self.A.NSColorSpace.deviceRGBColorSpace())
                .colorWithAlphaComponent_(0.8)
            )
        )
        return colors[0]

    def logo(self, parent, provider, x, y, size=32):
        name = (
            "ProviderCodexDark.png"
            if provider.integration_id == "openai.codex" and self.is_dark()
            else provider.icon
        )
        image = self.A.NSImage.alloc().initWithContentsOfFile_(str(ASSETS / name))
        view = self.A.NSImageView.alloc().initWithFrame_(self.rect(x, y, size, size))
        if provider.integration_id in ("anthropic.claude", "example.demo"):
            image.setTemplate_(True)
            view.setContentTintColor_(
                self.A.NSColor.colorWithSRGBRed_green_blue_alpha_(0.85, 0.47, 0.34, 1)
                if provider.integration_id == "anthropic.claude"
                else self.accent(provider.integration_id)
            )
        view.setImage_(image)
        view.setImageScaling_(self.A.NSImageScaleProportionallyUpOrDown)
        view.setAccessibilityElement_(False)
        parent.addSubview_(view)

    def progress(self, parent, percent, integration, x, y, width, historical=False):
        view = self.Progress.alloc().initWithFrame_(self.rect(x, y, width, 4))
        view.fraction = (
            float(max(Decimal(0), min(Decimal(100), percent)) / 100) if percent is not None else 0
        )
        view.accent = self.accent(integration)
        view.historical = historical
        view.setAccessibilityElement_(True)
        view.setAccessibilityRole_(self.A.NSAccessibilityProgressIndicatorRole)
        view.setAccessibilityLabel_("Usage" + (" · historical reading" if historical else ""))
        view.setAccessibilityValue_(
            f"{percentage(percent)} percent used" if percent is not None else "Usage unavailable"
        )
        parent.addSubview_(view)

    def separator(self, parent, y, *, x=16, width=WINDOW_WIDTH - 32):
        view = self.A.NSBox.alloc().initWithFrame_(self.rect(x, y, width, 1))
        view.setBoxType_(self.A.NSBoxCustom)
        view.setTitlePosition_(self.A.NSNoTitle)
        view.setBorderType_(self.A.NSNoBorder)
        view.setFillColor_(self.A.NSColor.separatorColor())
        parent.addSubview_(view)

    def row_subtitle(self, context):
        limit = context.primary if context and context.enabled else None
        if limit is None:
            return overview_status(context)
        from credusage.ui.compact_details import duration_name

        parts = [duration_name(limit)]
        if limit.freshness != Freshness.FRESH:
            parts.append(limit.freshness.value.capitalize())
            reset = countdown(limit.resets_at, self.shell.controller.clock.now())
            parts.append(f"Reset {reset}" if reset else limit.reset_label)
        else:
            parts.append(limit.reset_label)
        if context.loading:
            parts.append("Refreshing…")
        return " · ".join(parts)

    def row(self, parent, provider, y):
        row = self.Row.alloc().initWithFrame_(self.rect(8, y, WINDOW_WIDTH - 16, 56))
        row.setTitle_("")
        row.setBordered_(False)
        row.setFocusRingType_(self.A.NSFocusRingTypeNone)
        self.bind(row, lambda: self.detail(provider.integration_id))
        parent.addSubview_(row)
        context = provider.selected
        limit = context.primary if context and context.enabled else None
        percent = limit.percent_used if limit else None
        usage = (
            f"{percentage(percent)}%"
            if percent is not None
            else limit.usage if limit else "Unavailable"
        )
        subtitle = overview_status(context)
        self.logo(row, provider, 8, 6, 44)
        self.text(
            row, provider.name, 60, 3, 88, size=13, weight=self.A.NSFontWeightMedium, height=19
        )
        self.text(
            row,
            usage,
            148,
            3,
            84,
            size=13 if percent is not None else 12,
            height=19,
            secondary=percent is None,
            monospaced=percent is not None,
            alignment=self.A.NSTextAlignmentRight,
        )
        if percent is not None:
            self.progress(
                row,
                percent,
                provider.integration_id,
                60,
                25,
                172,
                limit.freshness != Freshness.FRESH,
            )
        field = self.text(
            row,
            self.row_subtitle(context),
            60,
            34 if percent is not None else 27,
            172,
            size=11,
            height=16,
            secondary=True,
        )
        field.setToolTip_(subtitle)
        row.setAccessibilityLabel_(f"{provider.name}, {usage}, {subtitle}")
        row.setAccessibilityHelp_("Open detailed usage and reset times")
        row.setToolTip_(f"{provider.name} · {subtitle}")

    def overview_updated_at(self):
        """A conservative overview age represents the oldest displayed numeric reading."""
        timestamps = []
        for provider in self.dashboard.providers:
            if provider.integration_id == "example.demo":
                continue
            context = provider.selected
            if (
                context
                and context.enabled
                and context.primary
                and context.primary.percent_used is not None
            ):
                if context.last_success_at:
                    timestamps.append(context.last_success_at)
        return min(timestamps, default=None)

    def updated_label(self, parent, checked, *, relative=False):
        from credusage.ui.presentation import timestamp

        value = "No readings"
        if checked:
            age = max(0, (self.shell.controller.clock.now() - checked).total_seconds())
            elapsed = (
                "just now"
                if age < 60
                else f"{int(age // 60)} min ago" if age < 3600 else f"{int(age // 3600)}h ago"
            )
            value = (
                ("Demo · " if self.dashboard.demo else "Updated ") + elapsed
                if relative
                else f"Updated {checked.astimezone():%H:%M}"
            )
        field = self.text(
            parent,
            value,
            128,
            13,
            112,
            size=11,
            height=16,
            secondary=True,
            alignment=self.A.NSTextAlignmentRight,
        )
        field.setToolTip_(
            (
                "Oldest successful update among displayed numeric readings: "
                if relative
                else "Source update: "
            )
            + timestamp(checked)
        )
        return field

    def render_overview(self, root):
        title = "CredUsage"
        self.text(root, title, 16, 12, 108, size=13, height=19, bold=True)
        self.updated_label(root, self.overview_updated_at(), relative=True)
        self.separator(root, 40, x=0, width=WINDOW_WIDTH)
        providers = [p for p in self.dashboard.providers if p.integration_id != "example.demo"]
        for index, provider in enumerate(providers):
            self.row(root, provider, 48 + index * 56)
        footer_y = OVERVIEW_HEIGHT - FOOTER_HEIGHT
        if any(p.integration_id == "example.demo" for p in self.dashboard.providers):
            self.button(
                root, "Demo fixtures…", lambda: self.detail("example.demo"), 16, footer_y, 120
            )
            footer_y += 24
        return footer_y

    def current_context(self):
        provider = self.current_provider()
        if provider is None:
            return None
        return next(
            (c for c in provider.contexts if c.connection_id == self.context_id), provider.selected
        )

    def current_groups(self):
        from credusage.ui.detail_layout import quota_groups

        return quota_groups(self.provider_id, self.current_context())

    def current_group(self):
        from credusage.ui.detail_layout import selected_group

        context = self.current_context()
        if context is None:
            return None
        return selected_group(
            self.current_groups(), self.group_keys.get(context.connection_id), context.primary_key
        )

    def pool_changed(self, sender):
        context = self.current_context()
        groups = self.current_groups()
        selected = sender.selectedItem().representedObject() if sender.selectedItem() else None
        if context and any(group.key == selected for group in groups):
            self.group_keys[context.connection_id] = selected
            self.action_message = ""
            self.render(force=True)

    def refresh_detail(self):
        context = self.current_context()
        if context is None or not context.enabled or context.loading:
            return
        try:
            self.shell.controller.refresh(context.connection_id, reason="manual")
            self.action_message = "Refresh requested. Source cooldowns apply."
            self.shell.tick()
        except (ValueError, OSError, RuntimeError):
            self.action_message = "Refresh could not be requested. Check Settings."
        self.render(force=True)

    def open_dashboard(self):
        from credusage.ui.compact_details import dashboard_action

        context = self.current_context()
        action = dashboard_action(self.shell.controller, context)
        if action is None or self.shell.controller.config.demo:
            return
        try:
            self.action_message = self.shell.controller.run_action(context.connection_id, action)
        except (ValueError, OSError, RuntimeError):
            self.action_message = (
                "The provider page could not be opened. Try again or check Settings."
            )
        self.render(force=True)

    def render_details(self, root):
        from credusage.ui.compact_details import CompactDetails

        return CompactDetails(self, self.geometry).render(root)

    def surface(self, height):
        A = self.A
        root = self.View.alloc().initWithFrame_(self.rect(0, 0, WINDOW_WIDTH, height))
        material = A.NSVisualEffectView.alloc().initWithFrame_(root.bounds())
        material.setMaterial_(A.NSVisualEffectMaterialPopover)
        material.setBlendingMode_(A.NSVisualEffectBlendingModeWithinWindow)
        material.setState_(A.NSVisualEffectStateActive)
        material.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
        root.addSubview_(material)
        return root

    def render(self, *, force=False):
        if self.dashboard is None:
            return
        from credusage.ui.detail_layout import detail_geometry

        if self.provider_id and self.current_provider() is None:
            self.provider_id = None
        provider = self.current_provider() if self.provider_id else None
        selector_rows = (
            int(len(provider.contexts) > 1) + int(len(self.current_groups()) > 1) if provider else 0
        )
        self.geometry = (
            detail_geometry(selector_rows=selector_rows, feedback=bool(self.action_message))
            if provider
            else None
        )
        updated = self.overview_updated_at() if not provider else None
        overview_age = (
            int(max(0, (self.shell.controller.clock.now() - updated).total_seconds()) // 60)
            if updated
            else None
        )
        signature = (
            overview_age,
            self.dashboard,
            self.provider_id,
            self.context_id,
            self.is_dark(),
            tuple(self.group_keys.items()),
            self.action_message,
            self.geometry,
        )
        if not force and signature == self._last_render:
            return
        old_focus = (
            self.controller.view().window().firstResponder()
            if self.controller.view().window()
            else None
        )
        focus_index = next((i for i, view in enumerate(self.focusables) if view == old_focus), None)
        self.callbacks = {}
        self.focusables = []
        width = WINDOW_WIDTH
        height = (
            self.geometry.height
            if self.geometry
            else OVERVIEW_HEIGHT
            + (
                24
                if any(p.integration_id == "example.demo" for p in self.dashboard.providers)
                else 0
            )
        )
        root = self.surface(height)
        footer_y = self.render_details(root) if self.provider_id else self.render_overview(root)
        self.separator(root, footer_y, x=0, width=width)
        for name, symbol, callback, x in (
            ("Settings", "gearshape", self.shell.settings, 12),
            ("Quit", "power", self.shell.quit, width - 44),
        ):
            button = self.button(root, "", callback, x, footer_y + 4, 32, 32, symbol=symbol)
            button.setImagePosition_(self.A.NSImageOnly)
            button.setAlignment_(self.A.NSTextAlignmentCenter)
            button.setAccessibilityLabel_(name)
            button.setToolTip_(name)
        self.controller.setView_(root)
        self.popover.setContentSize_((width, height))
        enabled = [view for view in self.focusables if view.isEnabled()]
        for left, right in zip(enabled, enabled[1:] + enabled[:1]):
            left.setNextKeyView_(right)
        if self.popover.isShown() and self.focusables and (force or focus_index is not None):
            index = min(focus_index or 0, len(self.focusables) - 1)
            target = self.focusables[index]
            root.window().makeFirstResponder_(target if target.isEnabled() else enabled[0])
        self._last_render = signature
