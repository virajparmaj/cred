"""Small connector and dependency contracts, independent of providers and Cocoa."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .errors import Diagnostic
from .models import (
    Capability,
    Connection,
    ConnectionAction,
    IdentityBinding,
    IntegrationDescriptor,
    QuotaObservation,
)


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class DiscoveryContext:
    home: Path
    deadline_monotonic: float
    allow_prompts: bool = False


@dataclass(frozen=True)
class DiscoveryResult:
    available: bool
    version: str | None = None
    message: str = ""
    capabilities: tuple[Capability, ...] = ()


@dataclass(frozen=True)
class CollectRequest:
    connection: Connection
    requested_at: datetime
    deadline_monotonic: float
    source_epoch: int = 0
    reason: str = "periodic"


@dataclass(frozen=True)
class CollectionResult:
    connection_id: str
    generation: int
    source_epoch: int
    binding: IdentityBinding = field(default_factory=IdentityBinding)
    observations: tuple[QuotaObservation, ...] = ()
    completeness: Completeness = Completeness.COMPLETE
    removed_keys: tuple[str, ...] = ()
    capabilities: tuple[Capability, ...] = ()
    plan: str | None = None
    diagnostic: Diagnostic | None = None


class UsageConnector(Protocol):
    descriptor: IntegrationDescriptor

    def discover(self, context: DiscoveryContext) -> DiscoveryResult: ...
    def collect(self, request: CollectRequest) -> CollectionResult: ...
    def actions(self, connection: Connection) -> tuple[ConnectionAction, ...]: ...
    def close(self) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...


class SnapshotRepository(Protocol):
    def load(self, connection: Connection) -> object: ...
    def save(self, snapshot: object) -> None: ...
