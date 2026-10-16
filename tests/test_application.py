"""Application-level checks using isolated data and passive fixture sources."""

import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from credusage.config import ConfigStore, default_config
from credusage.controller import Controller
from credusage.core.registry import ConnectorRegistry
from credusage.domain.contracts import CollectionResult
from credusage.domain.models import (
    ActionKind,
    ConnectionAction,
    ConnectionHealth,
    IntegrationDescriptor,
)


def cli(root, *arguments, payload=None):
    return subprocess.run(
        [sys.executable, "-B", "-m", "credusage", "--data-dir", str(root), *arguments],
        input=payload,
        capture_output=True,
        text=True,
        timeout=15,
    )


def settle(controller):
    deadline = time.monotonic() + 3
    while not controller.coordinator.is_idle() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert controller.coordinator.is_idle()


def test_self_check_has_no_runtime_side_effects(tmp_path):
    result = cli(tmp_path, "--self-check")
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["assets_present"] and report["core_without_appkit"]
    assert list(tmp_path.iterdir()) == []


def test_cli_demo_is_isolated_and_reports_heterogeneous_buckets(tmp_path):
    result = cli(tmp_path, "--demo", "--once")
    assert result.returncode == 0, result.stderr
    states = json.loads(result.stdout)
    assert len(states) == 1 and states[0]["connection"]["integration_id"] == "example.demo"
    observations = states[0]["snapshot"]["observations"]
    assert len(observations) == 5
    credit = next(item for item in observations if item["unit"] == "credits")
    assert credit["remaining"] == "12.123456789123456789"
    assert (tmp_path / "demo/config.json").exists()
    assert not (tmp_path / "config.json").exists()
    assert not (tmp_path / "demo/notification_state.json").exists()


def test_cli_connection_discovery_and_bounded_ingestion_roundtrip(tmp_path):
    result = cli(tmp_path, "--list-connections")
    assert result.returncode == 0
    connection = next(
        c for c in json.loads(result.stdout) if c["integration_id"] == "anthropic.claude"
    )
    payload = json.dumps(
        {
            "rate_limits": {"five_hour": {"used_percentage": 0.5}},
            "access_token": "excluded-secret",
            "transcript_path": "private-path",
        }
    )
    ingest = cli(
        tmp_path,
        "ingest-claude-statusline",
        "--connection",
        connection["connection_id"],
        payload=payload,
    )
    assert ingest.returncode == 0, ingest.stderr
    result = cli(tmp_path, "--once", "--integration", "anthropic.claude")
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)[0]
    assert state["snapshot"]["observations"][0]["used_percent"] == "0.5"
    assert state["snapshot"]["observations"][0]["provenance"]["event_at"] is None
    assert state["snapshot"]["binding"]["verified"] is False
    for path in tmp_path.rglob("*.json"):
        assert "excluded-secret" not in path.read_text() and "private-path" not in path.read_text()


def test_cli_ingestion_refuses_missing_disabled_or_demo_context(tmp_path):
    payload = '{"rate_limits":{"five_hour":{"used_percentage":50}}}'
    assert (
        cli(
            tmp_path, "ingest-claude-statusline", "--connection", "missing", payload=payload
        ).returncode
        == 2
    )
    cli(tmp_path, "--list-connections")
    store = ConfigStore(tmp_path)
    config = store.load()
    connection = next(c for c in config.connections if c.integration_id == "anthropic.claude")
    store.save(
        replace(
            config,
            connections=tuple(
                replace(c, enabled=False) if c == connection else c for c in config.connections
            ),
        )
    )
    result = cli(
        tmp_path,
        "ingest-claude-statusline",
        "--connection",
        connection.connection_id,
        payload=payload,
    )
    assert result.returncode == 2
    assert not (tmp_path / "telemetry").exists()
    assert (
        cli(
            tmp_path,
            "--demo",
            "ingest-claude-statusline",
            "--connection",
            "missing",
            payload=payload,
        ).returncode
        == 2
    )


def test_unknown_integration_cli_fails_without_starting_any_collector(tmp_path):
    assert cli(tmp_path, "--demo", "--once", "--integration", "openai.codex").returncode == 2


def test_diagnostics_exclude_labels_ids_and_values(tmp_path):
    controller = Controller(tmp_path / "demo", demo=True)
    try:
        controller.refresh()
        settle(controller)
        connection = controller.config.connections[0]
        controller.update_connection(replace(connection, label="private label secret@example.test"))
        settle(controller)
        path = controller.export_diagnostics(tmp_path / "diagnostics.json")
        text = path.read_text()
        assert "secret@example.test" not in text and connection.connection_id not in text
        assert "fixture-subject" not in text and "fixture-account" not in text
        assert "12.123456789123456789" not in text
        assert json.loads(text)["app_version"] == "0.1.0"
    finally:
        controller.close()


def test_disconnect_and_reset_reject_old_generation_and_keep_vendor_owned_state(tmp_path):
    controller = Controller(tmp_path / "demo", demo=True)
    vendor = tmp_path / "vendor-auth.json"
    vendor.write_text("externally-owned")
    try:
        controller.refresh()
        settle(controller)
        original = controller.config.connections[0]
        controller.disconnect(original.connection_id)
        assert not controller.config.connections[0].enabled
        assert controller.states()[0].snapshot is None
        controller.reset_preferences()
        assert controller.config.connections[0].enabled
        assert controller.config.connections[0].generation > original.generation
        assert vendor.read_text() == "externally-owned"
        settle(controller)
    finally:
        controller.close()


def test_launching_login_marks_pending_until_revalidated(tmp_path):
    action = ConnectionAction("login", "Login", ActionKind.LOGIN, "/fake/codex", ("login",))

    class Connector:
        descriptor = IntegrationDescriptor("openai.codex", "openai", "Codex")

        def collect(self, request):
            return CollectionResult(
                request.connection.connection_id,
                request.connection.generation,
                request.source_epoch,
            )

        def actions(self, connection):
            return (action,)

        def close(self):
            pass

    class Runner:
        def run(self, connection_id, action):
            return "Codex login opened in Terminal. Complete it there, then refresh."

        def close(self):
            pass

    controller = Controller(
        tmp_path,
        registry=ConnectorRegistry((Connector(),)),
        action_runner=Runner(),
        integrations=("openai.codex",),
    )
    try:
        connection = next(
            c for c in controller.config.connections if c.integration_id == "openai.codex"
        )
        controller.run_action(connection.connection_id, action)
        assert controller.coordinator.states()[0].outcome.health == ConnectionHealth.LOGIN_PENDING
        assert controller.coordinator.states()[0].snapshot is None
        assert controller.coordinator.states()[0].connection.generation > connection.generation
        controller.refresh(connection.connection_id)
        settle(controller)
        assert controller.coordinator.states()[0].outcome.health == ConnectionHealth.CONNECTED
    finally:
        controller.close()


def test_default_settings_preserve_zero_order():
    # Exercise the real field construction helper using a minimal native boundary.
    from credusage.ui.settings import SettingsWindow

    class Field:
        def initWithFrame_(self, frame):
            return self

        def setStringValue_(self, value):
            self.value = value

    class Fields:
        @staticmethod
        def alloc():
            return Field()

    class Native:
        NSTextField = Fields

    class Parent:
        def addSubview_(self, field):
            pass

    window = object.__new__(SettingsWindow)
    window.A = Native
    window._rect = lambda *args: args
    assert window.field(Parent(), 0, 0).value == "0"


def test_invalid_unverified_quota_never_reaches_presentation(tmp_path):
    from test_core import observation, snapshot

    from credusage.domain.models import Connection
    from credusage.domain.validation import validate_snapshot

    connection = Connection("one", "openai.codex")
    original = snapshot(connection)
    invalid = replace(
        original, observations=(replace(observation(connection), used_percent=Decimal("NaN")),)
    )
    with pytest.raises(ValueError):
        validate_snapshot(invalid)
