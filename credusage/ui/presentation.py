"""Pure, accessible presentation of provider-independent allowance state.

No native imports, I/O, or provider assumptions belong in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from credusage.domain.models import (
    Availability,
    ConnectionHealth,
    ConnectionState,
    Freshness,
    IntegrationDescriptor,
    ObservationBasis,
    QuotaObservation,
    Unit,
)

HEALTH_LABELS = {
    ConnectionHealth.DISCONNECTED: "Not connected",
    ConnectionHealth.CONNECTED: "Connected",
    ConnectionHealth.AUTH_REQUIRED: "Authentication required",
    ConnectionHealth.LOGIN_PENDING: "Login pending",
    ConnectionHealth.UNSUPPORTED_AUTH: "Unsupported authentication mode",
    ConnectionHealth.THROTTLED: "Source cooldown",
    ConnectionHealth.TRANSPORT_FAILURE: "Refresh failed",
    ConnectionHealth.SOURCE_UNAVAILABLE: "Source unavailable",
}


def number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def timestamp(value: datetime | None) -> str:
    return value.astimezone().strftime("%b %d, %H:%M:%S %Z") if value else "Unknown"


def countdown(target: datetime | None, now: datetime) -> str:
    if target is None:
        return ""
    seconds = int((target - now).total_seconds())
    if seconds <= 0:
        return "expected reset passed"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else "<1m"


def quantity(value: Decimal, observation: QuotaObservation) -> str:
    if observation.unit == Unit.PERCENT:
        return f"{number(value)}%"
    if observation.unit == Unit.CURRENCY:
        return f"{observation.currency or 'Unknown currency'} {number(value)}"
    if observation.unit == Unit.CREDITS:
        return f"{number(value)} {observation.credit_unit or 'credits'}"
    return f"{number(value)} {observation.unit.value}"


def usage_label(observation: QuotaObservation) -> str:
    if observation.availability == Availability.UNLIMITED:
        return "Unlimited (reported)"
    if observation.availability == Availability.UNSUPPORTED:
        return "Numerical usage unsupported"
    if observation.availability == Availability.NOT_APPLICABLE:
        return "Not applicable"
    if observation.used_percent is not None:
        return f"{number(observation.used_percent)}% used"
    if observation.used is not None and observation.limit is not None:
        return f"{quantity(observation.used, observation)} used of {quantity(observation.limit, observation)}"
    if observation.remaining is not None:
        return f"{quantity(observation.remaining, observation)} remaining"
    if observation.used is not None:
        return f"{quantity(observation.used, observation)} used (limit unknown)"
    if observation.state.value != "unknown":
        return f"{observation.state.value.capitalize()} (reported; quantity unknown)"
    return "Usage unknown"


@dataclass(frozen=True)
class BucketPresentation:
    key: str
    title: str
    details: tuple[str, ...]
    pin_title: str
    pinned: bool = False


@dataclass(frozen=True)
class ConnectionPresentation:
    connection_id: str
    integration_id: str
    vendor_id: str
    title: str
    details: tuple[str, ...]
    buckets: tuple[BucketPresentation, ...]
    enabled: bool


@dataclass(frozen=True)
class MenuPresentation:
    title: str
    connections: tuple[ConnectionPresentation, ...]
    demo: bool = False


def present_bucket(
    observation: QuotaObservation, now: datetime, pinned: bool = False
) -> BucketPresentation:
    freshness = observation.freshness(now)
    usage = usage_label(observation)
    suffix = "" if freshness == Freshness.FRESH else f" · {freshness.value}"
    if freshness == Freshness.EXPIRED:
        suffix += " historical reading"
    source = observation.provenance
    details = [
        f"Freshness: {freshness.value}",
        f"Source: {source.source_id}",
        f"Basis: {source.basis.value.replace('_', ' ')}",
        f"As of: {timestamp(source.event_at)}" if source.event_at else "As of: source time unknown",
        f"Fetched: {timestamp(source.fetched_at)}",
        f"Account binding: {'verified' if observation.binding.is_verified else 'unverified local context'}",
    ]
    window = observation.window
    if window.duration_seconds:
        hours = Decimal(window.duration_seconds) / Decimal(3600)
        details.append(f"Window: {number(hours)} hours ({window.kind.value})")
    else:
        details.append(f"Window: {window.kind.value}")
    if window.resets_at or window.ends_at:
        details.append(
            f"{'Expected reset passed; verification needed' if freshness == Freshness.EXPIRED else 'Reported next reset'}: {timestamp(window.resets_at or window.ends_at)}"
        )
    if observation.shared_pool_id and observation.sharing_evidence:
        details.extend(
            (
                f"Shared pool: {observation.shared_pool_id}",
                f"Sharing evidence: {observation.sharing_evidence}",
            )
        )
    if observation.model_ids:
        details.append("Models: " + ", ".join(observation.model_ids))
    if observation.feature:
        details.append(f"Feature: {observation.feature}")
    return BucketPresentation(
        observation.bucket_key,
        f"{observation.label}: {usage}{suffix}",
        tuple(details),
        "Pinned to menu bar" if pinned else "Pin to menu bar",
        pinned,
    )


def build_menu(
    states: tuple[ConnectionState, ...],
    descriptors: tuple[IntegrationDescriptor, ...],
    now: datetime,
    pinned_connection_id: str | None = None,
    pinned_bucket_key: str | None = None,
    demo: bool = False,
) -> MenuPresentation:
    by_id = {descriptor.integration_id: descriptor for descriptor in descriptors}
    connections = []
    title = "CredUsage demo" if demo else "CredUsage"
    for state in sorted(
        states,
        key=lambda state: (
            state.connection.preferences.order,
            state.connection.label.casefold(),
            state.connection.connection_id,
        ),
    ):
        connection = state.connection
        descriptor = by_id.get(connection.integration_id)
        display_name = descriptor.display_name if descriptor else connection.integration_id
        health = HEALTH_LABELS[state.outcome.health]
        if not connection.enabled:
            health = "Disconnected (disabled)"
        elif state.loading:
            health += " · refreshing"
        binding = state.snapshot.binding if state.snapshot else connection.binding
        details = [
            f"Status: {health}",
            f"Context: {connection.label}",
            f"Account binding: {'verified' if binding.is_verified else 'unverified local context'}",
        ]
        if state.snapshot and state.snapshot.plan:
            details.append(f"Plan: {state.snapshot.plan}")
        if descriptor:
            details.extend(
                (
                    f"Source policy: {descriptor.source_policy.value.replace('_', ' ')}",
                    f"Validation: {descriptor.validation.value.replace('_', ' ')}",
                    descriptor.evidence,
                )
            )
        if state.outcome.message:
            details.append(state.outcome.message)
        if state.outcome.retry_at:
            details.append(f"Retry after: {timestamp(state.outcome.retry_at)}")
        if state.outcome.last_success_at:
            details.append(f"Last successful refresh: {timestamp(state.outcome.last_success_at)}")
        observations = state.snapshot.observations if state.snapshot else ()
        buckets = tuple(
            present_bucket(
                observation,
                now,
                connection.connection_id == pinned_connection_id
                and observation.bucket_key == pinned_bucket_key,
            )
            for observation in observations
        )
        if not buckets:
            details.append("Usage unknown · no numerical observations")
        for observation, bucket in zip(observations, buckets):
            if bucket.pinned:
                historical = (
                    " " + observation.freshness(now).value
                    if observation.freshness(now) != Freshness.FRESH
                    else ""
                )
                condition = ""
                if not connection.enabled:
                    condition = " · disabled"
                elif state.outcome.health != ConnectionHealth.CONNECTED:
                    condition = " · " + HEALTH_LABELS[state.outcome.health].lower()
                reset = countdown(observation.window.resets_at or observation.window.ends_at, now)
                reset_text = f" · {reset}" if reset else ""
                title = f"{'Demo · ' if demo else ''}{display_name}: {usage_label(observation)}{reset_text}{historical}{condition}"
        connections.append(
            ConnectionPresentation(
                connection.connection_id,
                connection.integration_id,
                descriptor.vendor_id if descriptor else "other",
                f"{display_name} · {connection.label} · {health}",
                tuple(details),
                buckets,
                connection.enabled,
            )
        )
    return MenuPresentation(title, tuple(connections), demo)
