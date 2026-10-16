"""Bounded provider detail layout and quota grouping, independent of Cocoa."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from credusage.domain.models import Freshness, QuotaKind
from credusage.ui.dashboard import ContextView, LimitView

CANVAS_WIDTH = 200
CANVAS_HEIGHT = 260
CONTENT_SCALE = 1.25
WINDOW_WIDTH = CANVAS_WIDTH * CONTENT_SCALE
WINDOW_HEIGHT = CANVAS_HEIGHT * CONTENT_SCALE
FOOTER_Y = 236


@dataclass(frozen=True)
class QuotaGroup:
    key: str
    label: str
    short: LimitView | None = None
    long: LimitView | None = None
    credits: LimitView | None = None

    @property
    def readings(self) -> tuple[LimitView, ...]:
        return tuple(item for item in (self.short, self.long, self.credits) if item is not None)


@dataclass(frozen=True)
class DetailGeometry:
    width: float
    height: float
    compact: bool
    cards_y: float
    stats_y: float
    source_y: float
    actions_y: float
    footer_y: float


def detail_geometry(available_width: float, available_height: float) -> DetailGeometry:
    # AppKit positions the popover against its screen. Navigation never changes its canvas.
    return DetailGeometry(CANVAS_WIDTH, CANVAS_HEIGHT, True, 72, 168, 200, 214, FOOTER_Y)


def _generic_groups(limits: tuple[LimitView, ...]) -> tuple[QuotaGroup, ...]:
    remaining = [item for item in limits if item.kind != QuotaKind.CREDIT]
    groups = []
    while remaining:
        short = min(
            remaining,
            key=lambda item: (
                item.duration_seconds != 18000,
                item.duration_seconds or float("inf"),
            ),
        )
        remaining.remove(short)
        longer = [
            item
            for item in remaining
            if short.duration_seconds
            and item.duration_seconds
            and item.duration_seconds > short.duration_seconds
        ]
        long = next((item for item in longer if item.duration_seconds == 604800), None)
        if long is None and longer:
            long = max(longer, key=lambda item: item.duration_seconds)
        if long is not None:
            remaining.remove(long)
        groups.append(
            QuotaGroup(
                f"windows:{short.key}", "Usage windows" if not groups else short.label, short, long
            )
        )
    for item in limits:
        if item.kind == QuotaKind.CREDIT:
            groups.append(QuotaGroup(item.key, item.label, credits=item))
    return tuple(groups)


def quota_groups(integration: str, context: ContextView | None) -> tuple[QuotaGroup, ...]:
    if context is None:
        return ()
    if integration != "openai.codex":
        return _generic_groups(context.limits)
    pools, unmatched = {}, []
    for limit in context.limits:
        prefix, _, slot = limit.key.rpartition(":")
        # Adapter identities, not display labels or inferred shared-pool claims.
        if not prefix.startswith("codex:") or slot not in ("primary", "secondary", "credits"):
            unmatched.append(limit)
            continue
        pools.setdefault(prefix, {})[slot] = limit
    groups = tuple(
        QuotaGroup(
            key,
            key.removeprefix("codex:"),
            slots.get("primary"),
            slots.get("secondary"),
            slots.get("credits"),
        )
        for key, slots in pools.items()
    )
    return groups + _generic_groups(tuple(unmatched))


def selected_group(
    groups: tuple[QuotaGroup, ...], preferred: str | None, primary_key: str | None
) -> QuotaGroup | None:
    return (
        next((group for group in groups if group.key == preferred), None)
        or next(
            (group for group in groups if any(item.key == primary_key for item in group.readings)),
            None,
        )
        or next(iter(groups), None)
    )


def remaining_percent(limit: LimitView | None) -> Decimal | None:
    if limit is None or limit.percent_used is None:
        return None
    return max(Decimal(0), Decimal(100) - limit.percent_used)


def group_freshness(group: QuotaGroup | None) -> str:
    if group is None or not group.readings:
        return "Unavailable"
    priority = (Freshness.EXPIRED, Freshness.STALE, Freshness.UNDATED, Freshness.FRESH)
    return next(
        value.value.capitalize()
        for value in priority
        if any(item.freshness == value for item in group.readings)
    )


SOURCE_LABELS = {
    "openai.codex": "Codex CLI",
    "anthropic.claude": "Claude status-line",
    "cursor.editor": "Dashboard only",
    "google.antigravity": "CLI discovery",
    "example.demo": "Demo fixtures",
}
