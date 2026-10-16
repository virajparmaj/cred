"""Native app entry point and explicit passive command-line tools."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone
from importlib import util
from pathlib import Path

from credusage import __version__


def parser():
    result = argparse.ArgumentParser(
        description="CredUsage — passive local AI allowance monitoring"
    )
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument(
        "--demo", action="store_true", help="Isolated fixtures; no external actions"
    )
    result.add_argument("--data-dir", type=Path, help="Override the app-owned data folder")
    result.add_argument(
        "--once", action="store_true", help="Collect once passively and output JSON"
    )
    result.add_argument(
        "--self-check", action="store_true", help="Validate imports/assets without I/O"
    )
    result.add_argument("--list-connections", action="store_true", help="List local connection IDs")
    result.add_argument("--diagnostics", type=Path, help="Export redacted diagnostics and exit")
    result.add_argument("--integration", action="append", help="Restrict a one-shot collection")
    result.add_argument("--launch-at-login", choices=("enable", "disable"))
    subs = result.add_subparsers(dest="command")
    ingest = subs.add_parser(
        "ingest-claude-statusline", help="Ingest passive Claude status-line stdin"
    )
    ingest.add_argument("--connection", required=True, help="Local Claude connection ID")
    return result


def self_check():
    import credusage
    from credusage.core.registry import ConnectorRegistry
    from credusage.domain.models import Connection

    assets = Path(credusage.__file__).parent / "assets"
    return {
        "app_version": __version__,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "platform": sys.platform,
        "assets_present": all(
            (assets / name).is_file()
            for name in (
                "MenuIconTemplate.png",
                "MenuIconTemplate@2x.png",
                "AppIcon.icns",
                "ProviderCodex.png",
                "ProviderCodexDark.png",
                "ProviderClaude.png",
                "ProviderCursor.png",
                "ProviderAntigravity.png",
            )
        ),
        "native_dependencies_present": util.find_spec("rumps") is not None
        and util.find_spec("AppKit") is not None,
        "core_without_appkit": "AppKit" not in sys.modules,
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def data_root(args):
    root = args.data_dir.expanduser().resolve() if args.data_dir else Path.home() / ".credusage"
    # Even an explicit --data-dir cannot accidentally share live and demo state.
    return root / "demo" if args.demo else root


def _ingest(args, root):
    from credusage.config import ConfigStore
    from credusage.providers.claude import ingest_statusline

    if args.demo:
        raise ValueError("Ingestion is disabled in demo mode")
    store = ConfigStore(root)
    if not store.path.exists():
        raise ValueError("Initialize CredUsage with --list-connections first")
    config = store.load()
    connection = next((c for c in config.connections if c.connection_id == args.connection), None)
    if (
        connection is None
        or connection.integration_id != "anthropic.claude"
        or not connection.enabled
    ):
        raise ValueError("Select an enabled Claude connection from --list-connections")
    payload = sys.stdin.buffer.read(256 * 1024 + 1)
    ingest_statusline(connection, payload, root, datetime.now(timezone.utc))
    return 0


def _once(controller):
    from credusage.infrastructure.storage import to_data

    controller.refresh(reason="manual")
    deadline = time.monotonic() + 25
    while not controller.coordinator.is_idle() and time.monotonic() < deadline:
        controller.coordinator.tick()
        time.sleep(0.03)
    print(json.dumps(to_data(controller.coordinator.states()), indent=2))
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    if args.self_check:
        print(json.dumps(self_check(), indent=2))
        return 0
    root = data_root(args)
    try:
        if args.command == "ingest-claude-statusline":
            return _ingest(args, root)
        from credusage.infrastructure.instance import InstanceLock

        with InstanceLock(root):
            from credusage.controller import Controller

            controller = Controller(root, demo=args.demo, integrations=args.integration)
            try:
                if args.list_connections:
                    print(
                        json.dumps(
                            [
                                {
                                    "connection_id": c.connection_id,
                                    "integration_id": c.integration_id,
                                    "label": c.label,
                                }
                                for c in controller.config.connections
                            ],
                            indent=2,
                        )
                    )
                elif args.launch_at_login:
                    controller.set_preferences(launch_at_login=args.launch_at_login == "enable")
                    print("Launch at login " + args.launch_at_login + "d.")
                elif args.diagnostics:
                    controller.export_diagnostics(args.diagnostics)
                    print("Redacted diagnostics exported.")
                elif args.once:
                    return _once(controller)
                else:
                    if sys.platform != "darwin":
                        raise ValueError("The native menu bar app requires macOS")
                    from credusage.ui.app import run_app

                    run_app(controller)
                return 0
            finally:
                controller.close()
    except (ValueError, OSError, RuntimeError):
        # Avoid printing source errors, paths, or accidentally credential-bearing strings.
        print(
            "CredUsage could not complete this action. Check configuration, permissions, and logs.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
