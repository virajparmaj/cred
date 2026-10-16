"""Safe diagnostics: upstream payloads must never become exception messages."""

import math
from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    UNSUPPORTED_AUTH_MODE = "UNSUPPORTED_AUTH_MODE"
    COLLECTOR_RATE_LIMITED = "COLLECTOR_RATE_LIMITED"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_SCHEMA_CHANGED = "SOURCE_SCHEMA_CHANGED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    INVALID_LOCAL_DATA = "INVALID_LOCAL_DATA"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    STORAGE_ERROR = "STORAGE_ERROR"


@dataclass(frozen=True)
class Diagnostic:
    code: ErrorCode
    message: str
    retry_after_seconds: float | None = None

    def __post_init__(self):
        value = self.retry_after_seconds
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("Invalid retry interval")


class CollectionError(Exception):
    def __init__(self, diagnostic: Diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic.code.value)
