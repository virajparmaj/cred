"""Account-bound snapshots. Unverified local readings are never restored as an account."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from credusage.domain.models import Connection, QuotaKind, UsageSnapshot
from credusage.domain.validation import validate_snapshot

from .storage import atomic_json, from_data, quarantine, read_json, to_data


class JSONSnapshotRepository:
    def __init__(self, root: Path):
        self.root = root / "snapshots"

    def _path(self, connection_id, integration_id, generation, binding):
        components = repr((connection_id, integration_id, generation, binding.key))
        return self.root / (sha256(components.encode()).hexdigest() + ".json")

    def save(self, snapshot: UsageSnapshot) -> None:
        if not snapshot.binding.is_verified:
            return
        self._validate(snapshot)
        path = self._path(
            snapshot.connection_id, snapshot.integration_id, snapshot.generation, snapshot.binding
        )
        atomic_json(path, {"schema_version": 1, "snapshot": to_data(snapshot)})

    def load(self, connection: Connection) -> UsageSnapshot | None:
        if not connection.binding.is_verified:
            return None
        path = self._path(
            connection.connection_id,
            connection.integration_id,
            connection.generation,
            connection.binding,
        )
        if not path.exists():
            return None
        try:
            data = read_json(path)
            if data.get("schema_version") != 1:
                raise ValueError("Unsupported snapshot schema")
            snapshot = from_data(UsageSnapshot, data["snapshot"])
            self._validate(snapshot)
            if (
                snapshot.connection_id != connection.connection_id
                or snapshot.integration_id != connection.integration_id
                or snapshot.generation != connection.generation
                or snapshot.binding != connection.binding
            ):
                raise ValueError("Snapshot binding mismatch")
            return snapshot
        except (ValueError, TypeError, KeyError, OSError):
            quarantine(path)
            return None

    @staticmethod
    def _validate(snapshot):
        validate_snapshot(snapshot)
        if len(snapshot.observations) > 1024:
            raise ValueError("Too many quota buckets")
        keys = set()
        for observation in snapshot.observations:
            if (
                observation.connection_id != snapshot.connection_id
                or observation.generation != snapshot.generation
                or observation.binding != snapshot.binding
            ):
                raise ValueError("Observation binding mismatch")
            if not observation.bucket_key or observation.bucket_key in keys:
                raise ValueError("Invalid or duplicate bucket identity")
            keys.add(observation.bucket_key)
            for value in (observation.used, observation.limit, observation.used_percent):
                if value is not None and (not value.is_finite() or value < 0):
                    raise ValueError("Invalid quota quantity")
            if observation.remaining is not None and not observation.remaining.is_finite():
                raise ValueError("Invalid remaining quantity")
            if observation.shared_pool_id and not observation.sharing_evidence:
                raise ValueError("Shared pools require explicit evidence")
