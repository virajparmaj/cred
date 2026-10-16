"""Bounded, independent refresh scheduling with generation and retry guards."""

from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event, RLock

from credusage.domain.contracts import CollectionResult, CollectRequest
from credusage.domain.errors import CollectionError, Diagnostic, ErrorCode
from credusage.domain.models import ConnectionHealth, ConnectionState, RefreshOutcome
from credusage.infrastructure.clock import SystemClock

from .cache import mark_historical, merge_snapshot

logger = logging.getLogger("credusage.coordinator")

_HEALTH = {
    ErrorCode.AUTH_REQUIRED: ConnectionHealth.AUTH_REQUIRED,
    ErrorCode.AUTH_EXPIRED: ConnectionHealth.AUTH_REQUIRED,
    ErrorCode.UNSUPPORTED_AUTH_MODE: ConnectionHealth.UNSUPPORTED_AUTH,
    ErrorCode.COLLECTOR_RATE_LIMITED: ConnectionHealth.THROTTLED,
    ErrorCode.NETWORK_UNAVAILABLE: ConnectionHealth.TRANSPORT_FAILURE,
    ErrorCode.TIMEOUT: ConnectionHealth.TRANSPORT_FAILURE,
    ErrorCode.SOURCE_UNAVAILABLE: ConnectionHealth.SOURCE_UNAVAILABLE,
    ErrorCode.UNSUPPORTED_CAPABILITY: ConnectionHealth.SOURCE_UNAVAILABLE,
}


class RefreshCoordinator:
    def __init__(
        self,
        registry,
        connections,
        repository,
        *,
        clock=None,
        on_change=None,
        notifications=None,
        on_notification=None,
        workers=4,
        jitter=None,
        timeout_seconds=20,
        retry_store=None,
    ):
        self.registry = registry
        self.repository = repository
        self.clock = clock or SystemClock()
        self.on_change = on_change or (lambda states: None)
        self.notifications = notifications
        self.on_notification = on_notification or (lambda event: None)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="credusage")
        self._max_workers = workers
        self._jitter = jitter or (lambda seconds: random.uniform(0, min(30, seconds * 0.1)))
        self._timeout = timeout_seconds
        self.retry_store = retry_store
        self._lock = RLock()
        self._closed = False
        self._states = {}
        self._epochs = {}
        self._due = {}
        self._pending = {}
        self._inflight = {}
        self._source_retry = {}
        self._failures = {}
        self._fresh_deadlines = {}
        for index, connection in enumerate(connections):
            snapshot = self.repository.load(connection)
            self._states[connection.connection_id] = ConnectionState(connection, snapshot)
            if snapshot is not None:
                self._remember_freshness(snapshot)
            self._epochs[connection.connection_id] = 0
            self._due[connection.connection_id] = self.clock.monotonic() + index * 0.15
            if retry_store:
                saved = retry_store.get(self._source_key(connection))
                if saved:
                    from credusage.domain.normalization import utc_timestamp

                    target = utc_timestamp(saved["retry_at"])
                    remaining = max(0, (target - self.clock.now()).total_seconds())
                    if remaining:
                        scope = self._source_key(connection)
                        if saved.get("hard", True):
                            self._source_retry[scope] = self.clock.monotonic() + remaining
                        self._due[connection.connection_id] = self.clock.monotonic() + remaining
                        code = saved.get("code", "COLLECTOR_RATE_LIMITED")
                        try:
                            health = _HEALTH.get(
                                ErrorCode(code), ConnectionHealth.SOURCE_UNAVAILABLE
                            )
                        except ValueError:
                            health = ConnectionHealth.SOURCE_UNAVAILABLE
                        self._states[connection.connection_id] = ConnectionState(
                            connection,
                            snapshot,
                            RefreshOutcome(
                                health,
                                last_success_at=snapshot.collected_at if snapshot else None,
                                error_code=code,
                                message="Waiting for the next allowed source retry.",
                                retry_at=target,
                            ),
                        )

    def states(self):
        with self._lock:
            return tuple(self._states.values())

    def _source_key(self, connection):
        # Unidentified local profiles share an integration throttle, conservatively.
        context = connection.binding.key if connection.binding.is_verified else "local"
        return (connection.integration_id, context)

    def request_refresh(self, connection_id=None, *, reason="manual"):
        with self._lock:
            if self._closed:
                return
            keys = (connection_id,) if connection_id else tuple(self._states)
            for key in keys:
                if (
                    key in self._states
                    and key not in self._inflight
                    and self._states[key].connection.enabled
                ):
                    if reason not in (
                        "manual",
                        "push",
                        "configuration",
                        "source_changed",
                    ) and self.clock.monotonic() < self._due.get(key, 0):
                        continue
                    self._pending[key] = reason
        self.tick()

    def invalidate(self, connection_id):
        with self._lock:
            if connection_id not in self._states or self._closed:
                return
            self._epochs[connection_id] += 1
            state = self._states[connection_id]
            self._states[connection_id] = replace(state, snapshot=None)
            if state.connection.enabled:
                self._pending[connection_id] = "source_changed"
            else:
                self._pending.pop(connection_id, None)
        self.tick()

    def begin_login(self, connection_id):
        """A launched vendor login is pending, never authenticated by assumption."""
        with self._lock:
            state = self._states[connection_id]
            connection = replace(state.connection, generation=state.connection.generation + 1)
            self._epochs[connection_id] += 1
            self._pending.pop(connection_id, None)
            self._due[connection_id] = self.clock.monotonic() + 300
            self._states[connection_id] = ConnectionState(
                connection,
                outcome=RefreshOutcome(
                    ConnectionHealth.LOGIN_PENDING,
                    attempted_at=self.clock.now(),
                    message="Login opened in the vendor tool. Complete it there, then refresh.",
                ),
            )
        self._publish()
        return connection

    def update_connection(self, connection):
        with self._lock:
            previous = self._states.get(connection.connection_id)
            incompatible = previous is None or (
                previous.connection.generation != connection.generation
                or previous.connection.integration_id != connection.integration_id
                or previous.connection.binding != connection.binding
                or previous.connection.credential_ref != connection.credential_ref
                or previous.connection.preferences.executable != connection.preferences.executable
                or previous.connection.preferences.source_path != connection.preferences.source_path
                or previous.connection.preferences.experimental
                != connection.preferences.experimental
                or previous.connection.enabled != connection.enabled
            )
            if previous and incompatible:
                connection = replace(
                    connection,
                    generation=max(connection.generation, previous.connection.generation + 1),
                )
            if incompatible:
                self._epochs[connection.connection_id] = (
                    self._epochs.get(connection.connection_id, 0) + 1
                )
                self._states[connection.connection_id] = ConnectionState(connection)
                self._failures.pop(connection.connection_id, None)
            else:
                self._states[connection.connection_id] = replace(previous, connection=connection)
            self._due[connection.connection_id] = self.clock.monotonic()
            if connection.enabled:
                self._pending[connection.connection_id] = "configuration"
            else:
                self._pending.pop(connection.connection_id, None)
        self._publish()
        self.tick()
        return connection

    def tick(self):
        with self._lock:
            if self._closed:
                return
            now = self.clock.monotonic()
            self._expire_by_elapsed_time(now)
            for key, state in self._states.items():
                if (
                    state.connection.enabled
                    and key not in self._inflight
                    and now >= self._due.get(key, 0)
                ):
                    self._pending.setdefault(key, "periodic")
            occupied = {
                self._source_key(request.connection) for _, request in self._inflight.values()
            }
            for key in tuple(self._pending):
                if len(self._inflight) >= self._max_workers:
                    break
                state = self._states[key]
                if not state.connection.enabled:
                    self._pending.pop(key, None)
                    continue
                scope = self._source_key(state.connection)
                if key in self._inflight or scope in occupied:
                    continue
                if now < self._source_retry.get(scope, 0):
                    deadline = self._source_retry[scope]
                    self._states[key] = replace(
                        state,
                        outcome=replace(
                            state.outcome,
                            health=ConnectionHealth.THROTTLED,
                            retry_at=self.clock.now() + timedelta(seconds=deadline - now),
                            message="This source is cooling down; manual refresh respects its retry deadline.",
                        ),
                    )
                    continue
                reason = self._pending.pop(key)
                request = CollectRequest(
                    state.connection,
                    self.clock.now(),
                    now + self._timeout,
                    self._epochs[key],
                    reason,
                )
                self._states[key] = replace(state, loading=True)
                ready = Event()
                future = self._executor.submit(self._collect_ready, request, ready)
                self._inflight[key] = (future, request)
                occupied.add(scope)
                future.add_done_callback(lambda completed, k=key: self._completed(k, completed))
                ready.set()
        self._publish()

    def _collect_ready(self, request, ready):
        ready.wait()
        return self._collect(request)

    def _collect(self, request):
        try:
            connector = self.registry.get(request.connection.integration_id)
        except KeyError:
            return CollectionResult(
                request.connection.connection_id,
                request.connection.generation,
                request.source_epoch,
                diagnostic=Diagnostic(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "This integration is unavailable in this app version.",
                ),
            )
        if (
            connector.descriptor.source_policy.value == "experimental"
            and not request.connection.preferences.experimental
        ):
            return CollectionResult(
                request.connection.connection_id,
                request.connection.generation,
                request.source_epoch,
                diagnostic=Diagnostic(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Experimental collection is disabled for this connection.",
                ),
            )
        try:
            return connector.collect(request)
        except CollectionError as error:
            return CollectionResult(
                request.connection.connection_id,
                request.connection.generation,
                request.source_epoch,
                diagnostic=error.diagnostic,
            )
        except Exception:
            # Never expose upstream exception text, payloads, or auth fragments.
            return CollectionResult(
                request.connection.connection_id,
                request.connection.generation,
                request.source_epoch,
                diagnostic=Diagnostic(
                    ErrorCode.SOURCE_UNAVAILABLE,
                    "The source could not be read. See connection details and try again.",
                ),
            )

    def _completed(self, key, future):
        current = None
        with self._lock:
            entry = self._inflight.get(key)
            if entry is None or entry[0] is not future:
                return
            _, request = entry
            if self._closed or key not in self._states:
                self._inflight.pop(key, None)
                return
            state = self._states[key]
            if (
                request.connection.generation != state.connection.generation
                or request.source_epoch != self._epochs[key]
                or not state.connection.enabled
            ):
                self._states[key] = replace(state, loading=False)
            else:
                result = future.result()
                if not isinstance(result, CollectionResult):
                    result = CollectionResult(
                        key,
                        state.connection.generation,
                        request.source_epoch,
                        diagnostic=Diagnostic(
                            ErrorCode.SOURCE_SCHEMA_CHANGED,
                            "The collector returned an invalid result.",
                        ),
                    )
                if (
                    result.connection_id != key
                    or result.generation != state.connection.generation
                    or result.source_epoch != request.source_epoch
                ):
                    result = replace(
                        result,
                        diagnostic=Diagnostic(
                            ErrorCode.ACCOUNT_MISMATCH,
                            "The result belongs to an earlier connection context.",
                        ),
                    )
                current, diagnostic = self._accept(state, result)
        # Disk, log and notification I/O never hold the state lock needed by Cocoa.
        if current is not None:
            self._publish()
            self._persist(current, diagnostic)
            self._notify(state, current)
        with self._lock:
            if self._inflight.get(key, (None,))[0] is future:
                self._inflight.pop(key, None)
        self._publish()
        self.tick()

    def _persist(self, current, diagnostic):
        connection = current.connection
        try:
            if not diagnostic and current.snapshot is not None:
                self.repository.save(current.snapshot)
            if self.retry_store:
                scope = self._source_key(connection)
                if diagnostic:
                    hard = (
                        diagnostic.code == ErrorCode.COLLECTOR_RATE_LIMITED
                        or diagnostic.retry_after_seconds is not None
                    )
                    self.retry_store.set(
                        scope, current.outcome.retry_at, diagnostic.code.value, hard=hard
                    )
                else:
                    self.retry_store.clear(scope)
        except (OSError, ValueError, TypeError):
            logger.warning("integration=%s code=STORAGE_ERROR", connection.integration_id)
            with self._lock:
                key = connection.connection_id
                if self._states.get(key) == current and not diagnostic:
                    self._states[key] = replace(
                        current,
                        outcome=replace(
                            current.outcome,
                            error_code=ErrorCode.STORAGE_ERROR.value,
                            message="Current usage loaded; its saved cache could not be written.",
                        ),
                    )
        logger.info(
            "integration=%s code=%s retry=%s",
            connection.integration_id,
            current.outcome.error_code or "OK",
            current.outcome.retry_at.isoformat() if current.outcome.retry_at else "none",
        )

    def _notify(self, previous, current):
        if not self.notifications:
            return
        with self._lock:
            if self._closed or self._states.get(current.connection.connection_id) != current:
                return
        try:
            descriptor = self.registry.get(current.connection.integration_id).descriptor
            for event in self.notifications.evaluate(
                previous, current, descriptor, self.clock.now()
            ):
                with self._lock:
                    valid = (
                        not self._closed
                        and self._states.get(current.connection.connection_id) == current
                    )
                if valid:
                    self.on_notification(event)
        except (OSError, ValueError, KeyError):
            logger.warning("notification_state code=STORAGE_ERROR")

    def _accept(self, state, result):
        now, mono = self.clock.now(), self.clock.monotonic()
        key, connection = state.connection.connection_id, state.connection
        try:
            descriptor = self.registry.get(connection.integration_id).descriptor
        except KeyError:
            from credusage.domain.models import IntegrationDescriptor

            descriptor = IntegrationDescriptor(
                connection.integration_id, "other", connection.integration_id
            )
        diagnostic = result.diagnostic
        snapshot = state.snapshot
        if not diagnostic:
            try:
                if connection.binding.is_verified and result.binding != connection.binding:
                    raise ValueError("Wrong account")
                snapshot = merge_snapshot(snapshot, result, connection.integration_id, now)
                if snapshot is not None:
                    from credusage.domain.validation import validate_snapshot

                    validate_snapshot(snapshot)
            except (ValueError, OSError, TypeError):
                diagnostic = Diagnostic(
                    ErrorCode.ACCOUNT_MISMATCH,
                    "The source or stored account binding could not be verified.",
                )
                snapshot = mark_historical(state.snapshot, now)
        if diagnostic:
            failures = self._failures.get(key, 0) + 1
            self._failures[key] = failures
            delay = min(1800, 60 * 2 ** min(failures - 1, 5))
            if diagnostic.code in (
                ErrorCode.AUTH_REQUIRED,
                ErrorCode.AUTH_EXPIRED,
                ErrorCode.UNSUPPORTED_AUTH_MODE,
                ErrorCode.UNSUPPORTED_CAPABILITY,
            ):
                delay = 900
            delay = max(delay, diagnostic.retry_after_seconds or 0)
            deadline = mono + delay
            scope = self._source_key(connection)
            hard = (
                diagnostic.code == ErrorCode.COLLECTOR_RATE_LIMITED
                or diagnostic.retry_after_seconds is not None
            )
            if hard:
                self._source_retry[scope] = max(self._source_retry.get(scope, 0), deadline)
            outcome = RefreshOutcome(
                _HEALTH.get(diagnostic.code, ConnectionHealth.SOURCE_UNAVAILABLE),
                now,
                state.outcome.last_success_at,
                diagnostic.code.value,
                diagnostic.message,
                now + timedelta(seconds=delay),
            )
            snapshot = mark_historical(snapshot, now)
            self._due[key] = deadline
        else:
            self._failures.pop(key, None)
            self._source_retry.pop(self._source_key(connection), None)
            outcome = RefreshOutcome(ConnectionHealth.CONNECTED, now, now)
            interval = max(
                descriptor.minimum_refresh_seconds, connection.preferences.refresh_seconds
            )
            self._due[key] = mono + interval + self._jitter(interval)
        if snapshot is not None:
            self._remember_freshness(snapshot)
        current = ConnectionState(connection, snapshot, outcome, False)
        self._states[key] = current
        return current, diagnostic

    def _remember_freshness(self, snapshot):
        for item in snapshot.observations:
            key = (snapshot.connection_id, snapshot.generation, item.bucket_key)
            stamp = (
                item.provenance.source_id,
                item.provenance.fetched_at,
                item.provenance.event_at,
            )
            previous = self._fresh_deadlines.get(key)
            if item.fresh_until is not None and (previous is None or previous[0] != stamp):
                remaining = max(0, (item.fresh_until - self.clock.now()).total_seconds())
                self._fresh_deadlines[key] = (stamp, self.clock.monotonic() + remaining)

    def _expire_by_elapsed_time(self, monotonic_now):
        wall = self.clock.now()
        for key, state in tuple(self._states.items()):
            snapshot = state.snapshot
            if snapshot is None:
                continue
            observations = []
            changed = False
            for item in snapshot.observations:
                deadline = self._fresh_deadlines.get((key, snapshot.generation, item.bucket_key))
                expired = deadline is not None and monotonic_now >= deadline[1]
                future_read = item.provenance.fetched_at > wall
                if (
                    item.fresh_until is not None
                    and (expired or future_read)
                    and item.fresh_until > wall
                ):
                    item = replace(item, fresh_until=wall)
                    changed = True
                observations.append(item)
            if changed:
                self._states[key] = replace(
                    state, snapshot=replace(snapshot, observations=tuple(observations))
                )

    def _publish(self):
        self.on_change(self.states())

    def is_idle(self):
        with self._lock:
            now = self.clock.monotonic()
            eligible_pending = any(
                now >= self._source_retry.get(self._source_key(self._states[key].connection), 0)
                for key in self._pending
            )
            return not self._inflight and not eligible_pending

    def close(self):
        with self._lock:
            self._closed = True
            self._pending.clear()
            for key in self._epochs:
                self._epochs[key] += 1
        self.registry.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
