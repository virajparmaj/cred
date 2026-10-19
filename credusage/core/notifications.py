"""Durable transition notifications, never inferred from a countdown."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import RLock

from credusage.domain.models import (
    Availability,
    ConnectionHealth,
    Freshness,
    ObservationBasis,
    QuotaState,
    ValidationStatus,
)
from credusage.formatting import percentage
from credusage.infrastructure.storage import atomic_json, quarantine, read_json


@dataclass(frozen=True)
class Notification:
    title: str
    body: str


class NotificationPolicy:
    def __init__(self, root: Path, thresholds=(Decimal("80"), Decimal("95"))):
        self.path = root / "notification_state.json"
        self.thresholds = thresholds
        self._state = {}
        self._problems = {}
        self._lock = RLock()
        if self.path.exists():
            try:
                data = read_json(self.path)
                if data.get("schema_version") != 1 or not isinstance(data.get("episodes"), dict):
                    raise ValueError("Invalid notification state")
                for key, row in data["episodes"].items():
                    if not isinstance(key, str) or len(key) != 64 or not isinstance(row, dict):
                        raise ValueError("Invalid notification key")
                    level = Decimal(str(row.get("level")))
                    if (
                        not level.is_finite()
                        or level < 0
                        or level > 100
                        or type(row.get("exhausted")) is not bool
                    ):
                        raise ValueError("Invalid notification episode")
                problems = data.get("problems", {})
                if not isinstance(problems, dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in problems.items()
                ):
                    raise ValueError("Invalid connection notification state")
                self._state = data["episodes"]
                self._problems = problems
            except (ValueError, OSError, TypeError, ArithmeticError):
                quarantine(self.path)

    def _key(self, connection, item):
        # Rolling reset timestamps must not create a new episode.
        window = item.window.instance_id or "continuous"
        raw = repr(
            (
                connection.connection_id,
                connection.generation,
                item.binding.key,
                item.bucket_key,
                window,
            )
        )
        return sha256(raw.encode()).hexdigest()

    def evaluate(self, previous, current, descriptor, now):
        connection, snapshot = current.connection, current.snapshot
        problem_events = self._connection_events(previous, current, descriptor)
        if (
            not connection.preferences.notifications
            or snapshot is None
            or not snapshot.binding.is_verified
            or current.outcome.health != ConnectionHealth.CONNECTED
            or descriptor.validation != ValidationStatus.LIVE_VERIFIED
        ):
            return problem_events
        events = list(problem_events)
        with self._lock:
            for item in snapshot.observations:
                if (
                    item.freshness(now) != Freshness.FRESH
                    or item.provenance.basis
                    not in (ObservationBasis.REPORTED, ObservationBasis.TELEMETRY)
                    or item.binding != snapshot.binding
                    or item.availability != Availability.KNOWN
                ):
                    continue
                key = self._key(connection, item)
                value = item.used_percent
                exhausted = item.state == QuotaState.EXHAUSTED
                if value is None and item.state == QuotaState.UNKNOWN:
                    continue
                level = str(
                    max(
                        (t for t in self.thresholds if value is not None and value >= t),
                        default=Decimal("0"),
                    )
                )
                record = self._state.get(key)
                if not isinstance(record, dict):
                    # Seed initial reading without a startup quota alert.
                    self._state[key] = {"level": level, "exhausted": exhausted}
                    continue
                old_level = Decimal(str(record.get("level", "0")))
                was_exhausted = record.get("exhausted") is True
                if exhausted and not was_exhausted:
                    events.append(
                        Notification("Allowance exhausted", f"{connection.label}: {item.label}")
                    )
                elif value is not None and Decimal(level) > old_level:
                    events.append(
                        Notification(
                            "Allowance running low",
                            f"{connection.label}: {item.label} — {percentage(value)}% used",
                        )
                    )
                if (
                    was_exhausted
                    and item.state in (QuotaState.AVAILABLE, QuotaState.LOW)
                    and (value is None or value < 95)
                ):
                    events.append(
                        Notification(
                            "Allowance recovery confirmed", f"{connection.label}: {item.label}"
                        )
                    )
                if value is not None and old_level > 0 and value <= old_level - 5:
                    level = str(
                        max((t for t in self.thresholds if value >= t), default=Decimal("0"))
                    )
                elif Decimal(level) < old_level:
                    level = str(old_level)
                self._state[key] = {"level": level, "exhausted": exhausted}
            if len(self._state) > 4096:
                self._state = dict(list(self._state.items())[-4096:])
            self._save()
        return tuple(events)

    def _save(self):
        atomic_json(
            self.path, {"schema_version": 1, "episodes": self._state, "problems": self._problems}
        )

    def _connection_events(self, previous, current, descriptor):
        connection = current.connection
        if (
            not connection.preferences.notifications
            or not connection.enabled
            or descriptor.integration_id == "example.demo"
            or descriptor.validation == ValidationStatus.NOT_IMPLEMENTED
            or descriptor.source_policy.value == "dashboard_only"
        ):
            return ()
        raw = repr((connection.connection_id, connection.generation))
        key = sha256(raw.encode()).hexdigest()
        actionable = {"AUTH_REQUIRED", "AUTH_EXPIRED", "PERMISSION_DENIED", "SOURCE_SCHEMA_CHANGED"}
        code = current.outcome.error_code
        with self._lock:
            if (
                code in actionable
                and previous is not None
                and previous.outcome.last_success_at is not None
            ):
                if self._problems.get(key) == code:
                    return ()
                self._problems[key] = code
                self._save()
                return (
                    Notification(
                        "Connection needs attention",
                        f"{connection.label}: {code.replace('_',' ').lower()}",
                    ),
                )
            if current.outcome.health == ConnectionHealth.CONNECTED and key in self._problems:
                self._problems.pop(key, None)
                self._save()
        return ()
