"""Pure overview/detail records for the native popover; no Cocoa or I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from credusage.domain.models import (
    Availability,
    ConnectionState,
    Freshness,
    IntegrationDescriptor,
    QuotaKind,
    QuotaObservation,
    Unit,
)
from credusage.ui.presentation import (
    HEALTH_LABELS,
    countdown,
    number,
    present_bucket,
    quantity,
    usage_label,
)

PROVIDERS = (
    ("google.antigravity", "Antigravity", "ProviderAntigravity.png"),
    ("openai.codex", "Codex", "ProviderCodex.png"),
    ("anthropic.claude", "Claude Code", "ProviderClaude.png"),
    ("cursor.editor", "Cursor", "ProviderCursor.png"),
)


@dataclass(frozen=True)
class LimitView:
    key: str
    label: str
    usage: str
    percent_used: Decimal | None
    window_label: str
    resets_at: datetime | None
    reset_label: str
    freshness: Freshness
    quantity_summary: str
    facts: tuple[str, ...]
    duration_seconds: int | None
    kind: QuotaKind
    unit: Unit
    remaining: Decimal | None
    credit_unit: str | None
    fetched_at: datetime
    source_id: str


@dataclass(frozen=True)
class ContextView:
    connection_id: str
    label: str
    enabled: bool
    status: str
    plan: str | None
    verified: bool
    loading: bool
    limits: tuple[LimitView, ...]
    primary_key: str | None
    facts: tuple[str, ...]
    message: str
    last_success_at: datetime | None
    last_checked_at: datetime | None

    @property
    def primary(self) -> LimitView | None:
        return next((item for item in self.limits if item.key == self.primary_key), None)


@dataclass(frozen=True)
class ProviderView:
    integration_id: str
    name: str
    icon: str
    contexts: tuple[ContextView, ...]
    selected_id: str | None

    @property
    def selected(self) -> ContextView | None:
        return next(
            (item for item in self.contexts if item.connection_id == self.selected_id), None
        )


@dataclass(frozen=True)
class DashboardView:
    providers: tuple[ProviderView, ...]
    demo: bool = False


def window_label(observation: QuotaObservation) -> str:
    seconds = observation.window.duration_seconds
    if seconds:
        if seconds % 86400 == 0:
            return f"{seconds // 86400}d window"
        if seconds % 3600 == 0:
            return f"{seconds // 3600}h window"
        return f"{number(Decimal(seconds) / 60)}m window"
    kind = observation.window.kind.value
    return "Window not reported" if kind == "unknown" else f"{kind.capitalize()} window"


def present_limit(observation: QuotaObservation, now: datetime) -> LimitView:
    percent = observation.used_percent
    if observation.availability != Availability.KNOWN:
        percent = None
    if percent is None and observation.availability == Availability.KNOWN:
        if observation.used is not None and observation.limit is not None and observation.limit > 0:
            percent = observation.used / observation.limit * 100
    reset = observation.window.resets_at or observation.window.ends_at
    reset_label = (
        "Reset passed · awaiting source"
        if reset and reset <= now
        else f"Resets in {countdown(reset, now)}" if reset else "Reset not reported"
    )
    facts = list(present_bucket(observation, now).details)
    quantities = []
    for name in ("used", "remaining", "limit"):
        value = getattr(observation, name)
        if value is not None:
            item = f"{name.capitalize()}: {quantity(value, observation)}"
            facts.insert(0, item)
            quantities.append(item)
    return LimitView(
        observation.bucket_key,
        observation.label,
        usage_label(observation),
        percent,
        window_label(observation),
        reset,
        reset_label,
        observation.freshness(now),
        " · ".join(quantities),
        tuple(facts),
        observation.window.duration_seconds,
        observation.kind,
        observation.unit,
        observation.remaining,
        observation.credit_unit,
        observation.provenance.fetched_at,
        observation.provenance.source_id,
    )


def primary_observation(observations: tuple[QuotaObservation, ...]) -> QuotaObservation | None:
    allowances = tuple(item for item in observations if item.kind != QuotaKind.CREDIT)
    if not allowances:
        return None
    # Stable source order breaks ties, retaining distinct model pools in details.
    return min(
        allowances,
        key=lambda item: (
            item.window.duration_seconds != 18000,
            item.window.duration_seconds or float("inf"),
        ),
    )


def present_context(
    state: ConnectionState, descriptor: IntegrationDescriptor | None, now: datetime
) -> ContextView:
    connection, snapshot = state.connection, state.snapshot
    observations = snapshot.observations if snapshot else ()
    primary = primary_observation(observations)
    status = HEALTH_LABELS[state.outcome.health] if connection.enabled else "Disabled in Settings"
    facts = [f"Context: {connection.label}", f"Status: {status}"]
    binding = snapshot.binding if snapshot else connection.binding
    facts.append(
        "Account verified" if binding.is_verified else "Account binding: unverified local context"
    )
    if descriptor:
        facts.extend(
            (
                f"Source policy: {descriptor.source_policy.value.replace('_', ' ')}",
                f"Validation: {descriptor.validation.value.replace('_', ' ')}",
                descriptor.evidence,
            )
        )
    if state.outcome.retry_at:
        facts.append(f"Retry after: {state.outcome.retry_at.astimezone():%b %d, %H:%M:%S %Z}")
    if state.outcome.last_success_at:
        facts.append(
            f"Last refresh: {state.outcome.last_success_at.astimezone():%b %d, %H:%M:%S %Z}"
        )
    return ContextView(
        connection.connection_id,
        connection.label,
        connection.enabled,
        status,
        snapshot.plan if snapshot else None,
        binding.is_verified,
        state.loading,
        tuple(present_limit(item, now) for item in observations),
        primary.bucket_key if primary else None,
        tuple(facts),
        state.outcome.message
        or ("" if observations else "Usage is unavailable. Configure this source in Settings."),
        state.outcome.last_success_at or (snapshot.collected_at if snapshot else None),
        state.outcome.attempted_at,
    )


def build_dashboard(
    states: tuple[ConnectionState, ...],
    descriptors: tuple[IntegrationDescriptor, ...],
    now: datetime,
    pinned_connection_id: str | None = None,
    demo: bool = False,
) -> DashboardView:
    descriptors_by_id = {item.integration_id: item for item in descriptors}
    providers = []
    for integration, name, icon in PROVIDERS:
        matching = sorted(
            (state for state in states if state.connection.integration_id == integration),
            key=lambda state: (
                state.connection.preferences.order,
                state.connection.label.casefold(),
                state.connection.connection_id,
            ),
        )
        contexts = tuple(
            present_context(state, descriptors_by_id.get(integration), now) for state in matching
        )
        selected = next(
            (
                item
                for item in contexts
                if item.connection_id == pinned_connection_id and item.enabled
            ),
            None,
        )
        selected = selected or next((item for item in contexts if item.enabled), None)
        selected = selected or next(iter(contexts), None)
        providers.append(
            ProviderView(
                integration, name, icon, contexts, selected.connection_id if selected else None
            )
        )
    if demo:
        # Demo connections keep their real synthetic identity; never masquerade as a vendor.
        demo_contexts = tuple(
            present_context(state, descriptors_by_id.get(state.connection.integration_id), now)
            for state in states
            if state.connection.integration_id == "example.demo"
        )
        if demo_contexts:
            providers.append(
                ProviderView(
                    "example.demo",
                    "Demo fixtures",
                    "MenuIconTemplate.png",
                    demo_contexts,
                    demo_contexts[0].connection_id,
                )
            )
    return DashboardView(tuple(providers), demo)


def overview_status(context: ContextView | None) -> str:
    if context is None:
        return "Configure in Settings"
    if not context.enabled:
        return "Disabled in Settings"
    if context.primary is None:
        return "Refreshing…" if context.loading else context.status
    limit = context.primary
    parts = [limit.window_label, limit.reset_label]
    if limit.freshness != Freshness.FRESH:
        parts.append(limit.freshness.value.capitalize())
    if context.loading:
        parts.append("Refreshing…")
    elif context.status != "Connected":
        parts.append(context.status)
    return " · ".join(parts)


class ResetRefreshTracker:
    """Request each reported reset once; the coordinator still enforces scheduling."""

    def __init__(self):
        self._seen = set()

    def due(self, states: tuple[ConnectionState, ...], now: datetime) -> tuple[str, ...]:
        current, due = set(), set()
        for state in states:
            if not state.connection.enabled or state.snapshot is None:
                continue
            for observation in state.snapshot.observations:
                reset = observation.window.resets_at or observation.window.ends_at
                key = (
                    state.connection.connection_id,
                    state.connection.generation,
                    observation.bucket_key,
                    reset,
                )
                if reset and now >= reset:
                    current.add(key)
                    if key not in self._seen:
                        due.add(state.connection.connection_id)
        self._seen = current
        return tuple(sorted(due))
