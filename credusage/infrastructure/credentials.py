"""Vendor CLIs retain credential ownership; integrations receive references only."""

from typing import Protocol


class CredentialAccess(Protocol):
    def available(self, reference: str) -> bool: ...


class VendorOwnedCredentials:
    def available(self, reference: str) -> bool:
        # A configured reference is not proof of authentication. Ask the vendor CLI.
        return False
