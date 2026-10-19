"""Version-gated passive Antigravity CLI usage, parsed from real structured output."""

import json
import re
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote

from credusage.domain.contracts import CollectionResult, DiscoveryResult
from credusage.domain.errors import ErrorCode
from credusage.domain.models import (
    ActionKind,
    Availability,
    Capability,
    ConnectionAction,
    IntegrationDescriptor,
    Provenance,
    QuotaObservation,
    QuotaState,
    QuotaWindow,
    SourcePolicy,
)
from credusage.domain.normalization import fraction_remaining, utc_timestamp
from credusage.infrastructure.clock import SystemClock
from credusage.infrastructure.processes import CommandRunner, failure, find_executable

CAPABILITIES = (Capability.DISCOVERY, Capability.QUOTAS, Capability.DASHBOARD)
WINDOW_SECONDS = {"5h": 18000, "weekly": 604800}


def _text(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 100
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("Invalid quota text or identity")
    return value


def parse_usage(payload, request, *, fetched_at=None, tool_version=None):
    """Use only command.data; the human-readable response and token usage are unrelated."""
    if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
        raise ValueError("Usage command did not succeed")
    command = payload.get("command")
    if not isinstance(command, dict) or command.get("name") != "usage":
        raise ValueError("Not a passive usage result")
    data = command.get("data")
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list) or not groups:
        raise ValueError("No quota groups")
    fetched_at = fetched_at or request.requested_at
    provenance = Provenance(
        "antigravity.cli.usage",
        SourcePolicy.DOCUMENTED,
        "1",
        fetched_at,
        tool_version=tool_version,
    )
    observations, group_names, keys = [], set(), set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("Invalid quota group")
        name = _text(group.get("name"))
        if name in group_names:
            raise ValueError("Duplicate quota group")
        group_names.add(name)
        buckets = group.get("buckets")
        if not isinstance(buckets, list) or not buckets:
            raise ValueError("No quota buckets")
        pool = "antigravity:" + quote(name, safe="")
        for bucket in buckets:
            if not isinstance(bucket, dict) or "remaining_fraction" not in bucket:
                raise ValueError("Invalid quota bucket")
            key = pool + ":" + quote(_text(bucket.get("id")), safe="")
            if key in keys or len(observations) >= 1024:
                raise ValueError("Duplicate or excessive quota buckets")
            keys.add(key)
            label = _text(bucket.get("name"))
            window = _text(bucket.get("window"))
            used = fraction_remaining(bucket["remaining_fraction"])
            raw_reset = bucket.get("reset_time")
            if raw_reset is not None and not isinstance(raw_reset, str):
                raise ValueError("Reset time must be an ISO timestamp")
            reset = utc_timestamp(raw_reset)
            observations.append(
                QuotaObservation(
                    request.connection.connection_id,
                    request.connection.generation,
                    key,
                    f"{name} · {label}",
                    provenance,
                    used_percent=used,
                    window=QuotaWindow(
                        duration_seconds=WINDOW_SECONDS.get(window),
                        resets_at=reset,
                        reset_semantics="provider_reported",
                    ),
                    availability=Availability.KNOWN if used is not None else Availability.UNKNOWN,
                    state=(
                        QuotaState.UNKNOWN
                        if used is None
                        else (
                            QuotaState.EXHAUSTED
                            if used >= 100
                            else QuotaState.LOW if used >= 80 else QuotaState.AVAILABLE
                        )
                    ),
                    shared_pool_id=pool,
                    sharing_evidence="Antigravity /usage explicitly groups models within this quota pool.",
                    fresh_until=fetched_at + timedelta(minutes=10),
                )
            )
    # /usage does not report an account subject. Never reuse a configured binding.
    return CollectionResult(
        request.connection.connection_id,
        request.connection.generation,
        request.source_epoch,
        observations=tuple(observations),
        capabilities=CAPABILITIES,
    )


class AntigravityConnector:
    descriptor = IntegrationDescriptor(
        "google.antigravity",
        "google",
        "Antigravity",
        capabilities=CAPABILITIES,
        auth_modes=("vendor_cli",),
        evidence="Passive /usage JSON from CLI 1.4.0; fixture tested against user-supplied output. Account subject unverified; original-display comparison pending.",
    )

    def __init__(self, clock=None, *, runner=None):
        self.clock = clock or SystemClock()
        self.runner = runner or CommandRunner(monotonic=self.clock.monotonic)

    def _version(self, executable, deadline):
        output = self.runner.run((executable, "--version"), deadline, maximum_bytes=4096)
        if output.returncode != 0:
            raise failure(
                ErrorCode.SOURCE_UNAVAILABLE, "Antigravity CLI version could not be read."
            )
        match = re.search(rb"(?<![0-9])([0-9]+)\.([0-9]+)\.([0-9]+)(?![0-9])", output.stdout)
        if not match:
            raise failure(
                ErrorCode.SOURCE_SCHEMA_CHANGED, "Antigravity CLI returned an unrecognized version."
            )
        version = tuple(map(int, match.groups()))
        if version < (1, 2, 15):
            raise failure(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "Antigravity CLI 1.2.15 or later is required for passive usage output.",
            )
        return version

    def discover(self, context):
        executable = find_executable("agy")
        if not executable:
            return DiscoveryResult(
                False,
                message="Install Antigravity CLI agy 1.2.15 or later and sign in with the vendor tool.",
                capabilities=self.descriptor.capabilities,
            )
        version = self._version(executable, context.deadline_monotonic)
        return DiscoveryResult(
            True,
            ".".join(map(str, version)),
            "Passive CLI usage supported; authentication remains owned by Antigravity.",
            self.descriptor.capabilities,
        )

    def collect(self, request):
        executable = find_executable("agy", request.connection.preferences.executable)
        if not executable:
            raise failure(
                ErrorCode.SOURCE_UNAVAILABLE,
                "Install Antigravity CLI agy 1.2.15 or later and sign in with the vendor tool.",
            )
        version = self._version(executable, request.deadline_monotonic)
        # This is the documented passive CLI command. Never substitute a turn or
        # a generic prompting command when the command/schema is unavailable.
        output = self.runner.run(
            (executable, "-p", "/usage", "--output-format", "json"), request.deadline_monotonic
        )
        if output.returncode != 0:
            raise failure(
                ErrorCode.SOURCE_UNAVAILABLE, "Antigravity passive usage output is unavailable."
            )
        try:
            payload = json.loads(
                output.stdout,
                parse_float=Decimal,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            return parse_usage(
                payload,
                request,
                fetched_at=self.clock.now(),
                tool_version=".".join(map(str, version)),
            )
        except (ValueError, TypeError, OverflowError):
            raise failure(
                ErrorCode.SOURCE_SCHEMA_CHANGED,
                "Antigravity returned unsupported usage data. No new usage was accepted.",
            ) from None

    def actions(self, connection):
        return (
            ConnectionAction(
                "dashboard", "Open Antigravity", ActionKind.OPEN_URL, "https://antigravity.google/"
            ),
            ConnectionAction(
                "setup",
                "Antigravity CLI documentation",
                ActionKind.OPEN_URL,
                "https://antigravity.google/docs/changelog?tab=cli",
            ),
        )

    def close(self):
        pass
