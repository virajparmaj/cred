"""Native provider details using direct point sizes and unboxed usage columns."""

from decimal import Decimal

from credusage.domain.models import ActionKind, Freshness
from credusage.ui.detail_layout import SOURCE_LABELS, WINDOW_WIDTH, group_freshness
from credusage.ui.presentation import number, percentage, timestamp


def duration_name(limit):
    seconds = limit.duration_seconds if limit else None
    if not seconds:
        return "Not reported"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{number(Decimal(seconds) / 60)}m"


def window_title(limit, fallback):
    if limit is None:
        return fallback
    if limit.duration_seconds == 18000:
        return "5-hour"
    if limit.duration_seconds == 604800:
        return "Weekly"
    return duration_name(limit)


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
    """Round display only; preserve exact quantities in accessibility text."""
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
        self.ui.separator(root, self.g.stats_y - 8)
        self.status_strip(root)
        self.actions(root)
        if self.ui.action_message:
            self.ui.text(
                root,
                self.ui.action_message,
                16,
                self.g.feedback_y,
                WINDOW_WIDTH - 32,
                size=11,
                height=30,
                secondary=True,
                wrap=True,
            )
        return self.g.footer_y

    def checked_at(self):
        if self.context is None:
            return None
        if self.provider.integration_id == "cursor.editor":
            return self.context.last_checked_at or self.context.last_success_at
        return self.context.last_success_at or self.context.last_checked_at

    def header(self, root):
        ui = self.ui
        back = ui.button(root, "All Providers", ui.overview, 16, 8, 108, symbol="chevron.left")
        back.setAccessibilityLabel_("Back to all providers")
        ui.updated_label(root, self.context.last_success_at if self.context else None)
        ui.separator(root, 40, x=0, width=WINDOW_WIDTH)
        ui.logo(root, self.provider, 16, 52, 56)
        ui.text(root, self.provider.name, 80, 52, 160, size=15, bold=True, height=22)
        status = self.context.status if self.context else "Not configured"
        if self.context and self.context.loading:
            status += " · Refreshing…"
        if self.context and self.context.plan:
            status += f" · {self.context.plan.capitalize()}"
        ui.text(root, status, 80, 78, 160, size=11, height=16, secondary=True)
        if len(self.provider.contexts) <= 1:
            label = self.context.label if self.context else "Not configured"
            ui.text(
                root,
                label,
                80,
                98,
                160,
                size=11,
                height=17,
                secondary=True,
                alignment=ui.A.NSTextAlignmentLeft,
            )
        ui.separator(root, 120)

    def picker(self, root, labels, identities, selected, y, action, accessibility):
        ui = self.ui
        picker = ui.Picker.alloc().initWithFrame_pullsDown_(
            ui.rect(16, y, WINDOW_WIDTH - 32, 24), False
        )
        picker.setControlSize_(ui.A.NSControlSizeSmall)
        picker.setFont_(ui.A.NSFont.systemFontOfSize_(12))
        picker.setFocusRingType_(ui.A.NSFocusRingTypeExterior)
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
        y = 124
        if len(self.provider.contexts) > 1:
            self.picker(
                root,
                [
                    c.label + (" · disabled" if not c.enabled else "")
                    for c in self.provider.contexts
                ],
                [c.connection_id for c in self.provider.contexts],
                self.provider.contexts.index(self.context),
                y,
                "contextChanged:",
                "Provider context",
            )
            y += 32
        if len(self.groups) > 1:
            self.picker(
                root,
                [g.label for g in self.groups],
                [g.key for g in self.groups],
                self.groups.index(self.group),
                y,
                "poolChanged:",
                "Quota pool",
            )

    def cards(self, root):
        self.card(root, self.group.short, "Short term", 16)
        self.card(root, self.group.long, "Long term", 140)

    def metric(self, root, value, x, y, percent):
        ui = self.ui
        field = ui.text(
            root,
            value,
            x,
            y,
            100,
            size=26 if percent else 12,
            height=36,
            monospaced=percent,
        )
        if percent:
            styled = ui.A.NSMutableAttributedString.alloc().initWithAttributedString_(
                field.attributedStringValue()
            )
            styled.addAttribute_value_range_(
                ui.A.NSFontAttributeName, ui.A.NSFont.systemFontOfSize_(11), (len(value) - 4, 4)
            )
            for size in range(26, 17, -1):
                styled.addAttribute_value_range_(
                    ui.A.NSFontAttributeName,
                    ui.A.NSFont.monospacedDigitSystemFontOfSize_weight_(
                        size, ui.A.NSFontWeightMedium
                    ),
                    (0, len(value) - 5),
                )
                if styled.size().width <= 96:
                    break
            field.setAttributedStringValue_(styled)
        field.setAccessibilityLabel_(value)

    def card(self, root, limit, fallback, x):
        ui, y = self.ui, self.g.cards_y
        ui.text(root, window_title(limit, fallback), x, y, 100, size=11, height=16, secondary=True)
        percent = bool(limit and limit.percent_used is not None)
        value = (
            f"{percentage(limit.percent_used)}% used"
            if percent
            else limit.usage if limit else "Not reported"
        )
        self.metric(root, value, x, y + 18, percent)
        if percent:
            ui.progress(
                root,
                limit.percent_used,
                self.provider.integration_id,
                x,
                y + 60,
                100,
                limit.freshness != Freshness.FRESH,
            )
        reset = ui.text(
            root,
            limit.reset_label if limit else "Reset not reported",
            x,
            y + 70,
            100,
            size=11,
            height=16,
            secondary=True,
        )
        if limit:
            reset.setToolTip_(f"{limit.label}\nReported reset: {timestamp(limit.resets_at)}")
            reset.setAccessibilityLabel_(
                f"{limit.reset_label}; reported reset {timestamp(limit.resets_at)}"
            )
        if not limit or limit.freshness != Freshness.FRESH:
            field = ui.text(
                root,
                limit.freshness.value.capitalize() if limit else "Unavailable",
                x,
                y + 88,
                100,
                size=11,
                height=16,
                secondary=True,
            )
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
        ui.text(
            root, title, 16, self.g.cards_y + 8, WINDOW_WIDTH - 32, size=15, height=22, bold=True
        )
        ui.text(
            root,
            message,
            16,
            self.g.cards_y + 38,
            WINDOW_WIDTH - 32,
            size=12,
            height=60,
            secondary=True,
            wrap=True,
        )

    def stat(self, root, title, value, x, tooltip=None):
        ui, y = self.ui, self.g.stats_y
        ui.text(root, title, x, y, 72, size=11, height=16, secondary=True)
        field = ui.text(
            root, value, x, y + 18, 72, size=12, height=18, monospaced=title != "Freshness"
        )
        field.setToolTip_(tooltip or value)
        field.setAccessibilityLabel_(f"{title}: {tooltip or value}")

    def status_strip(self, root):
        ui, context = self.ui, self.context
        checked = self.checked_at()
        title = (
            "Updated"
            if context
            and context.last_success_at
            and self.provider.integration_id != "cursor.editor"
            else "Checked"
        )
        self.stat(
            root,
            title,
            checked.astimezone().strftime("%H:%M") if checked else "Unknown",
            16,
            timestamp(checked),
        )
        self.stat(root, "Freshness", group_freshness(self.group), 92)
        credit = self.group.credits if self.group and context and context.enabled else None
        if credit:
            self.stat(root, "Credits", credit_label(credit.remaining), 168, credit.usage)
        else:
            value = (
                "Verified"
                if context and context.verified
                else "Unverified" if context else "Not set"
            )
            self.stat(root, "Account", value, 168)
        source = (
            "Demo fixtures"
            if ui.dashboard.demo
            else SOURCE_LABELS.get(self.provider.integration_id, "Provider source")
        )
        source = "Source: " + source
        if context and not context.verified:
            source += " · Unverified reading"
        elif context and context.verified:
            source += " · Verified"
        field = ui.text(
            root,
            source,
            16,
            self.g.source_y,
            WINDOW_WIDTH - 32,
            size=11,
            height=30,
            secondary=True,
            wrap=True,
        )
        field.setToolTip_(source + ("\n" + "\n".join(context.facts) if context else ""))
        field.setAccessibilityLabel_(source)

    def actions(self, root):
        ui, context = self.ui, self.context
        refresh = ui.button(
            root,
            "Refreshing…" if context and context.loading else "Refresh",
            ui.refresh_detail,
            16,
            self.g.actions_y,
            108,
            filled=True,
        )
        refresh.setEnabled_(bool(context and context.enabled and not context.loading))
        action = dashboard_action(ui.shell.controller, context)
        label = (
            "Antigravity" if self.provider.integration_id == "google.antigravity" else "Dashboard"
        )
        button = ui.button(
            root,
            label,
            ui.open_dashboard,
            132,
            self.g.actions_y,
            108,
            symbol="arrow.up.right",
            filled=True,
        )
        button.setEnabled_(bool(action and not ui.shell.controller.config.demo))
        if action:
            button.setToolTip_(action.label)
        if ui.shell.controller.config.demo:
            button.setToolTip_("Demo mode: external actions are disabled")
