# Build on an arm64 Mac using the project-local Python environment.
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
metadata = []
for dependency in (
    "rumps",
    "pyobjc-core",
    "pyobjc-framework-Cocoa",
    "pyobjc-framework-ServiceManagement",
    "pyinstaller",
):
    metadata += copy_metadata(dependency)
a = Analysis(
    [str(root / "credusage" / "__main__.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "credusage" / "assets"), "credusage/assets"),
        (str(root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"), "licenses/python"),
    ]
    + metadata,
    hiddenimports=["rumps", "AppKit", "Foundation", "ServiceManagement"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CredUsage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="CredUsage")
app = BUNDLE(
    collection,
    name="CredUsage.app",
    icon=str(root / "credusage" / "assets" / "AppIcon.icns"),
    bundle_identifier="com.veer.credusage",
    version="0.1.0",
    info_plist={
        "CFBundleName": "CredUsage",
        "CFBundleDisplayName": "CredUsage",
        "CFBundleIdentifier": "com.veer.credusage",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "CredUsage contributors. See THIRD_PARTY_NOTICES.md.",
    },
)
