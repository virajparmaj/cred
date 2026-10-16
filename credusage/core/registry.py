"""Static registry of trusted built-in connectors; no dynamic plugin loading."""

from credusage.domain.contracts import UsageConnector


class ConnectorRegistry:
    def __init__(self, connectors: tuple[UsageConnector, ...]):
        self._connectors = {}
        for connector in connectors:
            key = connector.descriptor.integration_id
            if key in self._connectors:
                raise ValueError("Duplicate integration identifier")
            self._connectors[key] = connector

    def get(self, integration_id: str) -> UsageConnector:
        return self._connectors[integration_id]

    def descriptors(self):
        return tuple(connector.descriptor for connector in self._connectors.values())

    def close(self):
        for connector in self._connectors.values():
            try:
                connector.close()
            except Exception:
                pass
