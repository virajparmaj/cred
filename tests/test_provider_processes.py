"""Real local subprocess fixtures exercise framing, routing, deadlines and shutdown."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from credusage.domain.errors import CollectionError, ErrorCode
from credusage.infrastructure.processes import CommandRunner, JsonRpcProcess

SERVER = r"""
import json,sys
pending = None
for line in sys.stdin:
    data = json.loads(line)
    method = data["method"]
    if method == "initialized": continue
    if method == "initialize":
        assert data["params"]["clientInfo"]["name"] == "credusage"
        print(json.dumps({"id":data["id"],"result":{"userAgent":"fixture"}}), flush=True)
    elif method == "hang": continue
    elif method == "error":
        print(json.dumps({"id":data["id"],"error":{"code":429,"message":"secret account token do not expose"}}), flush=True)
    elif method in ("forbidden", "unauthorized"):
        print(json.dumps({"id":data["id"],"error":{"code":403 if method == "forbidden" else 401,"message":"private upstream reason"}}), flush=True)
    elif method == "bad":
        print("not-json-secret-token", flush=True)
    elif method == "oversize":
        print("x" * (1024*1024+1), flush=True)
    elif method == "decimal":
        print('{"id":%s,"result":{"balance":12.123456789123456789}}' % data["id"], flush=True)
    elif method == "first": pending = data
    elif method == "second":
        print(json.dumps({"method":"account/rateLimits/updated","params":{"identityless":True}}), flush=True)
        print(json.dumps({"id":data["id"],"result":"second"}), flush=True)
        print(json.dumps({"id":pending["id"],"result":"first"}), flush=True)
    else: print(json.dumps({"id":data["id"],"result":{"ok":True}}), flush=True)
"""


def transport(callback=None):
    result = JsonRpcProcess((sys.executable, "-u", "-c", SERVER), callback)
    result.start(time.monotonic() + 3)
    return result


def test_rpc_routes_out_of_order_results_and_unrelated_notification():
    notifications = []
    rpc = transport(lambda method, params: notifications.append((method, params)))
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(rpc.request, "first", {}, time.monotonic() + 2)
            # Ensure the first write preceded the second without time sleeps.
            limit = time.monotonic() + 1
            while rpc._next_id < 2 and time.monotonic() < limit:
                pass
            second = workers.submit(rpc.request, "second", {}, time.monotonic() + 2)
            assert first.result(timeout=3) == "first"
            assert second.result(timeout=3) == "second"
        assert notifications == [("account/rateLimits/updated", {"identityless": True})]
    finally:
        rpc.close()


def test_rpc_json_decimal_precision_survives_transport():
    rpc = transport()
    try:
        assert rpc.request("decimal", {}, time.monotonic() + 2)["balance"] == Decimal(
            "12.123456789123456789"
        )
    finally:
        rpc.close()


def test_rpc_timeout_closes_process_and_advances_epoch():
    rpc = transport()
    before = rpc.epoch
    started = time.monotonic()
    with pytest.raises(CollectionError) as error:
        rpc.request("hang", {}, time.monotonic() + 0.1)
    assert error.value.diagnostic.code == ErrorCode.TIMEOUT
    assert time.monotonic() - started < 2
    assert rpc.epoch > before
    assert rpc._process is None


def test_rpc_error_never_echoes_upstream_secret():
    rpc = transport()
    try:
        with pytest.raises(CollectionError) as error:
            rpc.request("error", {}, time.monotonic() + 2)
        assert error.value.diagnostic.code == ErrorCode.COLLECTOR_RATE_LIMITED
        assert "secret" not in error.value.diagnostic.message
    finally:
        rpc.close()


@pytest.mark.parametrize("method", ["bad", "oversize"])
def test_rpc_malformed_or_oversized_message_is_rejected(method):
    rpc = transport()
    try:
        with pytest.raises(CollectionError) as error:
            rpc.request(method, {}, time.monotonic() + 2)
        assert error.value.diagnostic.code == ErrorCode.SOURCE_SCHEMA_CHANGED
        assert "secret" not in error.value.diagnostic.message
    finally:
        rpc.close()


def test_rpc_close_is_bounded_and_rejects_late_reply():
    rpc = transport()
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(rpc.request, "hang", {}, time.monotonic() + 20)
        rpc.close()
        with pytest.raises(CollectionError):
            future.result(timeout=2)
    rpc.close()


def test_rpc_restart_reinitializes_new_process_epoch():
    rpc = transport()
    old = rpc.epoch
    rpc.close()
    rpc.start(time.monotonic() + 2)
    try:
        assert rpc.epoch > old
        assert rpc.request("read", {}, time.monotonic() + 2) == {"ok": True}
    finally:
        rpc.close()


def test_command_runner_caps_output_without_disk_spool():
    with pytest.raises(CollectionError) as error:
        CommandRunner().run(
            (sys.executable, "-c", "print('x'*1000000)"), time.monotonic() + 3, maximum_bytes=4096
        )
    assert error.value.diagnostic.code == ErrorCode.SOURCE_SCHEMA_CHANGED


def test_command_runner_timeout_and_nonzero_exit():
    with pytest.raises(CollectionError) as error:
        CommandRunner().run(
            (sys.executable, "-c", "import time; time.sleep(10)"), time.monotonic() + 0.1
        )
    assert error.value.diagnostic.code == ErrorCode.TIMEOUT
    result = CommandRunner().run(
        (sys.executable, "-c", "import sys; print('safe'); sys.exit(2)"), time.monotonic() + 2
    )
    assert result.returncode == 2 and result.stdout == b"safe\n"


def test_rpc_expired_deadline_does_not_start_a_process():
    def forbidden(*args, **kwargs):
        raise AssertionError("Expired deadline must not start a process")

    rpc = JsonRpcProcess(("unused",), popen=forbidden)
    with pytest.raises(CollectionError) as error:
        rpc.start(time.monotonic() - 1)
    assert error.value.diagnostic.code == ErrorCode.TIMEOUT


def test_rpc_expired_read_deadline_does_not_send_request():
    rpc = transport()
    try:
        before = rpc._next_id
        with pytest.raises(CollectionError) as error:
            rpc.request("read", {}, time.monotonic() - 1)
        assert error.value.diagnostic.code == ErrorCode.TIMEOUT
        assert rpc._next_id == before
    finally:
        rpc.close()


def test_command_expired_deadline_does_not_start_a_process():
    def forbidden(*args, **kwargs):
        raise AssertionError("Expired deadline must not start a process")

    runner = CommandRunner(popen=forbidden)
    with pytest.raises(CollectionError) as error:
        runner.run(("unused",), time.monotonic() - 1)
    assert error.value.diagnostic.code == ErrorCode.TIMEOUT


def test_command_accounts_for_startup_time_in_absolute_deadline():
    ticks = iter((0.0, 20.0))
    runner = CommandRunner(monotonic=lambda: next(ticks))
    started = time.monotonic()
    with pytest.raises(CollectionError) as error:
        runner.run((sys.executable, "-c", "import time;time.sleep(10)"), 10.0)
    assert error.value.diagnostic.code == ErrorCode.TIMEOUT
    assert time.monotonic() - started < 2


def test_find_executable_handles_missing_relative_and_minimal_gui_path(monkeypatch, tmp_path):
    import os

    from credusage.infrastructure.processes import find_executable

    candidate = tmp_path / ".local/bin/codex"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("#!/bin/sh\nexit 0\n")
    candidate.chmod(0o700)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr("credusage.infrastructure.processes.Path.home", lambda: tmp_path)
    assert find_executable("codex") == str(candidate)
    assert find_executable("codex", str(candidate)) == str(candidate)
    assert find_executable("codex", ".local/bin/codex") is None
    assert find_executable("codex", str(tmp_path / "missing")) is None
    candidate.chmod(0o600)
    assert find_executable("codex", str(candidate)) is None


@pytest.mark.parametrize(
    "method,code",
    [("forbidden", ErrorCode.PERMISSION_DENIED), ("unauthorized", ErrorCode.AUTH_REQUIRED)],
)
def test_rpc_forbidden_is_not_misclassified_as_missing_auth(method, code):
    rpc = transport()
    try:
        with pytest.raises(CollectionError) as error:
            rpc.request(method, {}, time.monotonic() + 2)
        assert error.value.diagnostic.code == code
        assert "private" not in error.value.diagnostic.message
    finally:
        rpc.close()
