"""Behavioral regression tests for isolation, time, scheduling and honest state."""

import json
import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from credusage.config import ConfigStore, default_config, validate
from credusage.core.cache import merge_snapshot
from credusage.core.coordinator import RefreshCoordinator
from credusage.core.notifications import NotificationPolicy
from credusage.core.registry import ConnectorRegistry
from credusage.domain.contracts import CollectionResult, Completeness
from credusage.domain.errors import Diagnostic, ErrorCode
from credusage.domain.models import (
    Availability,
    Connection,
    ConnectionHealth,
    ConnectionPreferences,
    ConnectionState,
    Freshness,
    IdentityBinding,
    IntegrationDescriptor,
    ObservationBasis,
    Provenance,
    QuotaObservation,
    QuotaState,
    QuotaWindow,
    RefreshOutcome,
    SourcePolicy,
    Unit,
    UsageSnapshot,
    ValidationStatus,
    WindowKind,
)
from credusage.domain.normalization import (
    count_percentage,
    fraction_remaining,
    fraction_used,
    number,
    percent_used,
    retry_after,
    source_money,
    utc_timestamp,
)
from credusage.infrastructure.retry_store import RetryStore
from credusage.infrastructure.snapshot_store import JSONSnapshotRepository
from credusage.infrastructure.storage import atomic_json, from_data, read_json, to_data
from credusage.logging_setup import RedactingFormatter

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
BINDING = IdentityBinding(subject_id="subject-a", account_id="tenant-a", verified=True)


class FakeClock:
    def __init__(self):
        self.wall = NOW
        self.mono = 1000.0

    def now(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


def observation(
    connection,
    percent="40",
    *,
    now=NOW,
    bucket="coding",
    reset=None,
    basis=ObservationBasis.REPORTED,
    event_at=NOW,
    window_id=None,
):
    return QuotaObservation(
        connection.connection_id,
        connection.generation,
        bucket,
        "Coding",
        Provenance("test.authoritative", SourcePolicy.DOCUMENTED, "1", now, event_at, basis),
        binding=connection.binding,
        used_percent=Decimal(percent) if percent is not None else None,
        availability=Availability.KNOWN if percent is not None else Availability.UNKNOWN,
        state=(
            QuotaState.EXHAUSTED
            if percent is not None and Decimal(percent) >= 100
            else QuotaState.AVAILABLE
        ),
        window=QuotaWindow(WindowKind.ROLLING, window_id, resets_at=reset),
        fresh_until=now + timedelta(minutes=10),
    )


def snapshot(connection, percent="40", **kwargs):
    return UsageSnapshot(
        connection.connection_id,
        connection.integration_id,
        connection.generation,
        connection.binding,
        (observation(connection, percent, **kwargs),),
        NOW,
    )


def state(connection, percent="40", **kwargs):
    return ConnectionState(
        connection,
        snapshot(connection, percent, **kwargs),
        RefreshOutcome(ConnectionHealth.CONNECTED, NOW, NOW),
    )


def wait_for(check, coordinator=None):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if coordinator:
            coordinator.tick()
        if check():
            return
        time.sleep(0.005)
    raise AssertionError("Background work did not settle")


@pytest.mark.parametrize(
    "raw,expected", [(0, "0"), (0.5, "0.5"), (1, "1"), (100, "100"), (125, "125")]
)
def test_percent_is_never_guessed_or_clamped(raw, expected):
    assert percent_used(raw) == Decimal(expected)


@pytest.mark.parametrize("bad", [True, False, "NaN", "Infinity", float("nan"), [], {}, "n/a", -1])
def test_invalid_quantity_rejected(bad):
    with pytest.raises(ValueError):
        number(bad)


def test_explicit_source_unit_contracts():
    assert fraction_used(1) == 100
    assert fraction_remaining(0.25) == 75
    assert count_percentage(2, 4) == 50
    assert count_percentage(2, 0) is None
    assert source_money(1250, scale=Decimal(100), currency="USD") == Decimal("12.5")
    assert number(None) is None


def test_timestamp_awareness_and_retry_after():
    assert utc_timestamp("2026-10-16T07:00:00-05:00") == NOW
    with pytest.raises(ValueError):
        utc_timestamp("2026-10-16T12:00:00")
    assert retry_after("120", NOW) == 120
    assert retry_after("Fri, 16 Oct 2026 12:02:00 GMT", NOW) == 120
    assert retry_after("unknown", NOW) is None


def test_lossless_snapshot_restart_does_not_reage(tmp_path):
    connection = Connection("one", "openai.codex", binding=BINDING)
    repo = JSONSnapshotRepository(tmp_path)
    original = snapshot(connection, "0.123456789123456789")
    repo.save(original)
    restored = repo.load(connection)
    assert restored == original
    assert restored.observations[0].used_percent == Decimal("0.123456789123456789")
    assert restored.observations[0].freshness(NOW + timedelta(hours=1)) == Freshness.STALE
    assert next(repo.root.glob("*.json")).stat().st_mode & 0o777 == 0o600


def test_wrong_subject_and_unverified_cache_are_not_restored(tmp_path):
    repo = JSONSnapshotRepository(tmp_path)
    connection = Connection("../escape", "openai.codex", binding=BINDING)
    repo.save(snapshot(connection))
    assert repo.load(replace(connection, binding=replace(BINDING, subject_id="other"))) is None
    unknown = replace(connection, binding=IdentityBinding())
    repo.save(snapshot(unknown))
    assert repo.load(unknown) is None
    assert len(tuple(repo.root.glob("*.json"))) == 1
    assert not (tmp_path.parent / "escape").exists()


def test_corrupt_snapshot_is_preserved_for_diagnosis(tmp_path):
    repo = JSONSnapshotRepository(tmp_path)
    connection = Connection("one", "openai.codex", binding=BINDING)
    repo.save(snapshot(connection))
    file = next(repo.root.glob("*.json"))
    file.write_text("{broken")
    assert repo.load(connection) is None
    assert list(repo.root.glob("*.invalid-*"))


def test_config_recovery_and_migration(tmp_path):
    store = ConfigStore(tmp_path)
    original = store.load()
    store.save(original)
    assert store.load() == original
    payload = read_json(store.path)
    payload["schema_version"] = 0
    atomic_json(store.path, payload)
    assert store.load() == original
    payload["schema_version"] = 999
    atomic_json(store.path, payload)
    recovered = store.load()
    assert store.recovered_invalid
    assert recovered != original
    assert list(tmp_path.glob("config.json.invalid-*"))


def test_invalid_config_booleans_and_demo_cannot_cross_storage(tmp_path):
    store = ConfigStore(tmp_path)
    config = store.load()
    payload = to_data(config)
    payload["connections"][0]["generation"] = True
    atomic_json(store.path, {"schema_version": 1, "config": payload})
    assert store.load().connections[0].generation == 0
    with pytest.raises(ValueError):
        store.save(default_config(demo=True))


def test_partial_merge_preserves_age_complete_retires_buckets():
    connection = Connection("one", "openai.codex", binding=BINDING)
    old = snapshot(connection)
    second = observation(connection, "60", bucket="weekly", now=NOW + timedelta(minutes=1))
    partial = CollectionResult("one", 0, 0, BINDING, (second,), Completeness.PARTIAL)
    merged = merge_snapshot(old, partial, "openai.codex", NOW + timedelta(minutes=1))
    assert len(merged.observations) == 2
    assert merged.observations[0].provenance.fetched_at == NOW
    complete = replace(partial, completeness=Completeness.COMPLETE)
    assert [
        o.bucket_key for o in merge_snapshot(merged, complete, "openai.codex", NOW).observations
    ] == ["weekly"]
    removed = replace(partial, observations=(), removed_keys=("coding",))
    assert [
        o.bucket_key for o in merge_snapshot(merged, removed, "openai.codex", NOW).observations
    ] == ["weekly"]


def test_unverified_partial_does_not_merge_and_units_cannot_mix():
    unknown = Connection("one", "openai.codex")
    old = snapshot(unknown)
    partial = CollectionResult(
        "one",
        0,
        0,
        observations=(observation(unknown, bucket="weekly"),),
        completeness=Completeness.PARTIAL,
    )
    assert len(merge_snapshot(old, partial, "openai.codex", NOW).observations) == 1
    known = replace(unknown, binding=BINDING)
    conflict = CollectionResult(
        "one", 0, 0, BINDING, (replace(observation(known), unit=Unit.TOKENS),)
    )
    with pytest.raises(ValueError):
        merge_snapshot(snapshot(known), conflict, "openai.codex", NOW)


def test_reset_expiration_is_per_bucket_and_never_restores_allowance():
    connection = Connection("one", "openai.codex")
    short = observation(connection, "80", reset=NOW + timedelta(seconds=5))
    weekly = observation(connection, "50", bucket="weekly", reset=NOW + timedelta(days=5))
    later = NOW + timedelta(seconds=6)
    assert short.freshness(later) == Freshness.EXPIRED
    assert short.used_percent == 80
    assert weekly.freshness(later) == Freshness.FRESH
    telemetry = replace(
        weekly,
        provenance=replace(weekly.provenance, basis=ObservationBasis.TELEMETRY, event_at=None),
    )
    assert telemetry.freshness(NOW) == Freshness.UNDATED


class FakeConnector:
    def __init__(self, integration="openai.codex", collect=None):
        self.descriptor = IntegrationDescriptor(integration, "test", integration)
        self.calls = []
        self.collect_fn = collect

    def collect(self, request):
        self.calls.append(request)
        if self.collect_fn:
            return self.collect_fn(request)
        return CollectionResult(
            request.connection.connection_id,
            request.connection.generation,
            request.source_epoch,
            request.connection.binding,
            (observation(request.connection),),
        )

    def close(self):
        pass


def coordinator(tmp_path, connectors, connections, **kwargs):
    return RefreshCoordinator(
        ConnectorRegistry(tuple(connectors)),
        connections,
        JSONSnapshotRepository(tmp_path),
        jitter=lambda _: 0,
        **kwargs,
    )


def test_one_inflight_and_manual_requests_coalesce(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def slow(request):
        entered.set()
        release.wait(2)
        return CollectionResult(
            request.connection.connection_id, request.connection.generation, request.source_epoch
        )

    connector = FakeConnector(collect=slow)
    coord = coordinator(tmp_path, [connector], [Connection("one", "openai.codex")])
    try:
        coord.request_refresh()
        assert entered.wait(1)
        for _ in range(10):
            coord.request_refresh(reason="manual")
        assert len(connector.calls) == 1
        release.set()
        wait_for(coord.is_idle)
        assert len(connector.calls) == 1
    finally:
        release.set()
        coord.close()


def test_four_workers_and_slow_source_does_not_block_others(tmp_path):
    release = threading.Event()
    active = 0
    maximum = 0
    lock = threading.Lock()

    def slow(request):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        release.wait(2)
        with lock:
            active -= 1
        return CollectionResult(
            request.connection.connection_id, request.connection.generation, request.source_epoch
        )

    connectors = [FakeConnector("p" + str(i), slow) for i in range(6)]
    connections = [Connection(str(i), "p" + str(i)) for i in range(6)]
    coord = coordinator(tmp_path, connectors, connections)
    try:
        coord.request_refresh()
        wait_for(lambda: sum(len(c.calls) for c in connectors) == 4)
        assert maximum == 4
        assert len([s for s in coord.states() if s.loading]) == 4
        release.set()
        wait_for(coord.is_idle)
        assert sum(len(c.calls) for c in connectors) == 6
    finally:
        release.set()
        coord.close()


def test_late_old_generation_and_disconnect_are_rejected(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def slow(request):
        entered.set()
        release.wait(2)
        return CollectionResult(
            request.connection.connection_id,
            request.connection.generation,
            request.source_epoch,
            request.connection.binding,
            (observation(request.connection, "99"),),
        )

    connector = FakeConnector(collect=slow)
    connection = Connection("one", "openai.codex", binding=BINDING)
    coord = coordinator(tmp_path, [connector], [connection])
    try:
        coord.request_refresh()
        assert entered.wait(1)
        updated = coord.update_connection(replace(connection, enabled=False))
        assert updated.generation > connection.generation
        release.set()
        wait_for(coord.is_idle)
        assert coord.states()[0].snapshot is None
        assert not coord.states()[0].connection.enabled
    finally:
        release.set()
        coord.close()


def test_retry_after_survives_restart_and_wall_clock_jump(tmp_path):
    clock = FakeClock()

    def throttled(request):
        return CollectionResult(
            request.connection.connection_id,
            request.connection.generation,
            request.source_epoch,
            diagnostic=Diagnostic(ErrorCode.COLLECTOR_RATE_LIMITED, "Try later", 600),
        )

    connector = FakeConnector(collect=throttled)
    connection = Connection("one", "openai.codex")
    coord = coordinator(
        tmp_path, [connector], [connection], clock=clock, retry_store=RetryStore(tmp_path)
    )
    coord.request_refresh()
    wait_for(coord.is_idle)
    for _ in range(4):
        coord.request_refresh()
    assert len(connector.calls) == 1
    clock.wall += timedelta(days=1)
    coord.request_refresh()
    assert len(connector.calls) == 1  # in-process scheduling uses monotonic time
    coord.close()
    restarted_clock = FakeClock()
    restarted_clock.advance(100)
    other = FakeConnector()
    restarted = coordinator(
        tmp_path, [other], [connection], clock=restarted_clock, retry_store=RetryStore(tmp_path)
    )
    try:
        restarted.request_refresh()
        assert not other.calls
        assert restarted.states()[0].outcome.retry_at == NOW + timedelta(seconds=600)
        restarted_clock.advance(501)
        restarted.tick()
        wait_for(restarted.is_idle)
        assert len(other.calls) == 1
    finally:
        restarted.close()


def test_fallback_keeps_error_and_old_observation_age(tmp_path):
    clock = FakeClock()
    connector = FakeConnector()
    coord = coordinator(
        tmp_path, [connector], [Connection("one", "openai.codex", binding=BINDING)], clock=clock
    )
    try:
        coord.request_refresh()
        wait_for(coord.is_idle)
        old = coord.states()[0].snapshot
        connector.collect_fn = lambda r: CollectionResult(
            r.connection.connection_id,
            r.connection.generation,
            r.source_epoch,
            diagnostic=Diagnostic(ErrorCode.AUTH_EXPIRED, "Sign in again"),
        )
        clock.advance(30)
        coord.request_refresh()
        wait_for(coord.is_idle)
        current = coord.states()[0]
        assert current.outcome.health == ConnectionHealth.AUTH_REQUIRED
        assert (
            current.snapshot.observations[0].provenance.fetched_at
            == old.observations[0].provenance.fetched_at
        )
        assert current.snapshot.observations[0].freshness(clock.now()) == Freshness.STALE
    finally:
        coord.close()


def test_snapshot_persistence_happens_on_worker_not_main(tmp_path):
    class Repo(JSONSnapshotRepository):
        thread = None

        def save(self, value):
            self.thread = threading.current_thread().name
            super().save(value)

    repo = Repo(tmp_path)
    connector = FakeConnector()
    coord = RefreshCoordinator(
        ConnectorRegistry((connector,)), [Connection("one", "openai.codex", binding=BINDING)], repo
    )
    try:
        coord.request_refresh()
        wait_for(coord.is_idle)
        assert repo.thread.startswith("credusage")
    finally:
        coord.close()


def test_notification_threshold_hysteresis_rolling_and_restart(tmp_path):
    connection = Connection("one", "openai.codex", binding=BINDING)
    descriptor = replace(FakeConnector().descriptor, validation=ValidationStatus.LIVE_VERIFIED)
    policy = NotificationPolicy(tmp_path)
    assert not policy.evaluate(None, state(connection, "70"), descriptor, NOW)
    assert (
        len(policy.evaluate(state(connection, "70"), state(connection, "81"), descriptor, NOW)) == 1
    )
    assert not policy.evaluate(None, state(connection, "79"), descriptor, NOW)
    assert not policy.evaluate(
        None, state(connection, "82", reset=NOW + timedelta(hours=3)), descriptor, NOW
    )
    policy = NotificationPolicy(tmp_path)
    assert not policy.evaluate(None, state(connection, "82"), descriptor, NOW)
    assert not policy.evaluate(None, state(connection, "74"), descriptor, NOW)
    assert len(policy.evaluate(None, state(connection, "81"), descriptor, NOW)) == 1
    assert len(policy.evaluate(None, state(connection, "100"), descriptor, NOW)) == 1
    assert len(policy.evaluate(None, state(connection, "5"), descriptor, NOW)) == 1


@pytest.mark.parametrize("mutation", ["unverified", "stale", "undated", "demo", "fixture"])
def test_no_quota_alerts_from_untrusted_data(tmp_path, mutation):
    connection = Connection("one", "openai.codex", binding=BINDING)
    descriptor = replace(FakeConnector().descriptor, validation=ValidationStatus.LIVE_VERIFIED)
    policy = NotificationPolicy(tmp_path)
    assert not policy.evaluate(None, state(connection, "70"), descriptor, NOW)
    current = state(connection, "100")
    if mutation == "unverified":
        connection = replace(connection, binding=IdentityBinding())
        current = state(connection, "100")
    elif mutation == "stale":
        current = state(connection, "100", now=NOW - timedelta(hours=1))
    elif mutation == "undated":
        current = state(connection, "100", basis=ObservationBasis.TELEMETRY, event_at=None)
    elif mutation == "demo":
        current = state(connection, "100", basis=ObservationBasis.DEMO)
    else:
        descriptor = replace(descriptor, validation=ValidationStatus.FIXTURE_TESTED)
    assert not policy.evaluate(None, current, descriptor, NOW)


def test_redaction_never_keeps_bearer_or_credential_values():
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        "credusage",
        logging.ERROR,
        "",
        0,
        "Authorization: Bearer secret access_token=secret2 sk-hidden-key",
        (),
        None,
    )
    result = formatter.format(record)
    assert "secret" not in result and "hidden-key" not in result


def test_core_imports_without_native_frameworks(tmp_path):
    import subprocess
    import sys

    code = "import sys; import credusage.core.coordinator, credusage.config; assert 'AppKit' not in sys.modules; assert 'rumps' not in sys.modules"
    subprocess.run([sys.executable, "-B", "-c", code], check=True)


@pytest.mark.parametrize("raw", ["[]", "null", "42", '"text"'])
def test_non_object_json_cannot_crash_stores(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(raw)
    assert ConfigStore(tmp_path).load().connections
    (tmp_path / "notification_state.json").write_text(raw)
    NotificationPolicy(tmp_path)
    (tmp_path / "refresh_state.json").write_text(raw)
    RetryStore(tmp_path)


@pytest.mark.parametrize("level", ["bad", "NaN", None])
def test_corrupt_notification_episodes_are_quarantined(tmp_path, level):
    atomic_json(
        tmp_path / "notification_state.json",
        {"schema_version": 1, "episodes": {"a" * 64: {"level": level, "exhausted": True}}},
    )
    NotificationPolicy(tmp_path)
    assert list(tmp_path.glob("notification_state.json.invalid-*"))


def test_demo_configuration_rejects_real_collectors():
    live = default_config()
    with pytest.raises(ValueError):
        validate(replace(live, demo=True))


def test_disabled_context_cannot_be_revived_by_push(tmp_path):
    connector = FakeConnector()
    connection = Connection("one", "openai.codex", enabled=False)
    coord = coordinator(tmp_path, [connector], [connection])
    try:
        coord.invalidate("one")
        coord.tick()
        assert coord.is_idle() and not connector.calls
    finally:
        coord.close()


def test_explicit_generation_change_immediately_detaches_snapshot(tmp_path):
    connector = FakeConnector()
    connection = Connection("one", "openai.codex", binding=BINDING)
    coord = coordinator(tmp_path, [connector], [connection])
    try:
        coord.request_refresh()
        wait_for(coord.is_idle)
        changed = replace(connection, generation=5, enabled=False)
        coord.update_connection(changed)
        assert coord.states()[0].snapshot is None
        assert coord.states()[0].connection.generation == 5
    finally:
        coord.close()


def test_executable_alias_cannot_bypass_shared_source_throttle(tmp_path):
    clock = FakeClock()

    def throttle(request):
        return CollectionResult(
            request.connection.connection_id,
            request.connection.generation,
            request.source_epoch,
            diagnostic=Diagnostic(ErrorCode.COLLECTOR_RATE_LIMITED, "Wait", 600),
        )

    connector = FakeConnector(collect=throttle)
    one = Connection("one", "openai.codex")
    two = Connection(
        "two", "openai.codex", preferences=ConnectionPreferences(executable="/same/codex")
    )
    coord = coordinator(tmp_path, [connector], [one, two], clock=clock)
    try:
        coord.request_refresh("one")
        wait_for(coord.is_idle)
        coord.request_refresh("two")
        assert len(connector.calls) == 1
        assert coord.states()[1].outcome.retry_at == NOW + timedelta(seconds=600)
    finally:
        coord.close()


def test_slow_disk_does_not_hold_menu_state_lock(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class SlowRepo(JSONSnapshotRepository):
        def save(self, value):
            entered.set()
            release.wait(2)
            super().save(value)

    connection = Connection("one", "openai.codex", binding=BINDING)
    coord = RefreshCoordinator(
        ConnectorRegistry((FakeConnector(),)), [connection], SlowRepo(tmp_path)
    )
    try:
        coord.request_refresh()
        assert entered.wait(1)
        started = time.monotonic()
        states = coord.states()
        coord.tick()
        assert time.monotonic() - started < 0.1
        assert states[0].snapshot is not None
    finally:
        release.set()
        wait_for(coord.is_idle)
        coord.close()


def test_unknown_quota_does_not_confirm_recovery(tmp_path):
    connection = Connection("one", "openai.codex", binding=BINDING)
    descriptor = replace(FakeConnector().descriptor, validation=ValidationStatus.LIVE_VERIFIED)
    policy = NotificationPolicy(tmp_path)
    policy.evaluate(None, state(connection, "100"), descriptor, NOW)
    unknown = state(connection, None)
    unknown = replace(
        unknown,
        snapshot=replace(
            unknown.snapshot,
            observations=(
                replace(
                    unknown.snapshot.observations[0],
                    availability=Availability.KNOWN,
                    state=QuotaState.UNKNOWN,
                ),
            ),
        ),
    )
    assert not policy.evaluate(state(connection, "100"), unknown, descriptor, NOW)
    assert len(policy.evaluate(unknown, state(connection, "5"), descriptor, NOW)) == 1


def test_auth_recheck_can_follow_explicit_manual_login_refresh(tmp_path):
    clock = FakeClock()
    connector = FakeConnector(
        collect=lambda r: CollectionResult(
            r.connection.connection_id,
            r.connection.generation,
            r.source_epoch,
            diagnostic=Diagnostic(ErrorCode.AUTH_REQUIRED, "Sign in"),
        )
    )
    connection = Connection("one", "openai.codex")
    coord = coordinator(
        tmp_path, [connector], [connection], clock=clock, retry_store=RetryStore(tmp_path)
    )
    try:
        coord.request_refresh()
        wait_for(coord.is_idle)
        coord.request_refresh(reason="menu")
        assert len(connector.calls) == 1
        connector.collect_fn = None
        coord.request_refresh(reason="manual")
        wait_for(coord.is_idle)
        assert len(connector.calls) == 2
        assert coord.states()[0].outcome.health == ConnectionHealth.CONNECTED
    finally:
        coord.close()


def test_wall_clock_rewind_cannot_extend_in_process_freshness(tmp_path):
    clock = FakeClock()
    connection = Connection("one", "openai.codex", binding=BINDING)
    coord = coordinator(tmp_path, [FakeConnector()], [connection], clock=clock)
    try:
        coord.request_refresh()
        wait_for(coord.is_idle)
        clock.mono += 601
        clock.wall -= timedelta(days=1)
        coord.tick()
        current = coord.states()[0].snapshot
        assert current.observations[0].freshness(clock.now()) == Freshness.STALE
    finally:
        coord.close()


def test_connection_problem_is_actionable_and_deduplicated(tmp_path):
    connection = Connection("one", "openai.codex")
    descriptor = FakeConnector().descriptor
    before = state(connection)
    failure = replace(
        before,
        outcome=RefreshOutcome(
            ConnectionHealth.AUTH_REQUIRED, NOW, NOW, "AUTH_EXPIRED", "Reconnect"
        ),
    )
    policy = NotificationPolicy(tmp_path)
    assert len(policy.evaluate(before, failure, descriptor, NOW)) == 1
    assert not policy.evaluate(before, failure, descriptor, NOW)
    assert not NotificationPolicy(tmp_path).evaluate(before, failure, descriptor, NOW)


def test_lock_keeps_inode_and_blocks_a_second_monitor(tmp_path):
    from credusage.infrastructure.instance import InstanceLock

    with InstanceLock(tmp_path):
        inode = (tmp_path / "monitor.lock").stat().st_ino
        with pytest.raises(RuntimeError):
            with InstanceLock(tmp_path):
                pass
    with InstanceLock(tmp_path):
        assert (tmp_path / "monitor.lock").stat().st_ino == inode
