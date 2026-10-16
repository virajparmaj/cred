"""Bounded vendor-owned subprocesses. No shell, token files, or raw diagnostics."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from credusage.domain.errors import CollectionError, Diagnostic, ErrorCode

MAX_MESSAGE_BYTES = 1024 * 1024


def find_executable(name: str, configured: str | None = None) -> str | None:
    if configured:
        path = Path(configured).expanduser()
        return (
            str(path)
            if path.is_absolute() and path.is_file() and os.access(path, os.X_OK)
            else None
        )
    found = shutil.which(name)
    if found:
        return found
    for directory in (
        Path.home() / ".local/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/Applications/Codex.app/Contents/Resources"),
    ):
        path = directory / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def failure(code: ErrorCode, message: str) -> CollectionError:
    return CollectionError(Diagnostic(code, message))


@dataclass(frozen=True)
class CommandOutput:
    returncode: int
    stdout: bytes


class CommandRunner:
    def __init__(self, *, monotonic=None, popen=None):
        self._monotonic = monotonic or time.monotonic
        self._popen = popen or subprocess.Popen

    def run(
        self, argv: tuple[str, ...], deadline: float, *, maximum_bytes: int = MAX_MESSAGE_BYTES
    ) -> CommandOutput:
        remaining = min(15.0, deadline - self._monotonic())
        if remaining <= 0:
            raise failure(ErrorCode.TIMEOUT, "The source command timed out.")
        if maximum_bytes <= 0:
            raise ValueError("A positive output bound is required")
        try:
            process = self._popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                shell=False,
            )
        except PermissionError:
            raise failure(
                ErrorCode.PERMISSION_DENIED, "Permission to run the source command was denied."
            ) from None
        except OSError:
            raise failure(
                ErrorCode.SOURCE_UNAVAILABLE, "The source command could not be started."
            ) from None
        data = bytearray()
        overflow = threading.Event()

        def drain():
            try:
                while True:
                    chunk = process.stdout.read(min(65536, maximum_bytes + 1 - len(data)))
                    if not chunk:
                        return
                    data.extend(chunk)
                    if len(data) > maximum_bytes:
                        overflow.set()
                        try:
                            process.kill()
                        except OSError:
                            pass
                        return
            except (OSError, ValueError):
                return

        reader = threading.Thread(target=drain, name="credusage-command-output", daemon=True)
        reader.start()
        try:
            try:
                remaining = max(0.0, min(15.0, deadline - self._monotonic()))
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
                raise failure(ErrorCode.TIMEOUT, "The source command timed out.") from None
            reader.join(timeout=0.5)
            if reader.is_alive():
                raise failure(
                    ErrorCode.TIMEOUT, "The source command did not finish its output stream."
                )
            if overflow.is_set():
                raise failure(
                    ErrorCode.SOURCE_SCHEMA_CHANGED,
                    "The source response exceeded the supported size.",
                )
            return CommandOutput(process.returncode, bytes(data))
        finally:
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            process.stdout.close()


class JsonRpcProcess:
    """One line-delimited JSON-RPC stdio transport with bounded waits and shutdown.

    The reader dispatches replies by request ID and forwards notifications without
    interpreting account identity. stderr is discarded because upstream text may
    contain sensitive account details. Restarts always create a new epoch.
    """

    def __init__(
        self,
        argv: tuple[str, ...],
        on_notification: Callable[[str, object], None] | None = None,
        *,
        popen=None,
        monotonic=None,
    ):
        self.argv = argv
        self.on_notification = on_notification
        self._popen = popen or subprocess.Popen
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self._process = None
        self._pending = {}
        self._next_id = 0
        self._epoch = 0
        self._closed = False
        self._reader = None
        self.tool_version = None

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def start(self, deadline: float) -> None:
        if deadline <= self._monotonic():
            raise failure(ErrorCode.TIMEOUT, "The Codex source deadline elapsed before startup.")
        with self._lock:
            if self._process is not None and self._process.poll() is None and not self._closed:
                return
            self._epoch += 1
            self._closed = False
            try:
                process = self._popen(
                    self.argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    shell=False,
                )
            except PermissionError:
                raise failure(
                    ErrorCode.PERMISSION_DENIED,
                    "Permission to start the Codex app-server was denied.",
                ) from None
            except OSError:
                raise failure(
                    ErrorCode.SOURCE_UNAVAILABLE, "The Codex app-server could not be started."
                ) from None
            self._process = process
            epoch = self._epoch
            self._reader = threading.Thread(
                target=self._read, args=(process, epoch), name="credusage-codex-stdio", daemon=True
            )
            self._reader.start()
        try:
            initialized = self.request(
                "initialize",
                {
                    "clientInfo": {"name": "credusage", "title": "CredUsage", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": False},
                },
                deadline,
            )
            agent = initialized.get("userAgent") if isinstance(initialized, dict) else None
            match = (
                re.search(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])", agent)
                if isinstance(agent, str)
                else None
            )
            self.tool_version = match.group(1) if match else None
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def _read(self, process, epoch: int) -> None:
        code = ErrorCode.NETWORK_UNAVAILABLE
        try:
            while True:
                raw = process.stdout.readline(MAX_MESSAGE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                    code = ErrorCode.SOURCE_SCHEMA_CHANGED
                    break
                try:
                    message = json.loads(
                        raw,
                        parse_float=Decimal,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                except (ValueError, UnicodeDecodeError):
                    code = ErrorCode.SOURCE_SCHEMA_CHANGED
                    break
                if not isinstance(message, dict):
                    code = ErrorCode.SOURCE_SCHEMA_CHANGED
                    break
                with self._lock:
                    if epoch != self._epoch or self._closed:
                        break
                    if "id" in message and "method" not in message:
                        request_id = message["id"]
                        pending = (
                            self._pending.get(request_id)
                            if isinstance(request_id, int) and not isinstance(request_id, bool)
                            else None
                        )
                        if pending is not None:
                            pending[1].append(message)
                            pending[0].set()
                        continue
                if (
                    isinstance(message.get("method"), str)
                    and "id" not in message
                    and self.on_notification
                ):
                    try:
                        self.on_notification(message["method"], message.get("params"))
                    except Exception:
                        pass  # A UI callback cannot stop protocol reply routing.
        finally:
            with self._lock:
                if epoch == self._epoch:
                    for event, box in self._pending.values():
                        if not box:
                            box.append({"transport_error": code})
                        event.set()
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    def _write(self, message: dict) -> None:
        if self._closed or self._process is None or self._process.poll() is not None:
            raise failure(ErrorCode.NETWORK_UNAVAILABLE, "The Codex app-server disconnected.")
        try:
            self._process.stdin.write(
                json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n"
            )
            self._process.stdin.flush()
        except (OSError, ValueError):
            raise failure(
                ErrorCode.NETWORK_UNAVAILABLE, "The Codex app-server disconnected."
            ) from None

    def notify(self, method: str, params: dict) -> None:
        with self._lock:
            self._write({"method": method, "params": params})

    def request(self, method: str, params: dict, deadline: float):
        if deadline <= self._monotonic():
            raise failure(
                ErrorCode.TIMEOUT, "The Codex source deadline elapsed before the read request."
            )
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            epoch = self._epoch
            event, box = threading.Event(), []
            self._pending[request_id] = (event, box)
            try:
                self._write({"id": request_id, "method": method, "params": params})
            except Exception:
                self._pending.pop(request_id, None)
                raise
        timeout = max(0.0, min(20.0, deadline - self._monotonic()))
        ready = event.wait(timeout)
        with self._lock:
            self._pending.pop(request_id, None)
            if epoch != self._epoch or self._closed:
                raise failure(
                    ErrorCode.NETWORK_UNAVAILABLE,
                    "The Codex source process changed during collection.",
                )
        if not ready:
            self.close()
            raise failure(
                ErrorCode.TIMEOUT, "The Codex app-server did not respond before the deadline."
            )
        response = box[0]
        if "transport_error" in response:
            raise failure(
                response["transport_error"],
                "The Codex app-server stopped or returned an invalid message.",
            )
        if "error" in response:
            error = response["error"]
            rpc_code = error.get("code") if isinstance(error, dict) else None
            code = (
                ErrorCode.COLLECTOR_RATE_LIMITED
                if rpc_code == 429
                else (
                    ErrorCode.AUTH_REQUIRED
                    if rpc_code == 401
                    else (
                        ErrorCode.PERMISSION_DENIED
                        if rpc_code == 403
                        else (
                            ErrorCode.UNSUPPORTED_CAPABILITY
                            if rpc_code == -32601
                            else ErrorCode.NETWORK_UNAVAILABLE
                        )
                    )
                )
            )
            raise failure(code, "The Codex app-server rejected the read request.")
        if "result" not in response:
            raise failure(
                ErrorCode.SOURCE_SCHEMA_CHANGED, "The Codex app-server response has no result."
            )
        return response["result"]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._epoch += 1
            process, self._process = self._process, None
            for event, box in self._pending.values():
                if not box:
                    box.append({"transport_error": ErrorCode.NETWORK_UNAVAILABLE})
                event.set()
        if process is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=0.5)
            for stream in (process.stdout, process.stdin):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
