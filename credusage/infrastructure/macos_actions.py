"""User-initiated macOS actions; no shell execution of provider input."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from credusage.domain.models import ActionKind, ConnectionAction

BUNDLE_ID = "com.veer.credusage"
SAFE_WEB_HOSTS = frozenset(
    {
        "chatgpt.com",
        "www.chatgpt.com",
        "openai.com",
        "help.openai.com",
        "developers.openai.com",
        "learn.chatgpt.com",
        "platform.openai.com",
        "claude.ai",
        "code.claude.com",
        "support.claude.com",
        "console.anthropic.com",
        "cursor.com",
        "www.cursor.com",
        "www.cursor.sh",
        "antigravity.google",
        "gemini.google.com",
        "support.google.com",
        "aistudio.google.com",
    }
)


def safe_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and url.hostname in SAFE_WEB_HOSTS
            and not url.username
            and not url.password
            and url.port in (None, 443)
            and not any(ord(char) < 32 for char in value)
        )
    except ValueError:
        return False


class ActionRunner:
    """Open actions promptly. Interactive login always belongs to the vendor CLI."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        demo: bool = False,
        popen=subprocess.Popen,
        monotonic=time.monotonic,
    ):
        self.root = root or Path.home() / ".credusage"
        self.demo = demo
        self._popen = popen
        self._monotonic = monotonic
        self._login_after: dict[str, float] = {}
        self._lock = threading.Lock()
        self._processes: list[object] = []

    def _spawn(self, argv: list[str]) -> None:
        process = self._popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._processes = [child for child in self._processes if child.poll() is None]
        self._processes.append(process)

    def run(self, connection_id: str, action: ConnectionAction) -> str:
        if self.demo:
            return "Demo mode: external actions are disabled."
        try:
            if action.kind in (ActionKind.OPEN_URL, ActionKind.HELP):
                if not safe_url(action.target):
                    return (
                        action.target
                        if action.kind == ActionKind.HELP and not "://" in action.target
                        else "This URL is not an approved provider destination."
                    )
                self._spawn(["/usr/bin/open", action.target])
                return "Opened provider page."
            if action.kind == ActionKind.OPEN_APP:
                app = Path(action.target)
                if not app.is_absolute() or app.suffix != ".app" or not app.is_dir():
                    return "The provider app is not installed at that location."
                self._spawn(["/usr/bin/open", "-a", str(app)])
                return "Opened provider app."
            if action.kind == ActionKind.LOGIN:
                return self._login(connection_id, action)
            return "This action is unavailable."
        except (OSError, ValueError):
            return "The action could not be opened. Check the installed app or executable path."

    def _login(self, connection_id: str, action: ConnectionAction) -> str:
        # Keep login restricted to the vendor's explicit authentication command.
        if action.argv not in (("login",), ("login", "--device-auth")):
            return "Only the explicit Codex login command is supported."
        executable = Path(action.target) if Path(action.target).is_absolute() else None
        if executable is None:
            found = shutil.which(action.target)
            executable = Path(found) if found else None
        if (
            executable is None
            or executable.name not in ("codex", "codex.exe")
            or not executable.is_file()
            or not os.access(executable, os.X_OK)
        ):
            return "Codex is unavailable. Set its absolute executable path in Settings."
        with self._lock:
            now = self._monotonic()
            if now < self._login_after.get(connection_id, 0):
                return "A login was recently opened. Finish it in Terminal, then refresh."
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            marker = self.root / (
                "login-" + hashlib.sha256(connection_id.encode()).hexdigest()[:20] + ".pending"
            )
            if marker.exists() and time.time() - marker.stat().st_mtime < 300:
                return "A login is pending in Terminal. Finish it, then refresh."
            marker.touch(mode=0o600, exist_ok=True)
            marker.chmod(0o600)
            command = shlex.join([str(executable), *action.argv])
            # The marker is app-owned. Paths and arguments are shell-quoted individually.
            shell_command = f"{command}; /bin/rm -f {shlex.quote(str(marker))}"
            apple_script = (
                'tell application "Terminal"\nactivate\ndo script '
                + json.dumps(shell_command)
                + "\nend tell"
            )
            try:
                self._spawn(["/usr/bin/osascript", "-e", apple_script])
            except OSError:
                marker.unlink(missing_ok=True)
                raise
            self._login_after[connection_id] = now + 60
        return "Codex login opened in Terminal. Complete it there, then refresh CredUsage."

    def open_logs(self) -> str:
        if self.demo:
            return "Demo mode logs remain in the isolated demo data folder."
        log = self.root / "logs" / "credusage.log"
        if not log.is_file():
            return "No log file has been written yet."
        try:
            self._spawn(["/usr/bin/open", "-t", str(log)])
            return "Opened CredUsage log."
        except OSError:
            return "The log could not be opened."

    def close(self) -> None:
        # External browser / login processes are user-owned once opened.
        self._processes.clear()


class LaunchAtLogin:
    """Manage only CredUsage's own launch agent, effective at the next login."""

    def __init__(self, home: Path | None = None):
        self.path = (home or Path.home()) / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"

    def set_enabled(self, enabled: bool, argv: list[str] | tuple[str, ...]) -> None:
        if not enabled:
            self.path.unlink(missing_ok=True)
            return
        if not argv or not Path(argv[0]).is_absolute():
            raise ValueError("Launch at login needs an absolute executable path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = plistlib.dumps(
            {
                "Label": BUNDLE_ID,
                "ProgramArguments": list(argv),
                "RunAtLoad": True,
                "ProcessType": "Interactive",
            },
            fmt=plistlib.FMT_XML,
        )
        fd, temporary = tempfile.mkstemp(prefix=".credusage-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
