#!/bin/bash
set -euo pipefail
DESTINATION="$HOME/Applications"
REMOVE_DATA=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --destination) [[ $# -ge 2 && -n "$2" ]] || { echo "--destination needs a directory." >&2; exit 2; }; DESTINATION="$2"; shift 2 ;;
        --remove-data) REMOVE_DATA=1; shift ;;
        --help) echo "Usage: ./uninstall.sh [--destination DIRECTORY] [--remove-data]"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
APP="$DESTINATION/CredUsage.app"
if /usr/bin/pgrep -x CredUsage >/dev/null; then
    echo "Quit CredUsage before uninstalling it." >&2
    exit 1
fi
if [[ "$REMOVE_DATA" == 1 && -L "$HOME/.credusage" ]]; then
    echo "Refusing to remove a symlinked data directory." >&2
    exit 1
fi
if [[ -L "$APP" ]]; then
    echo "Refusing to remove a symlink: $APP" >&2
    exit 1
fi
if [[ -d "$APP" ]]; then
    BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")
    [[ "$BUNDLE_ID" == com.veer.credusage ]] || { echo "Refusing to remove an unrelated application." >&2; exit 1; }
fi
# Only this app's launch agent is removed; no vendor processes are touched.
AGENT="$HOME/Library/LaunchAgents/com.veer.credusage.plist"
if [[ -f "$AGENT" ]]; then
    /bin/launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null || true
    rm -f "$AGENT"
fi
if [[ -d "$APP" ]]; then
    rm -rf "$APP"
fi
if [[ "$REMOVE_DATA" == 1 ]]; then
    rm -rf "$HOME/.credusage"
fi
echo "CredUsage uninstalled. Data retained unless --remove-data was supplied."
