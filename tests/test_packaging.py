"""Build/installer contract checks; never install, launch GUI, or register login."""

from __future__ import annotations

import ast
import json
import plistlib
import shutil
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_metadata_and_assets_are_self_contained():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["project"]["requires-python"] == ">=3.11"
    assert config["project"]["scripts"]["credusage"] == "credusage.__main__:main"
    assert all("sys_platform == 'darwin'" in item for item in config["project"]["dependencies"])
    for pattern in ("assets/*.png", "assets/*.icns", "assets/*.svg"):
        assert pattern in config["tool"]["setuptools"]["package-data"]["credusage"]
    png = (ROOT / "credusage/assets/AppIcon.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1024, 1024)
    icon = (ROOT / "credusage/assets/AppIcon.icns").read_bytes()
    assert icon[:4] == b"icns" and struct.unpack(">I", icon[4:8])[0] == len(icon)


def test_spec_bundles_arm64_runtime_with_correct_native_identity():
    tree = ast.parse((ROOT / "CredUsage.spec").read_text())
    calls = {
        node.func.id: node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    exe = {arg.arg: arg.value for arg in calls["EXE"].keywords}
    bundle = {arg.arg: arg.value for arg in calls["BUNDLE"].keywords}
    assert ast.literal_eval(exe["target_arch"]) == "arm64"
    assert ast.literal_eval(exe["exclude_binaries"]) is True  # onedir, not extraction at launch
    assert "COLLECT" in calls
    assert ast.literal_eval(bundle["bundle_identifier"]) == "com.veer.credusage"
    info = ast.literal_eval(bundle["info_plist"])
    assert info["LSUIElement"] is True
    assert info["NSHighResolutionCapable"] is True
    source = (ROOT / "CredUsage.spec").read_text()
    assert "cred-claude" not in source and "cred-codex" not in source
    assert "THIRD_PARTY_NOTICES.md" in source and "copy_metadata" in source


@pytest.mark.parametrize("script", ["build_app.sh", "install.sh", "uninstall.sh"])
def test_shell_scripts_have_valid_syntax(script):
    subprocess.run(["/bin/bash", "-n", str(ROOT / script)], check=True, capture_output=True)


@pytest.mark.parametrize("script", ["install.sh", "uninstall.sh"])
def test_script_help_has_no_side_effects(script, tmp_path):
    # --help exits during option parsing, before a build or any filesystem operation.
    result = subprocess.run(
        ["/bin/bash", str(ROOT / script), "--help"],
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and "Usage:" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_installer_requires_explicit_startup_options_and_builds_before_replace():
    install = (ROOT / "install.sh").read_text()
    assert "LAUNCH=0" in install and "LOGIN=0" in install
    assert install.index('"$PROJECT_DIR/build_app.sh"') < install.index('mv "$APP"')
    assert "com.veer.credusage" in install and "Refusing to replace a symlink" in install
    assert "pgrep -x CredUsage" in install and "trap cleanup EXIT" in install
    assert 'mv "$STAGED/previous.app" "$APP"' in install
    uninstall = (ROOT / "uninstall.sh").read_text()
    assert "REMOVE_DATA=0" in uninstall and "--remove-data" in uninstall
    assert "pgrep -x CredUsage" in uninstall
    assert (
        "--destination needs a directory." in install
        and "--destination needs a directory." in uninstall
    )
    assert "com.veer.credusage.plist" in uninstall
    assert "credclaude" not in uninstall.lower() and "credcodex" not in uninstall.lower()


def test_attribution_keeps_full_claude_permission_and_codex_license_gate():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    assert "Copyright (c) 2026 virajparmaj" in notices
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in notices
    assert "no license file" in notices


@pytest.mark.skipif(sys.platform != "darwin", reason="native bundle exists only on macOS")
def test_built_bundle_cli_and_identity_if_available(tmp_path):
    app = ROOT / "dist/CredUsage.app"
    if not app.is_dir():
        pytest.skip("run build_app.sh for native bundle smoke")
    relocated = tmp_path / "Different path with spaces" / "CredUsage.app"
    shutil.copytree(app, relocated, symlinks=True)
    app = relocated
    with (app / "Contents/Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    assert info["CFBundleIdentifier"] == "com.veer.credusage"
    assert info["LSUIElement"] is True
    command = app / "Contents/MacOS" / info["CFBundleExecutable"]
    result = subprocess.run(
        [str(command), "--self-check"],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["assets_present"] is True
    assert report["native_dependencies_present"] is True
    assert report["core_without_appkit"] is True
    assert report["frozen"] is True
    assert report["architecture"] == "arm64"
    assert not (tmp_path / ".credusage").exists()
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="native Mach-O linkage check")
def test_built_bundle_has_no_external_homebrew_or_virtualenv_linkage():
    frameworks = ROOT / "dist/CredUsage.app/Contents/Frameworks"
    if not frameworks.is_dir():
        pytest.skip("run build_app.sh for native linkage inspection")
    inspected = 0
    for path in frameworks.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic not in (
            b"\xcf\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
        ):
            continue
        inspected += 1
        result = subprocess.run(
            ["/usr/bin/otool", "-L", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            assert not dependency.startswith("/") or dependency.startswith(
                ("/System/", "/usr/lib/")
            ), (path, dependency)
    assert inspected > 0
