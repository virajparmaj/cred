"""Documented Codex app-server usage reads; never starts a turn or renews auth."""

from __future__ import annotations

import inspect
import threading
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from credusage.domain.contracts import CollectionResult, DiscoveryResult
from credusage.domain.errors import CollectionError, Diagnostic, ErrorCode
from credusage.domain.models import (
    ActionKind,
    Availability,
    Capability,
    ConnectionAction,
    IdentityBinding,
    IntegrationDescriptor,
    ObservationBasis,
    Provenance,
    QuotaKind,
    QuotaObservation,
    QuotaState,
    QuotaWindow,
    SourcePolicy,
    Unit,
    WindowKind,
)
from credusage.domain.normalization import number, percent_used, utc_timestamp
from credusage.infrastructure.clock import SystemClock
from credusage.infrastructure.processes import (
    CommandRunner,
    JsonRpcProcess,
    failure,
    find_executable,
)

CAPABILITIES = (
    Capability.DISCOVERY,
    Capability.PLAN,
    Capability.QUOTAS,
    Capability.CREDITS,
    Capability.PUSH,
    Capability.DASHBOARD,
    Capability.LOGIN,
)


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def _text(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise ValueError("Invalid text")
    return " ".join(value.split())[:200] or fallback


def _duration(value: object) -> int | None:
    value = number(value)
    if value is None:
        return None
    seconds = value * 60
    if seconds != seconds.to_integral_value() or seconds > 315360000:
        raise ValueError("Invalid window duration")
    return int(seconds)


def _state(used: Decimal | None) -> QuotaState:
    return (
        QuotaState.UNKNOWN
        if used is None
        else (
            QuotaState.EXHAUSTED
            if used >= 100
            else QuotaState.LOW if used >= 80 else QuotaState.AVAILABLE
        )
    )


def parse_rate_limits(payload: object, request, *, fetched_at=None) -> CollectionResult:
    """Parse percentages as percentages. Map keys, units and windows remain distinct."""
    data = _object(payload)
    fetched_at = fetched_at or request.requested_at
    account_id = data.get("accountId")
    if account_id is not None and (not isinstance(account_id, str) or not account_id):
        raise ValueError("Invalid account identity")
    if (
        request.connection.binding.account_id
        and account_id != request.connection.binding.account_id
    ):
        raise failure(
            ErrorCode.ACCOUNT_MISMATCH,
            "Codex returned another account context; reconnect this source.",
        )
    binding = IdentityBinding(account_id=account_id)  # Tenant scope is not a subject identity.
    if data.get("rateLimitsByLimitId") is not None:
        sources = _object(data["rateLimitsByLimitId"])
    else:
        legacy = data.get("rateLimits")
        if legacy is None:
            raise ValueError("No supported rate-limit data")
        legacy = _object(legacy)
        sources = {_text(legacy.get("limitId"), "default"): legacy}
    observations = []
    plan = None
    for source_key, raw in sources.items():
        if not isinstance(source_key, str) or not source_key or len(source_key) > 200:
            raise ValueError("Invalid limit identifier")
        source = _object(raw)
        declared_id = source.get("limitId")
        if declared_id is not None and declared_id != source_key:
            raise ValueError("Inconsistent limit identifier")
        label = _text(source.get("limitName"), source_key)
        if source.get("planType") is not None:
            plan = _text(source["planType"], "Unknown plan")
        provenance = Provenance(
            "codex.app-server",
            SourcePolicy.DOCUMENTED,
            "1",
            fetched_at,
            None,
            ObservationBasis.REPORTED,
        )
        for slot in ("primary", "secondary"):
            if source.get(slot) is None:
                continue
            window = _object(source[slot])
            used = percent_used(window.get("usedPercent"))
            reset = utc_timestamp(window.get("resetsAt"))
            duration = _duration(window.get("windowDurationMins"))
            period = (
                f"{duration // 3600}h"
                if duration and duration % 3600 == 0
                else f"{duration // 60}m" if duration else slot
            )
            observations.append(
                QuotaObservation(
                    request.connection.connection_id,
                    request.connection.generation,
                    f"codex:{source_key}:{slot}",
                    f"{label} · {period}",
                    provenance,
                    binding=binding,
                    unit=Unit.PERCENT,
                    used_percent=used,
                    kind=QuotaKind.RATE_LIMIT,
                    window=QuotaWindow(
                        WindowKind.UNKNOWN,
                        None,
                        duration_seconds=duration,
                        resets_at=reset,
                        reset_semantics="provider_reported",
                    ),
                    state=_state(used),
                    availability=Availability.KNOWN if used is not None else Availability.UNKNOWN,
                    fresh_until=fetched_at + timedelta(minutes=10),
                )
            )
        if source.get("credits") is not None:
            credits = _object(source["credits"])
            for boolean_field in ("hasCredits", "isUnlimited"):
                if credits.get(boolean_field) is not None and not isinstance(
                    credits[boolean_field], bool
                ):
                    raise ValueError("Invalid credit flag")
            balance = number(credits.get("balance"), allow_negative=True)
            unlimited = credits.get("isUnlimited") is True
            observations.append(
                QuotaObservation(
                    request.connection.connection_id,
                    request.connection.generation,
                    f"codex:{source_key}:credits",
                    f"{label} · credits",
                    provenance,
                    binding=binding,
                    kind=QuotaKind.CREDIT,
                    unit=Unit.CREDITS,
                    remaining=balance,
                    credit_unit="Codex credits",
                    availability=(
                        Availability.UNLIMITED
                        if unlimited
                        else Availability.KNOWN if balance is not None else Availability.UNKNOWN
                    ),
                    state=(
                        QuotaState.AVAILABLE
                        if unlimited or balance is not None and balance > 0
                        else QuotaState.EXHAUSTED if balance is not None else QuotaState.UNKNOWN
                    ),
                    fresh_until=fetched_at + timedelta(minutes=10),
                )
            )
    return CollectionResult(
        request.connection.connection_id,
        request.connection.generation,
        request.source_epoch,
        binding=binding,
        observations=tuple(observations),
        capabilities=CAPABILITIES,
        plan=plan,
    )


class CodexConnector:
    descriptor = IntegrationDescriptor(
        "openai.codex",
        "openai",
        "Codex",
        capabilities=CAPABILITIES,
        auth_modes=("vendor_cli_chatgpt",),
        evidence="Official app-server protocol; parser and transport fixture tests. Account subject unavailable; live comparison pending.",
    )

    def __init__(self, clock=None, on_invalidate=None, *, transport_factory=None, runner=None):
        self.clock = clock or SystemClock()
        self.on_invalidate = on_invalidate
        self._factory = transport_factory or JsonRpcProcess
        self._runner = runner or CommandRunner()
        self._transport = None
        self._executable = None
        self._connections = set()
        self._lock = threading.Lock()

    def discover(self, context):
        path = find_executable("codex")
        return DiscoveryResult(
            bool(path),
            message=(
                "Codex app-server available."
                if path
                else "Install Codex CLI and authenticate with the vendor tool."
            ),
            capabilities=CAPABILITIES,
        )

    def _notification(self, method, params):
        if method in ("account/rateLimits/updated", "account/updated") and self.on_invalidate:
            # Push payloads can lack subject identity. They only schedule a new read.
            for connection_id in tuple(self._connections):
                account_changed = method == "account/updated"
                # Preserve the original single-argument callback interface while
                # allowing coordinators to distinguish identity invalidation
                # from a quota refresh trigger.
                try:
                    inspect.signature(self.on_invalidate).bind(connection_id, account_changed)
                except (TypeError, ValueError):
                    self.on_invalidate(connection_id)
                else:
                    self.on_invalidate(connection_id, account_changed)

    @staticmethod
    def _account_hint(payload):
        account = _object(payload).get("account")
        if account is None:
            raise failure(
                ErrorCode.AUTH_REQUIRED, "Sign in to Codex with the vendor CLI, then refresh."
            )
        account = _object(account)
        if account.get("type") != "chatgpt":
            raise failure(
                ErrorCode.UNSUPPORTED_AUTH_MODE,
                "Codex subscription allowances require ChatGPT sign-in; API-key mode has no supported allowance source.",
            )
        # A change is useful evidence of a race; equality never verifies identity.
        return tuple(account.get(field) for field in ("type", "email", "planType", "accountId"))

    def collect(self, request):
        with self._lock:
            try:
                executable = find_executable("codex", request.connection.preferences.executable)
                if not executable:
                    raise failure(
                        ErrorCode.SOURCE_UNAVAILABLE,
                        "Codex CLI is unavailable. Install it or select its executable in Settings.",
                    )
                if self._transport is None or executable != self._executable:
                    if self._transport:
                        self._transport.close()
                    self._executable = executable
                    self._transport = self._factory(
                        (executable, "app-server", "--listen", "stdio://"),
                        on_notification=self._notification,
                    )
                self._connections.add(request.connection.connection_id)
                transport = self._transport
                transport.start(request.deadline_monotonic)
                epoch = transport.epoch
                before = transport.request(
                    "account/read", {"refreshToken": False}, request.deadline_monotonic
                )
                hint = self._account_hint(before)
                raw = transport.request("account/rateLimits/read", {}, request.deadline_monotonic)
                after = transport.request(
                    "account/read", {"refreshToken": False}, request.deadline_monotonic
                )
                if hint != self._account_hint(after) or epoch != transport.epoch:
                    raise failure(
                        ErrorCode.ACCOUNT_MISMATCH,
                        "Codex account context changed during collection. Refresh again.",
                    )
                result = parse_rate_limits(raw, request, fetched_at=self.clock.now())
                version = getattr(transport, "tool_version", None)
                observations = tuple(
                    replace(item, provenance=replace(item.provenance, tool_version=version))
                    for item in result.observations
                )
                return replace(result, observations=observations)
            except CollectionError:
                raise
            except (ValueError, TypeError, KeyError, OverflowError):
                raise failure(
                    ErrorCode.SOURCE_SCHEMA_CHANGED,
                    "Codex returned an unsupported usage response. No new usage was accepted.",
                ) from None

    def actions(self, connection):
        return (
            ConnectionAction(
                "login",
                "Sign in to Codex…",
                ActionKind.LOGIN,
                find_executable("codex", connection.preferences.executable) or "codex",
                ("login",),
            ),
            ConnectionAction(
                "dashboard",
                "Open Codex usage",
                ActionKind.OPEN_URL,
                "https://chatgpt.com/codex/settings/usage",
            ),
            ConnectionAction(
                "help",
                "Codex sign-in help",
                ActionKind.OPEN_URL,
                "https://developers.openai.com/codex/auth",
            ),
        )

    def close(self):
        transport = self._transport
        if transport:
            transport.close()
