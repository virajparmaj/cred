"""Fixed-height native provider pages, rendered through the popover's controls."""

from decimal import Decimal

from credusage.domain.models import ActionKind, Freshness
from credusage.ui.detail_layout import SOURCE_LABELS, group_freshness, remaining_percent
from credusage.ui.presentation import number, timestamp


def duration_name(limit):
    seconds = limit.duration_seconds if limit else None
    if not seconds:
        return "Not reported"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{number(Decimal(seconds) / 60)}m"


def dashboard_action(controller, context):
    if context is None:
        return None
    return next(
        (
            action
            for action in controller.actions(context.connection_id)
            if action.action_id == "dashboard" and action.kind == ActionKind.OPEN_URL
        ),
        None,
    )


def credit_label(value):
    """Round the compact display only; exact credit quantities remain in accessibility text."""
    if value is not None and 0 < abs(value) < Decimal("0.01"):
        return "<0.01" if value > 0 else "−<0.01"
    return f"{value:,.2f}".rstrip("0").rstrip(".") if value is not None else "Not reported"


class CompactDetails:
    def __init__(self, owner, geometry):
        self.ui, self.g = owner, geometry
        self.provider = owner.current_provider()
        self.context = owner.current_context()
        self.groups = owner.current_groups()
        self.group = owner.current_group()

    def render(self, root):
        self.header(root)
        self.selectors(root)
        if (
            self.context
            and self.context.enabled
            and self.group
            and (self.group.short or self.group.long)
        ):
            self.cards(root)
        else:
            self.unavailable(root)
        self.status_strip(root)
        self.actions(root)
        return self.g.footer_y

    def header(self, root):
        ui = self.ui
        back = ui.button(root, "", ui.overview, 6, 7, 22, 22, symbol="chevron.left")
        back.setAccessibilityLabel_("Back to overview")
        back.setToolTip_("Back to overview")
        ui.logo(root, self.provider, 34, 8, 22)
        ui.text(root, self.provider.name, 60, 7, 132, size=12, bold=True, height=17)
        status = self.context.status if self.context else "Not configured"
        if self.context and self.context.loading:
            status += " · Refreshing…"
        if self.context and self.context.plan:
            status += f" · {self.context.plan.capitalize()}"
        ui.text(root, status, 60, 24, 132, size=8, height=12, secondary=True)

    def picker(self, root, labels, identities, selected, x, width, action, accessibility):
        ui = self.ui
        picker = ui.Picker.alloc().initWithFrame_pullsDown_(ui.rect(x, 43, width, 20), False)
        picker.setControlSize_(ui.A.NSControlSizeSmall)
        picker.setFont_(ui.A.NSFont.systemFontOfSize_(9))
        picker.setFocusRingType_(ui.A.NSFocusRingTypeNone)
        picker.addItemsWithTitles_(labels)
        for index, identity in enumerate(identities):
            picker.itemAtIndex_(index).setRepresentedObject_(identity)
        picker.selectItemAtIndex_(selected)
        picker.owner = ui
        picker.setTarget_(ui.shell.delegate)
        picker.setAction_(action)
        picker.setAccessibilityLabel_(accessibility)
        picker.setToolTip_(labels[selected])
        root.addSubview_(picker)
        ui.focusables.append(picker)

    def selectors(self, root):
        context_picker = len(self.provider.contexts) > 1
        pool_picker = len(self.groups) > 1
        width = 86 if context_picker and pool_picker else 180
        if context_picker:
            labels = [
                c.label + (" · disabled" if not c.enabled else "") for c in self.provider.contexts
            ]
            self.picker(
                root,
                labels,
                [c.connection_id for c in self.provider.contexts],
                self.provider.contexts.index(self.context),
                10,
                width,
                "contextChanged:",
                "Provider context",
            )
        if pool_picker:
            self.picker(
                root,
                [g.label for g in self.groups],
                [g.key for g in self.groups],
                self.groups.index(self.group),
                104 if context_picker else 10,
                width,
                "poolChanged:",
                "Quota pool",
            )
        if not context_picker and not pool_picker:
            label = self.context.label if self.context else "Configure in Settings"
            self.ui.text(root, label, 10, 46, 180, size=9, height=13, secondary=True)

    def cards(self, root):
        self.ui.tile(root, 6, 68, 92, 96)
        self.ui.tile(root, 102, 68, 92, 96)
        self.card(root, self.group.short, "Short term", 10)
        self.card(root, self.group.long, "Long term", 106)

    def metric(self, root, value, x, y, percent):
        ui = self.ui
        field = ui.text(root, value, x, y, 84, size=20 if percent else 10, height=27, bold=True)
        if percent:
            styled = ui.A.NSMutableAttributedString.alloc().initWithAttributedString_(
                field.attributedStringValue()
            )
            styled.addAttribute_value_range_(
                ui.A.NSFontAttributeName, ui.A.NSFont.systemFontOfSize_(8), (len(value) - 4, 4)
            )
            for size in range(20, 11, -1):
                styled.addAttribute_value_range_(
                    ui.A.NSFontAttributeName,
                    ui.A.NSFont.boldSystemFontOfSize_(size),
                    (0, len(value) - 5),
                )
                if styled.size().width <= 80:
                    break
            field.setAttributedStringValue_(styled)
        field.setAccessibilityLabel_(value)

    def card(self, root, limit, title, x):
        ui, y = self.ui, self.g.cards_y
        label = f"{duration_name(limit)} · {title}" if limit else title
        ui.text(root, label.upper(), x, y, 84, size=8, height=13, bold=True, secondary=True)
        percent = bool(limit and limit.percent_used is not None)
        value = (
            f"{number(limit.percent_used)}% used"
            if percent
            else (limit.usage if limit else "Not reported")
        )
        self.metric(root, value, x, y + 17, percent)
        ui.progress(
            root,
            limit.percent_used if limit else None,
            self.provider.integration_id,
            x,
            y + 45,
            84,
            bool(limit and limit.freshness != Freshness.FRESH),
        )
        reset = ui.text(
            root,
            limit.reset_label if limit else "Reset not reported",
            x,
            y + 54,
            84,
            size=8,
            height=12,
            secondary=True,
        )
        if limit:
            reset.setToolTip_(f"{limit.label}\nReported reset: {timestamp(limit.resets_at)}")
            reset.setAccessibilityLabel_(
                f"{limit.reset_label}; reported reset {timestamp(limit.resets_at)}"
            )
        left = remaining_percent(limit)
        remaining = f"{number(left)}% left" if left is not None else "Remaining unknown"
        ui.text(root, remaining, x, y + 67, 84, size=9, height=13, secondary=True)
        if not limit or limit.freshness != Freshness.FRESH:
            freshness = limit.freshness.value.capitalize() if limit else "Unavailable"
            field = ui.text(root, freshness, x, y + 80, 84, size=8, height=12, secondary=True)
            if limit:
                field.setToolTip_("\n".join(limit.facts))

    def unavailable(self, root):
        ui = self.ui
        title, message = "Usage unavailable", "Configure this provider in Settings."
        if self.context:
            message = self.context.message
            if not self.context.enabled:
                title, message = "Connection disabled", "Enable this connection in Settings."
            elif self.group and self.group.credits:
                title, message = "Credit balance", self.group.credits.usage
            elif self.provider.integration_id == "cursor.editor":
                title = "Dashboard only"
            elif self.provider.integration_id == "google.antigravity":
                title = "Source readiness"
        ui.tile(root, 6, 68, 188, 96)
        ui.text(root, title, 12, 76, 176, size=12, height=18, bold=True)
        field = ui.text(root, message, 12, 101, 176, size=9, height=57, secondary=True, wrap=True)
        field.setAccessibilityLabel_(message)

    def stat(self, root, title, value, x, tooltip=None):
        ui, y = self.ui, self.g.stats_y
        ui.text(root, title, x, y + 6, 56, size=7.5, height=11, secondary=True)
        field = ui.text(root, value, x, y + 17, 56, size=9, height=13, bold=True)
        if field.attributedStringValue().size().width > 52:
            field.setFont_(ui.A.NSFont.boldSystemFontOfSize_(8))
        field.setToolTip_(tooltip or value)
        field.setAccessibilityLabel_(f"{title}: {tooltip or value}")

    def status_strip(self, root):
        ui, context = self.ui, self.context
        ui.tile(root, 6, self.g.stats_y, 188, 31)
        discovery_only = self.provider.integration_id in ("cursor.editor", "google.antigravity")
        checked = (
            (
                (context.last_checked_at or context.last_success_at)
                if discovery_only
                else (context.last_success_at or context.last_checked_at)
            )
            if context
            else None
        )
        title = (
            "Updated" if context and context.last_success_at and not discovery_only else "Checked"
        )
        self.stat(
            root,
            title,
            checked.astimezone().strftime("%H:%M") if checked else "Unknown",
            10,
            timestamp(checked),
        )
        self.stat(root, "Freshness", group_freshness(self.group), 72)
        credit = self.group.credits if self.group else None
        if credit:
            self.stat(root, "Credits", credit_label(credit.remaining), 134, credit.usage)
        else:
            value = (
                "Verified"
                if context and context.verified
                else ("Unverified" if context else "Not set")
            )
            self.stat(root, "Account", value, 134, "Not configured" if context is None else None)
        source = (
            "Demo fixtures"
            if ui.dashboard.demo
            else SOURCE_LABELS.get(self.provider.integration_id, "Provider source")
        )
        if credit and context and not context.verified:
            source += " · Unverified"
        field = ui.text(
            root,
            ui.action_message or source,
            10,
            self.g.source_y,
            180,
            size=8,
            height=11,
            secondary=True,
        )
        field.setToolTip_("\n".join(context.facts) if context else source)
        field.setAccessibilityLabel_(ui.action_message or source)

    def actions(self, root):
        ui, context = self.ui, self.context
        refresh = ui.button(
            root,
            "Refreshing…" if context and context.loading else "Refresh",
            ui.refresh_detail,
            10,
            self.g.actions_y,
            58,
            symbol="arrow.clockwise",
        )
        refresh.setEnabled_(bool(context and context.enabled and not context.loading))
        action = dashboard_action(ui.shell.controller, context)
        label = (
            "Open Antigravity"
            if self.provider.integration_id == "google.antigravity"
            else "Dashboard"
        )
        button = ui.button(
            root,
            label,
            ui.open_dashboard,
            82,
            self.g.actions_y,
            108,
            symbol="arrow.up.right.square",
        )
        button.setEnabled_(bool(action and not ui.shell.controller.config.demo))
        if action:
            button.setToolTip_(action.label)
        if ui.shell.controller.config.demo:
            button.setToolTip_("Demo mode: external actions are disabled")
