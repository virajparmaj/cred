"""Versioned app-owned preferences; no credentials and no import-time I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from credusage.domain.models import Connection, ConnectionPreferences
from credusage.infrastructure.storage import atomic_json, from_data, quarantine, read_json, to_data

INTEGRATION_IDS = (
    "openai.codex",
    "openai.chatgpt",
    "google.antigravity",
    "google.gemini_app",
    "anthropic.claude",
    "cursor.editor",
)
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Config:
    connections: tuple[Connection, ...]
    pinned_connection_id: str | None = None
    pinned_bucket_key: str | None = None
    thresholds: tuple[Decimal, ...] = (Decimal("80"), Decimal("95"))
    launch_at_login: bool = False
    notifications: bool = True
    demo: bool = False


def default_config(*, demo: bool = False) -> Config:
    ids = INTEGRATION_IDS if not demo else ("example.demo",)
    connections = tuple(
        Connection(
            str(uuid4()),
            integration,
            "Demo fixtures" if demo else "Local context",
            preferences=ConnectionPreferences(order=index),
        )
        for index, integration in enumerate(ids)
    )
    return Config(connections, connections[0].connection_id, demo=demo)


def validate(config: Config) -> Config:
    ids = set()
    if len(config.connections) > 64:
        raise ValueError("Too many connections")
    for connection in config.connections:
        if not connection.connection_id or len(connection.connection_id) > 256:
            raise ValueError("Invalid connection identifier")
        if connection.connection_id in ids:
            raise ValueError("Duplicate connection identifier")
        ids.add(connection.connection_id)
        if not re.fullmatch(r"[a-z][a-z0-9_]*[.][a-z][a-z0-9_.-]{0,96}", connection.integration_id):
            raise ValueError("Invalid integration identifier")
        if config.demo and connection.integration_id != "example.demo":
            raise ValueError("Live integrations cannot run in demo mode")
        if not config.demo and connection.integration_id == "example.demo":
            raise ValueError("Demo integration cannot run in live mode")
        if not connection.label.strip() or len(connection.label) > 120:
            raise ValueError("Account label must contain 1 to 120 characters")
        if any(ord(char) < 32 for char in connection.label):
            raise ValueError("Control characters in account label")
        if not 0 <= connection.generation <= 2**53:
            raise ValueError("Invalid connection generation")
        if not 300 <= connection.preferences.refresh_seconds <= 86400:
            raise ValueError("Refresh interval must be between 300 and 86400 seconds")
        for path in (connection.preferences.executable, connection.preferences.source_path):
            if path is not None and (not Path(path).is_absolute() or "\x00" in path):
                raise ValueError("Configured paths must be absolute")
    if not config.thresholds or tuple(sorted(set(config.thresholds))) != config.thresholds:
        raise ValueError("Thresholds must be unique and increasing")
    if any(not item.is_finite() or item <= 0 or item > 100 for item in config.thresholds):
        raise ValueError("Thresholds must be in (0, 100]")
    if config.pinned_connection_id is not None and config.pinned_connection_id not in ids:
        config = replace(config, pinned_connection_id=None, pinned_bucket_key=None)
    return config


def migrate_config(envelope: dict) -> dict:
    version = envelope.get("schema_version")
    if version == 0 and isinstance(envelope.get("config"), dict):
        # Only our explicitly versioned v0 preferences, never another app's state.
        return {"schema_version": 1, "config": envelope["config"]}
    if version != SCHEMA_VERSION:
        raise ValueError("Unsupported configuration schema")
    return envelope


class ConfigStore:
    def __init__(self, root: Path, *, demo: bool = False):
        self.root = root
        self.path = root / "config.json"
        self.demo = demo
        self.recovered_invalid = False

    def load(self) -> Config:
        if not self.path.exists():
            return default_config(demo=self.demo)
        try:
            payload = migrate_config(read_json(self.path))
            config = validate(from_data(Config, payload["config"]))
            if config.demo != self.demo:
                raise ValueError("Storage mode mismatch")
            return config
        except (ValueError, TypeError, KeyError, OSError):
            self.recovered_invalid = True
            quarantine(self.path)
            return default_config(demo=self.demo)

    def save(self, config: Config) -> None:
        config = validate(config)
        if config.demo != self.demo:
            raise ValueError("Storage mode mismatch")
        atomic_json(self.path, {"schema_version": SCHEMA_VERSION, "config": to_data(config)})
