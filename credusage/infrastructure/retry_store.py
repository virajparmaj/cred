"""Persist cooldowns as UTC; re-evaluate them against monotonic time at startup."""

from hashlib import sha256
from threading import RLock

from credusage.domain.normalization import utc_timestamp

from .storage import atomic_json, quarantine, read_json


class RetryStore:
    def __init__(self, root):
        self.path = root / "refresh_state.json"
        self.entries = {}
        self._lock = RLock()
        if self.path.exists():
            try:
                data = read_json(self.path)
                if data.get("schema_version") != 1 or not isinstance(data.get("sources"), dict):
                    raise ValueError("Invalid cooldown state")
                self.entries = data["sources"]
                for row in self.entries.values():
                    if (
                        not isinstance(row, dict)
                        or utc_timestamp(row.get("retry_at")) is None
                        or not isinstance(row.get("code"), str)
                        or type(row.get("hard", True)) is not bool
                    ):
                        raise ValueError("Invalid cooldown record")
            except (OSError, ValueError, TypeError):
                quarantine(self.path)
                self.entries = {}

    def _key(self, scope):
        return sha256(repr(scope).encode()).hexdigest()

    def get(self, scope):
        with self._lock:
            row = self.entries.get(self._key(scope))
            return dict(row) if row else None

    def set(self, scope, retry_at, code, *, hard=True):
        with self._lock:
            self.entries[self._key(scope)] = {
                "retry_at": retry_at.isoformat(),
                "code": code,
                "hard": hard,
            }
            self._save()

    def clear(self, scope):
        with self._lock:
            if self.entries.pop(self._key(scope), None) is not None:
                self._save()

    def _save(self):
        atomic_json(self.path, {"schema_version": 1, "sources": self.entries})
