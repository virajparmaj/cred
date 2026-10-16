#!/bin/bash
# Only CredUsage is installed. Building succeeds before an existing copy is replaced.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINATION="$HOME/Applications"
LAUNCH=0
LOGIN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --destination) [[ $# -ge 2 && -n "$2" ]] || { echo "--destination needs a directory." >&2; exit 2; }; DESTINATION="$2"; shift 2 ;;
        --launch) LAUNCH=1; shift ;;
        --login) LOGIN=1; shift ;;
        --help) echo "Usage: ./install.sh [--destination DIRECTORY] [--launch] [--login]"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
"$PROJECT_DIR/build_app.sh"
if /usr/bin/pgrep -x CredUsage >/dev/null; then
    echo "Quit CredUsage before replacing its application bundle." >&2
    exit 1
fi
mkdir -p "$DESTINATION"
APP="$DESTINATION/CredUsage.app"
if [[ -L "$APP" ]]; then
    echo "Refusing to replace a symlink: $APP" >&2
    exit 1
fi
STAGED="$(mktemp -d "$DESTINATION/.credusage-install.XXXXXX")"
cleanup() {
    # Restore the previous app if interrupted between the two directory moves.
    if [[ -d "$STAGED/previous.app" && ! -e "$APP" ]]; then
        mv "$STAGED/previous.app" "$APP"
    fi
    rm -rf "$STAGED"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
/usr/bin/ditto "$PROJECT_DIR/dist/CredUsage.app" "$STAGED/CredUsage.app"
/usr/bin/codesign --verify --deep --strict "$STAGED/CredUsage.app"
if [[ -e "$APP" ]]; then
    BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")
    [[ "$BUNDLE_ID" == com.veer.credusage ]] || { echo "Refusing to replace an unrelated application." >&2; exit 1; }
    mv "$APP" "$STAGED/previous.app"
fi
if ! mv "$STAGED/CredUsage.app" "$APP"; then
    [[ ! -d "$STAGED/previous.app" ]] || mv "$STAGED/previous.app" "$APP"
    exit 1
fi
if [[ "$LOGIN" == 1 ]]; then
    "$APP/Contents/MacOS/CredUsage" --launch-at-login enable
fi
if [[ "$LAUNCH" == 1 ]]; then
    /usr/bin/open "$APP"
fi
echo "Installed $APP"
