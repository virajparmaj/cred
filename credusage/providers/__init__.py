"""Built-in providers. Consumer allowances have independent integration identities."""

from .antigravity import AntigravityConnector
from .claude import ClaudeConnector
from .codex import CodexConnector
from .dashboard import dashboard_connectors
from .example import ExampleConnector


def built_in_connectors(clock=None, on_invalidate=None, root=None, demo=False):
    if demo:
        return (ExampleConnector(clock, enabled=True),)
    return (
        CodexConnector(clock, on_invalidate),
        ClaudeConnector(clock, root),
        AntigravityConnector(clock),
        *dashboard_connectors(),
    )


__all__ = [
    "built_in_connectors",
    "CodexConnector",
    "ClaudeConnector",
    "AntigravityConnector",
    "ExampleConnector",
]
