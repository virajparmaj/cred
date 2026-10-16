"""Separate visible consumer products with truthful dashboard-only capabilities."""

from pathlib import Path

from credusage.domain.contracts import DiscoveryResult
from credusage.domain.errors import ErrorCode
from credusage.domain.models import (
    ActionKind,
    Capability,
    ConnectionAction,
    IntegrationDescriptor,
    SourcePolicy,
    ValidationStatus,
)
from credusage.infrastructure.processes import failure, find_executable


class DashboardConnector:
    def __init__(
        self,
        integration_id,
        vendor,
        label,
        url,
        *,
        app_names=(),
        cli=None,
        evidence="No supported passive numerical source is verified for this consumer product.",
    ):
        self.descriptor = IntegrationDescriptor(
            integration_id,
            vendor,
            label,
            capabilities=(Capability.DISCOVERY, Capability.DASHBOARD),
            source_policy=SourcePolicy.DASHBOARD_ONLY,
            validation=ValidationStatus.FIXTURE_TESTED,
            evidence=evidence,
        )
        self.url = url
        self.app_names = app_names
        self.cli = cli

    def discover(self, context):
        installed = any(
            (base / name).exists()
            for base in (Path("/Applications"), context.home / "Applications")
            for name in self.app_names
        )
        installed = installed or bool(self.cli and find_executable(self.cli))
        return DiscoveryResult(
            installed,
            message=(
                "Application detected. "
                if installed
                else "Application not detected; web dashboard available. "
            )
            + self.descriptor.evidence,
            capabilities=self.descriptor.capabilities,
        )

    def collect(self, request):
        raise failure(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            self.descriptor.evidence + " Open the product dashboard to view usage.",
        )

    def actions(self, connection):
        return (
            ConnectionAction(
                "dashboard", "Open " + self.descriptor.display_name, ActionKind.OPEN_URL, self.url
            ),
        )

    def close(self):
        pass


def dashboard_connectors():
    return (
        DashboardConnector(
            "cursor.editor",
            "cursor",
            "Cursor",
            "https://cursor.com/dashboard",
            app_names=("Cursor.app",),
            cli="cursor",
            evidence="Personal/free-account passive usage has not been verified. Enterprise API availability does not establish personal-account access.",
        ),
        DashboardConnector(
            "openai.chatgpt",
            "openai",
            "ChatGPT",
            "https://chatgpt.com/",
            app_names=("ChatGPT.app",),
            evidence="No supported numerical consumer ChatGPT allowance source is verified. Codex quota readings are separate.",
        ),
        DashboardConnector(
            "google.gemini_app",
            "google",
            "Gemini",
            "https://gemini.google.com/",
            app_names=("Gemini.app",),
            evidence="No supported numerical consumer Gemini allowance source is verified. Antigravity readings are separate.",
        ),
    )
