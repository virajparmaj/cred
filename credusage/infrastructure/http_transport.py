"""HTTP boundary for future documented collectors; no production HTTP collector."""

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    def get(
        self, url: str, *, headers: Mapping[str, str], timeout: float, maximum_bytes: int
    ) -> HttpResponse: ...
