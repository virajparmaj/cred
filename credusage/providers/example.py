"""Production-disabled connector proving heterogeneous display and failure states."""

from datetime import timedelta
from decimal import Decimal

from credusage.domain.contracts import CollectionResult, DiscoveryResult
from credusage.domain.errors import ErrorCode
from credusage.domain.models import (
    Availability,
    Capability,
    IdentityBinding,
    IntegrationDescriptor,
    ObservationBasis,
    Provenance,
    QuotaKind,
    QuotaObservation,
    QuotaState,
    QuotaWindow,
    SourcePolicy,
    Unit,
    WindowKind,
)
from credusage.infrastructure.clock import SystemClock
from credusage.infrastructure.processes import failure


class ExampleConnector:
    descriptor = IntegrationDescriptor(
        "example.demo",
        "example",
        "Example fixtures",
        capabilities=(Capability.QUOTAS,),
        source_policy=SourcePolicy.LOCAL_TELEMETRY,
        evidence="Synthetic demonstration only; disabled in production.",
    )

    def __init__(self, clock=None, *, enabled=False):
        self.clock = clock or SystemClock()
        self.enabled = enabled

    def discover(self, context):
        return DiscoveryResult(
            self.enabled,
            message="Synthetic demonstration data." if self.enabled else "Disabled in production.",
            capabilities=self.descriptor.capabilities,
        )

    def collect(self, request):
        if not self.enabled:
            raise failure(
                ErrorCode.UNSUPPORTED_CAPABILITY, "Example connector is disabled in production."
            )
        if request.reason == "demo-failure":
            raise failure(
                ErrorCode.NETWORK_UNAVAILABLE,
                "Demonstration refresh failure. Previous observations remain visible.",
            )
        now = self.clock.now()
        binding = IdentityBinding(
            subject_id="fixture-subject", account_id="fixture-account", verified=True
        )
        provenance = Provenance(
            "example.synthetic", SourcePolicy.LOCAL_TELEMETRY, "1", now, now, ObservationBasis.DEMO
        )
        common = dict(
            connection_id=request.connection.connection_id,
            generation=request.connection.generation,
            provenance=provenance,
            binding=binding,
            fresh_until=now + timedelta(minutes=10),
        )
        observations = (
            QuotaObservation(
                bucket_key="example:shared:short",
                label="Shared model pool · 3h",
                used_percent=Decimal("82.5"),
                window=QuotaWindow(
                    WindowKind.ROLLING, "demo-short", 10800, resets_at=now + timedelta(hours=2)
                ),
                availability=Availability.KNOWN,
                state=QuotaState.LOW,
                model_ids=("example-a", "example-b"),
                shared_pool_id="example-shared-pool",
                sharing_evidence="Synthetic contract explicitly defines one shared pool.",
                **common,
            ),
            QuotaObservation(
                bucket_key="example:shared:long",
                label="Shared model pool · 14d",
                used_percent=Decimal("27"),
                window=QuotaWindow(
                    WindowKind.FIXED, "demo-long", 1209600, resets_at=now + timedelta(days=9)
                ),
                availability=Availability.KNOWN,
                state=QuotaState.AVAILABLE,
                model_ids=("example-a", "example-b"),
                shared_pool_id="example-shared-pool",
                sharing_evidence="Synthetic contract explicitly defines one shared pool.",
                **common,
            ),
            QuotaObservation(
                bucket_key="example:feature",
                label="Research available · qualitative",
                kind=QuotaKind.FEATURE,
                unit=Unit.UNKNOWN,
                availability=Availability.KNOWN,
                state=QuotaState.AVAILABLE,
                feature="research",
                **common,
            ),
            QuotaObservation(
                bucket_key="example:unknown",
                label="Image quota · source did not report a value",
                kind=QuotaKind.FEATURE,
                unit=Unit.REQUESTS,
                availability=Availability.UNKNOWN,
                **common,
            ),
            QuotaObservation(
                bucket_key="example:credit",
                label="Credits · provider units",
                kind=QuotaKind.CREDIT,
                unit=Unit.CREDITS,
                remaining=Decimal("12.123456789123456789"),
                credit_unit="example credits",
                availability=Availability.KNOWN,
                state=QuotaState.AVAILABLE,
                **common,
            ),
        )
        return CollectionResult(
            request.connection.connection_id,
            request.connection.generation,
            request.source_epoch,
            binding=binding,
            observations=observations,
            capabilities=self.descriptor.capabilities,
            plan="Demo",
        )

    def actions(self, connection):
        return ()

    def close(self):
        pass
