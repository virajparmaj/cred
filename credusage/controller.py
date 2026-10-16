"""Application services shared by native UI and explicit command-line flows."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
from uuid import uuid4

from credusage import __version__
from credusage.config import ConfigStore, default_config, validate
from credusage.core.coordinator import RefreshCoordinator
from credusage.core.notifications import NotificationPolicy
from credusage.core.registry import ConnectorRegistry
from credusage.domain.models import ActionKind, Connection, IntegrationDescriptor
from credusage.infrastructure.clock import SystemClock
from credusage.infrastructure.retry_store import RetryStore
from credusage.infrastructure.snapshot_store import JSONSnapshotRepository
from credusage.infrastructure.storage import atomic_json
from credusage.logging_setup import setup_logging


def launch_argv(root: Path, demo=False):
    if getattr(sys, "frozen", False):
        args = [str(Path(sys.executable).resolve())]
    else:
        args = [str(Path(sys.executable).resolve()), "-m", "credusage"]
    args.extend(["--data-dir", str(root)])
    if demo:
        args.append("--demo")
    return args


class Controller:
    def __init__(
        self,
        root: Path,
        *,
        demo=False,
        clock=None,
        registry=None,
        action_runner=None,
        launch_service=None,
        integrations=None,
    ):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.clock = clock or SystemClock()
        self.store = ConfigStore(root, demo=demo)
        self.config = self.store.load()
        self.store.save(self.config)
        self.logger = setup_logging(root)
        self._events = Queue()
        self._closed = False
        if registry is None:
            from credusage.providers import built_in_connectors

            registry = ConnectorRegistry(
                built_in_connectors(self.clock, self._invalidate, root, demo)
            )
        self.registry = registry
        if integrations and any(
            key not in {d.integration_id for d in registry.descriptors()} for key in integrations
        ):
            raise ValueError("Unknown integration requested")
        from credusage.infrastructure.macos_actions import ActionRunner, LaunchAtLogin

        self.action_runner = action_runner or ActionRunner(root, demo=demo)
        self.launch_service = launch_service or LaunchAtLogin()
        self.notifications = NotificationPolicy(root, self.config.thresholds)
        connections = self.config.connections
        if integrations:
            connections = tuple(c for c in connections if c.integration_id in integrations)
        self.coordinator = RefreshCoordinator(
            registry,
            connections,
            JSONSnapshotRepository(root),
            clock=self.clock,
            notifications=self.notifications,
            on_notification=self._notification,
            retry_store=RetryStore(root),
        )

    def _invalidate(self, connection_id, account_changed=False):
        coordinator = getattr(self, "coordinator", None)
        if coordinator is not None:
            if account_changed:
                coordinator.invalidate(connection_id)
            else:
                coordinator.request_refresh(connection_id, reason="push")

    def _notification(self, event):
        if self.config.notifications and not self.config.demo:
            self._events.put((event.title, "CredUsage", event.body))

    def states(self):
        self.coordinator.tick()
        return self.coordinator.states()

    def refresh(self, connection_id=None, reason="manual"):
        self.coordinator.request_refresh(connection_id, reason=reason)

    def update_connection(self, connection):
        proposed = tuple(
            connection if c.connection_id == connection.connection_id else c
            for c in self.config.connections
        )
        if not any(c.connection_id == connection.connection_id for c in self.config.connections):
            proposed += (connection,)
        validate(replace(self.config, connections=proposed))
        connection = self.coordinator.update_connection(connection)
        connections = tuple(
            connection if c.connection_id == connection.connection_id else c for c in proposed
        )
        self.config = validate(replace(self.config, connections=connections))
        self.store.save(self.config)
        return connection

    def add_connection(self, integration_id, label="Local context"):
        return self.update_connection(Connection(str(uuid4()), integration_id, label))

    def disconnect(self, connection_id):
        connection = next(c for c in self.config.connections if c.connection_id == connection_id)
        # Disconnect is local: it never invokes vendor logout or deletes their credentials.
        from credusage.domain.models import IdentityBinding

        return self.update_connection(
            replace(
                connection,
                enabled=False,
                generation=connection.generation + 1,
                credential_ref=None,
                binding=IdentityBinding(),
            )
        )

    def pin(self, connection_id, bucket_key):
        self.set_preferences(pinned_connection_id=connection_id, pinned_bucket_key=bucket_key)

    def set_preferences(self, **changes):
        if "demo" in changes or "connections" in changes:
            raise ValueError("Use the connection controls or explicit demo launch")
        proposed = validate(replace(self.config, **changes))
        if proposed.launch_at_login != self.config.launch_at_login:
            if proposed.demo:
                raise ValueError("Demo mode cannot register a login item")
            self.launch_service.set_enabled(proposed.launch_at_login, launch_argv(self.root))
        self.store.save(proposed)
        self.config = proposed
        self.notifications.thresholds = proposed.thresholds

    def actions(self, connection_id):
        connection = next(c for c in self.config.connections if c.connection_id == connection_id)
        if self.config.demo:
            return ()
        try:
            return self.registry.get(connection.integration_id).actions(connection)
        except KeyError:
            return ()

    def run_action(self, connection_id, action):
        if action not in self.actions(connection_id):
            return "This action is unavailable in the current connection."
        message = self.action_runner.run(connection_id, action)
        if action.kind == ActionKind.LOGIN and "opened" in message.lower():
            connection = self.coordinator.begin_login(connection_id)
            connections = tuple(
                connection if c.connection_id == connection_id else c
                for c in self.config.connections
            )
            self.config = replace(self.config, connections=connections)
            self.store.save(self.config)
        return message

    def drain_notifications(self):
        messages = []
        while True:
            try:
                messages.append(self._events.get_nowait())
            except Empty:
                return tuple(messages)

    def export_diagnostics(self, path):
        records = []
        descriptors = {d.integration_id: d for d in self.registry.descriptors()}
        for state in self.coordinator.states():
            descriptor = descriptors.get(
                state.connection.integration_id,
                IntegrationDescriptor(
                    state.connection.integration_id, "other", "Unavailable integration"
                ),
            )
            observations = state.snapshot.observations if state.snapshot else ()
            records.append(
                {
                    "integration_id": descriptor.integration_id,
                    "adapter_version": descriptor.version,
                    "validation": descriptor.validation.value,
                    "source_policy": descriptor.source_policy.value,
                    "health": state.outcome.health.value,
                    "error_code": state.outcome.error_code,
                    "attempted_at": state.outcome.attempted_at,
                    "last_success_at": state.outcome.last_success_at,
                    "retry_at": state.outcome.retry_at,
                    "binding_verified": bool(state.snapshot and state.snapshot.binding.is_verified),
                    "buckets": [
                        {
                            "source_id": o.provenance.source_id,
                            "tool_version": o.provenance.tool_version,
                            "fetched_at": o.provenance.fetched_at,
                            "event_at": o.provenance.event_at,
                            "freshness": o.freshness(self.clock.now()).value,
                            "basis": o.provenance.basis.value,
                        }
                        for o in observations
                    ],
                }
            )
        atomic_json(
            Path(path),
            {
                "schema_version": 1,
                "app_version": __version__,
                "exported_at": self.clock.now(),
                "demo": self.config.demo,
                "integrations": records,
            },
        )
        return Path(path)

    def reset_preferences(self):
        old = self.config
        if old.launch_at_login and not old.demo:
            self.launch_service.set_enabled(False, ())
        # Retain IDs but invalidate generations; only app preferences change.
        fresh = default_config(demo=old.demo)
        by_product = {c.integration_id: c for c in fresh.connections}
        connections = (
            tuple(
                replace(
                    by_product.get(c.integration_id, c),
                    connection_id=c.connection_id,
                    generation=c.generation + 1,
                )
                for c in old.connections
            )
            or fresh.connections
        )
        self.config = replace(
            fresh, connections=connections, pinned_connection_id=connections[0].connection_id
        )
        self.store.save(self.config)
        self.notifications.thresholds = self.config.thresholds
        for connection in connections:
            self.coordinator.update_connection(connection)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.coordinator.close()
        self.action_runner.close()
        for handler in tuple(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
