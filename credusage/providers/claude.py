"""Passive Claude Code status-line telemetry; no OAuth or Keychain collector."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from credusage.domain.contracts import CollectionResult, DiscoveryResult
from credusage.domain.errors import ErrorCode
from credusage.domain.models import (
    ActionKind,
    Availability,
    Capability,
    ConnectionAction,
    IdentityBinding,
    IntegrationDescriptor,
    ObservationBasis,
    Provenance,
    QuotaObservation,
    QuotaState,
    QuotaWindow,
    SourcePolicy,
    Unit,
    WindowKind,
)
from credusage.domain.normalization import percent_used, utc_timestamp
from credusage.infrastructure.clock import SystemClock
from credusage.infrastructure.processes import failure, find_executable

MAX_STDIN_BYTES = 64 * 1024
SLOTS = {"five_hour": ("Claude · 5h", 18000), "seven_day": ("Claude · 7d", 604800)}
CAPABILITIES = (Capability.DISCOVERY, Capability.TELEMETRY, Capability.QUOTAS, Capability.DASHBOARD)


def telemetry_path(connection_id: str, root: Path) -> Path:
    return Path(root) / "telemetry" / (hashlib.sha256(connection_id.encode()).hexdigest() + ".json")


def _safe_identifier(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid telemetry identifier")
    return value


def normalize_statusline(payload: bytes) -> dict:
    if not isinstance(payload, bytes) or len(payload) > MAX_STDIN_BYTES:
        raise ValueError("Status-line input exceeds the supported size")
    try:
        data = json.loads(
            payload,
            parse_float=Decimal,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Malformed status-line JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("rate_limits"), dict):
        raise ValueError("Status-line input has no supported rate_limits object")
    buckets = {}
    for slot in SLOTS:
        value = data["rate_limits"].get(slot)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError("Invalid status-line rate limit")
        used = percent_used(value.get("used_percentage"))
        raw_reset = value.get("resets_at")
        if raw_reset is not None and (
            isinstance(raw_reset, bool) or not isinstance(raw_reset, (int, float, Decimal))
        ):
            raise ValueError("Status-line reset must use epoch seconds")
        reset = utc_timestamp(raw_reset)
        buckets[slot] = {
            "used_percent": str(used) if used is not None else None,
            "resets_at": reset.isoformat() if reset else None,
        }
    # The documented payload has no event timestamp or verified account subject.
    # Receipt time and session_id must never be promoted into either one.
    return {
        "session_id": _safe_identifier(data.get("session_id")),
        "version": _safe_identifier(data.get("version")),
        "event_at": None,
        "buckets": buckets,
    }


def ingest_statusline(connection, payload: bytes, root: Path, now: datetime) -> Path:
    """Explicit ingestion entry point. Writes only allowlisted normalized fields."""
    normalized = normalize_statusline(payload)
    ingested_at = utc_timestamp(now)
    envelope = {
        "schema_version": 1,
        "connection_id": connection.connection_id,
        "generation": connection.generation,
        "ingested_at": ingested_at.isoformat(),
        **normalized,
    }
    path = telemetry_path(connection.connection_id, root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".inbox-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(envelope, stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return path


def _read_inbox(path: Path) -> bytes:
    """Open without waiting for FIFO peers and reject anything but a regular file.

    O_NOFOLLOW prevents replacing the final component with a symlink between the
    path check and open. fstat checks the opened descriptor, closing the same
    race for devices and pipes. Read length remains bounded if the file grows.
    """
    if path.is_symlink():
        raise ValueError("Telemetry symlinks are unsupported")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError("Telemetry symlinks are unsupported") from None
        raise
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STDIN_BYTES:
            raise ValueError("Telemetry must be a bounded regular file")
        data = bytearray()
        while len(data) <= MAX_STDIN_BYTES:
            chunk = os.read(fd, min(65536, MAX_STDIN_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_STDIN_BYTES:
            raise ValueError("Telemetry exceeded the supported size")
        return bytes(data)
    finally:
        os.close(fd)


def parse_telemetry(envelope, request) -> CollectionResult:
    if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
        raise ValueError("Unsupported telemetry schema")
    if (
        envelope.get("connection_id") != request.connection.connection_id
        or type(envelope.get("generation")) is not int
        or envelope["generation"] != request.connection.generation
    ):
        raise failure(
            ErrorCode.ACCOUNT_MISMATCH,
            "This Claude telemetry belongs to another connection generation. Reconfigure the ingestion command.",
        )
    ingested = utc_timestamp(envelope.get("ingested_at"))
    if ingested is None or envelope.get("event_at") is not None:
        raise ValueError("Unsupported telemetry timestamp")
    buckets = envelope.get("buckets")
    if not isinstance(buckets, dict):
        raise ValueError("Invalid telemetry buckets")
    observations = []
    version = envelope.get("version")
    version = (
        version
        if isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
        else None
    )
    for slot, (label, duration) in SLOTS.items():
        value = buckets.get(slot)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError("Invalid telemetry bucket")
        used = percent_used(value.get("used_percent"))
        reset = utc_timestamp(value.get("resets_at"))
        observations.append(
            QuotaObservation(
                request.connection.connection_id,
                request.connection.generation,
                "claude:" + slot,
                label,
                Provenance(
                    "claude.statusline",
                    SourcePolicy.LOCAL_TELEMETRY,
                    "1",
                    ingested,
                    None,
                    ObservationBasis.TELEMETRY,
                    version,
                ),
                binding=IdentityBinding(),
                unit=Unit.PERCENT,
                used_percent=used,
                window=QuotaWindow(
                    WindowKind.ROLLING if slot == "five_hour" else WindowKind.UNKNOWN,
                    None,
                    duration_seconds=duration,
                    resets_at=reset,
                    reset_semantics="provider_reported",
                ),
                state=(
                    QuotaState.UNKNOWN
                    if used is None
                    else (
                        QuotaState.EXHAUSTED
                        if used >= 100
                        else QuotaState.LOW if used >= 80 else QuotaState.AVAILABLE
                    )
                ),
                availability=Availability.UNKNOWN if used is None else Availability.KNOWN,
            )
        )
    return CollectionResult(
        request.connection.connection_id,
        request.connection.generation,
        request.source_epoch,
        observations=tuple(observations),
        capabilities=CAPABILITIES,
    )


class ClaudeConnector:
    descriptor = IntegrationDescriptor(
        "anthropic.claude",
        "anthropic",
        "Claude Code",
        capabilities=CAPABILITIES,
        source_policy=SourcePolicy.LOCAL_TELEMETRY,
        auth_modes=("passive_statusline",),
        evidence="Official Claude Code status-line contract; fixture tested. Undated local context; no verified account subject.",
    )

    def __init__(self, clock=None, root=None):
        self.clock = clock or SystemClock()
        self.root = Path(root) if root else Path.home() / ".credusage"

    def discover(self, context):
        installed = bool(find_executable("claude"))
        return DiscoveryResult(
            installed,
            message=(
                "Claude Code detected. Configure explicit status-line ingestion to supply telemetry."
                if installed
                else "Install Claude Code and configure status-line ingestion."
            ),
            capabilities=CAPABILITIES,
        )

    def collect(self, request):
        path = (
            Path(request.connection.preferences.source_path).expanduser()
            if request.connection.preferences.source_path
            else telemetry_path(request.connection.connection_id, self.root)
        )
        try:
            raw = _read_inbox(path)
            envelope = json.loads(
                raw,
                parse_float=Decimal,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            result = parse_telemetry(envelope, request)
            if not result.observations:
                raise failure(
                    ErrorCode.SOURCE_UNAVAILABLE,
                    "Claude status-line telemetry contains no supported allowance windows yet.",
                )
            return result
        except FileNotFoundError:
            raise failure(
                ErrorCode.SOURCE_UNAVAILABLE,
                "Claude telemetry is unconfigured. Use the explicit ingest-claude-statusline command with your existing status-line script.",
            ) from None
        except PermissionError:
            raise failure(
                ErrorCode.PERMISSION_DENIED,
                "Claude telemetry cannot be read. Check the app-owned inbox permissions.",
            ) from None
        except (ValueError, TypeError, OverflowError):
            raise failure(
                ErrorCode.INVALID_LOCAL_DATA,
                "Claude telemetry is malformed or unsupported. No new usage was accepted.",
            ) from None
        except OSError:
            raise failure(
                ErrorCode.SOURCE_UNAVAILABLE, "Claude telemetry inbox is unavailable."
            ) from None

    def actions(self, connection):
        return (
            ConnectionAction(
                "dashboard",
                "Open Claude usage",
                ActionKind.OPEN_URL,
                "https://claude.ai/settings/usage",
            ),
            ConnectionAction(
                "setup",
                "Status-line setup documentation",
                ActionKind.OPEN_URL,
                "https://code.claude.com/docs/en/statusline#rate-limit-usage",
            ),
        )

    def close(self):
        pass
