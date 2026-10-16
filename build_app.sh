#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    echo "CredUsage.app must be built on an arm64 Mac." >&2
    exit 1
fi
PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "Create .venv and install requirements-build.txt first; see README.md." >&2
    exit 1
fi
"$PYTHON" -c 'import sys, platform, PyInstaller, rumps, AppKit; assert sys.version_info >= (3, 11); assert platform.machine() == "arm64"'
cd "$PROJECT_DIR"
"$PROJECT_DIR/build_icon.sh"
export PYINSTALLER_CONFIG_DIR="$PROJECT_DIR/.cache/pyinstaller"
"$PYTHON" -m PyInstaller --noconfirm --clean --distpath "$PROJECT_DIR/dist" --workpath "$PROJECT_DIR/build" "$PROJECT_DIR/CredUsage.spec"
/usr/bin/plutil -lint "$PROJECT_DIR/dist/CredUsage.app/Contents/Info.plist"
/usr/bin/codesign --verify --deep --strict "$PROJECT_DIR/dist/CredUsage.app"
"$PROJECT_DIR/dist/CredUsage.app/Contents/MacOS/CredUsage" --self-check
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$PROJECT_DIR/dist/CredUsage.app" "$PROJECT_DIR/dist/CredUsage-macOS-arm64.zip"
echo "Built $PROJECT_DIR/dist/CredUsage.app"
echo "Packaged $PROJECT_DIR/dist/CredUsage-macOS-arm64.zip"
echo "Ad-hoc signed for local use. Developer ID signing and notarization are separate release gates."
