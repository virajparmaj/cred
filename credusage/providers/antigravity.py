"""Version-gated passive Antigravity CLI discovery; output schema is unverified."""

import re

from credusage.domain.contracts import DiscoveryResult
from credusage.domain.errors import ErrorCode
from credusage.domain.models import (
    ActionKind,
    Capability,
    ConnectionAction,
    IntegrationDescriptor,
    ValidationStatus,
)
from credusage.infrastructure.clock import SystemClock
from credusage.infrastructure.processes import CommandRunner, failure, find_executable


class AntigravityConnector:
    descriptor = IntegrationDescriptor(
        "google.antigravity",
        "google",
        "Antigravity",
        capabilities=(Capability.DISCOVERY, Capability.DASHBOARD),
        validation=ValidationStatus.NOT_IMPLEMENTED,
        auth_modes=("vendor_cli",),
        evidence="Official changelog documents passive usage JSON since CLI 1.2.15; actual output schema not yet obtained. Numerical collection unavailable.",
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
                message="Antigravity CLI agy is not installed. The output schema remains unverified.",
                capabilities=self.descriptor.capabilities,
            )
        version = self._version(executable, context.deadline_monotonic)
        return DiscoveryResult(
            True,
            ".".join(map(str, version)),
            "Passive CLI supported; numerical parser awaits sanitized real output.",
            self.descriptor.capabilities,
        )

    def collect(self, request):
        executable = find_executable("agy", request.connection.preferences.executable)
        if not executable:
            raise failure(
                ErrorCode.SOURCE_UNAVAILABLE,
                "Install Antigravity CLI agy 1.2.15 or later. Numerical usage awaits schema verification.",
            )
        self._version(executable, request.deadline_monotonic)
        # This is the documented passive CLI command. Never substitute a turn or
        # a generic prompting command when the command/schema is unavailable.
        output = self.runner.run(
            (executable, "-p", "/usage", "--output-format", "json"), request.deadline_monotonic
        )
        if output.returncode != 0:
            raise failure(
                ErrorCode.SOURCE_UNAVAILABLE, "Antigravity passive usage output is unavailable."
            )
        # No fixture was invented for an unavailable real source. Raw output is
        # discarded; defining a parser requires an actual sanitized sample.
        raise failure(
            ErrorCode.SOURCE_SCHEMA_CHANGED,
            "Antigravity passive output was received, but its schema and units have not been verified. No usage was accepted.",
        )

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
