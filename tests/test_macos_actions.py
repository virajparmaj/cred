import plistlib
from pathlib import Path

from credusage.domain.models import ActionKind, ConnectionAction
from credusage.infrastructure.macos_actions import ActionRunner, LaunchAtLogin, safe_url


class FakeProcess:
    def poll(self):
        return None


class FakeLauncher:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return FakeProcess()


def test_only_https_exact_provider_hosts_are_opened():
    assert safe_url("https://chatgpt.com/codex/settings/usage")
    for value in (
        "http://chatgpt.com",
        "https://chatgpt.com.evil.test",
        "https://evil.test",
        "file:///tmp/file",
        "https://user:secret@chatgpt.com",
        "https://chatgpt.com:8443",
    ):
        assert not safe_url(value)


def test_dashboard_opens_with_argv_and_no_shell(tmp_path):
    launcher = FakeLauncher()
    runner = ActionRunner(tmp_path, popen=launcher)
    result = runner.run(
        "one",
        ConnectionAction("dashboard", "Dashboard", ActionKind.OPEN_URL, "https://chatgpt.com"),
    )
    assert result == "Opened provider page."
    assert launcher.calls[0][0] == ["/usr/bin/open", "https://chatgpt.com"]
    assert "shell" not in launcher.calls[0][1]


def test_demo_cannot_open_login_or_network(tmp_path):
    launcher = FakeLauncher()
    runner = ActionRunner(tmp_path, demo=True, popen=launcher)
    assert "disabled" in runner.run(
        "one",
        ConnectionAction("dashboard", "Dashboard", ActionKind.OPEN_URL, "https://chatgpt.com"),
    )
    assert not launcher.calls


def test_login_is_explicit_restricted_and_cooled_down(tmp_path):
    executable = tmp_path / "space and quote '" / "codex"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    launcher = FakeLauncher()
    runner = ActionRunner(tmp_path / "data", popen=launcher, monotonic=lambda: 10)
    action = ConnectionAction("login", "Login", ActionKind.LOGIN, str(executable), ("login",))
    assert "opened in Terminal" in runner.run("one", action)
    assert "recently opened" in runner.run("one", action)
    assert len(launcher.calls) == 1
    argv = launcher.calls[0][0]
    assert argv[:2] == ["/usr/bin/osascript", "-e"]
    assert "login" in argv[2]
    unsafe = ConnectionAction("login", "Login", ActionKind.LOGIN, str(executable), ("exec", "hi"))
    assert "Only the explicit" in runner.run("two", unsafe)
    assert len(launcher.calls) == 1


def test_app_open_requires_existing_absolute_app(tmp_path):
    runner = ActionRunner(tmp_path, popen=FakeLauncher())
    action = ConnectionAction("app", "Open", ActionKind.OPEN_APP, "Cursor.app")
    assert "not installed" in runner.run("one", action)


def test_login_item_is_isolated_and_removal_retains_other_files(tmp_path):
    helper = LaunchAtLogin(tmp_path)
    helper.path.parent.mkdir(parents=True)
    other = helper.path.parent / "some.other.product.plist"
    other.write_text("keep")
    helper.set_enabled(True, ("/Applications/CredUsage.app/Contents/MacOS/CredUsage",))
    document = plistlib.loads(helper.path.read_bytes())
    assert document["Label"] == "com.veer.credusage"
    assert document["RunAtLoad"] is True
    assert helper.path.stat().st_mode & 0o777 == 0o600
    helper.set_enabled(False, ())
    assert not helper.path.exists()
    assert other.read_text() == "keep"
