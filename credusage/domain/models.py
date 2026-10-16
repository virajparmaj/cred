"""Immutable domain records. This module has no platform or I/O dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum


class Capability(StrEnum):
    DISCOVERY = "discovery"
    PLAN = "plan"
    QUOTAS = "quotas"
    TELEMETRY = "telemetry"
    CREDITS = "credits"
    DASHBOARD = "dashboard"
    LOGIN = "login"
    PUSH = "push"


class ValidationStatus(StrEnum):
    NOT_IMPLEMENTED = "not_implemented"
    FIXTURE_TESTED = "fixture_tested"
    LIVE_VERIFIED = "live_verified"


class SourcePolicy(StrEnum):
    DOCUMENTED = "documented"
    LOCAL_TELEMETRY = "local_telemetry"
    EXPERIMENTAL = "experimental"
    DASHBOARD_ONLY = "dashboard_only"


class ConnectionHealth(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    AUTH_REQUIRED = "needs_authentication"
    LOGIN_PENDING = "login_pending"
    UNSUPPORTED_AUTH = "unsupported_auth_mode"
    THROTTLED = "collector_throttled"
    TRANSPORT_FAILURE = "transport_failure"
    SOURCE_UNAVAILABLE = "source_unavailable"


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"
    UNDATED = "undated"


class ObservationBasis(StrEnum):
    REPORTED = "provider_reported"
    TELEMETRY = "provider_telemetry"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    DEMO = "demo"


class Availability(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"
    UNLIMITED = "unlimited"


class QuotaState(StrEnum):
    AVAILABLE = "available"
    LOW = "low"
    EXHAUSTED = "exhausted"
    UNKNOWN = "unknown"


class QuotaKind(StrEnum):
    ALLOWANCE = "included_allowance"
    RATE_LIMIT = "rate_limit"
    FEATURE = "feature_allowance"
    CONCURRENCY = "concurrency"
    SPEND = "spend_budget"
    CREDIT = "credit_balance"


class Unit(StrEnum):
    PERCENT = "percent"
    REQUESTS = "requests"
    TOKENS = "tokens"
    CURRENCY = "currency"
    CREDITS = "credits"
    CONCURRENCY = "concurrency"
    UNKNOWN = "unknown"


class WindowKind(StrEnum):
    FIXED = "fixed"
    ROLLING = "rolling"
    REFILL = "refill"
    BILLING = "billing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntegrationDescriptor:
    integration_id: str
    vendor_id: str
    display_name: str
    version: str = "1"
    capabilities: tuple[Capability, ...] = ()
    source_policy: SourcePolicy = SourcePolicy.DOCUMENTED
    validation: ValidationStatus = ValidationStatus.FIXTURE_TESTED
    evidence: str = "Fixture tests; live verification pending"
    auth_modes: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ("darwin",)
    refresh_seconds: int = 300
    minimum_refresh_seconds: int = 300


@dataclass(frozen=True)
class IdentityBinding:
    subject_id: str | None = None
    account_id: str | None = None
    workspace_id: str | None = None
    organization_id: str | None = None
    project_id: str | None = None
    verified: bool = False

    @property
    def is_verified(self) -> bool:
        return self.verified and bool(self.subject_id)

    @property
    def key(self) -> tuple[str | None, ...]:
        return (
            self.subject_id,
            self.account_id,
            self.workspace_id,
            self.organization_id,
            self.project_id,
        )


@dataclass(frozen=True)
class ConnectionPreferences:
    refresh_seconds: int = 300
    experimental: bool = False
    notifications: bool = True
    order: int = 0
    executable: str | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class Connection:
    connection_id: str
    integration_id: str
    label: str = "Local context"
    generation: int = 0
    enabled: bool = True
    binding: IdentityBinding = field(default_factory=IdentityBinding)
    credential_ref: str | None = None
    preferences: ConnectionPreferences = field(default_factory=ConnectionPreferences)


@dataclass(frozen=True)
class QuotaWindow:
    kind: WindowKind = WindowKind.UNKNOWN
    instance_id: str | None = None
    duration_seconds: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    resets_at: datetime | None = None
    reset_semantics: str = "unknown"


@dataclass(frozen=True)
class Provenance:
    source_id: str
    source_kind: SourcePolicy
    adapter_version: str
    fetched_at: datetime
    event_at: datetime | None = None
    basis: ObservationBasis = ObservationBasis.REPORTED
    tool_version: str | None = None


@dataclass(frozen=True)
class QuotaObservation:
    connection_id: str
    generation: int
    bucket_key: str
    label: str
    provenance: Provenance
    binding: IdentityBinding = field(default_factory=IdentityBinding)
    metric: str = "allowance"
    kind: QuotaKind = QuotaKind.ALLOWANCE
    unit: Unit = Unit.PERCENT
    used: Decimal | None = None
    limit: Decimal | None = None
    remaining: Decimal | None = None
    used_percent: Decimal | None = None
    currency: str | None = None
    credit_unit: str | None = None
    window: QuotaWindow = field(default_factory=QuotaWindow)
    state: QuotaState = QuotaState.UNKNOWN
    availability: Availability = Availability.UNKNOWN
    model_ids: tuple[str, ...] = ()
    feature: str | None = None
    all_models: bool = False
    shared_pool_id: str | None = None
    sharing_evidence: str | None = None
    fresh_until: datetime | None = None

    def freshness(self, now: datetime) -> Freshness:
        reset = self.window.resets_at or self.window.ends_at
        if reset is not None and now >= reset:
            return Freshness.EXPIRED
        if self.provenance.event_at is None and self.provenance.basis == ObservationBasis.TELEMETRY:
            return Freshness.UNDATED
        if self.fresh_until is None:
            return Freshness.UNDATED
        return Freshness.FRESH if now < self.fresh_until else Freshness.STALE


@dataclass(frozen=True)
class UsageSnapshot:
    connection_id: str
    integration_id: str
    generation: int
    binding: IdentityBinding
    observations: tuple[QuotaObservation, ...]
    collected_at: datetime
    source_epoch: int = 0
    plan: str | None = None
    capabilities: tuple[Capability, ...] = ()


@dataclass(frozen=True)
class RefreshOutcome:
    health: ConnectionHealth = ConnectionHealth.DISCONNECTED
    attempted_at: datetime | None = None
    last_success_at: datetime | None = None
    error_code: str | None = None
    message: str | None = None
    retry_at: datetime | None = None


@dataclass(frozen=True)
class ConnectionState:
    connection: Connection
    snapshot: UsageSnapshot | None = None
    outcome: RefreshOutcome = field(default_factory=RefreshOutcome)
    loading: bool = False


class ActionKind(StrEnum):
    OPEN_URL = "open_url"
    OPEN_APP = "open_app"
    LOGIN = "login"
    HELP = "help"


@dataclass(frozen=True)
class ConnectionAction:
    action_id: str
    label: str
    kind: ActionKind
    target: str
    argv: tuple[str, ...] = ()
