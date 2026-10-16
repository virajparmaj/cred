#!/bin/bash
# Generate every native/Retina icon size from the checked-in 1024px master.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MASTER="$PROJECT_DIR/credusage/assets/AppIcon.png"
ICONSET="$PROJECT_DIR/build/AppIcon.iconset"
if [[ "$(uname -s)" != Darwin ]]; then
    echo "Icon generation requires macOS sips and iconutil." >&2
    exit 1
fi
SIZE=$(/usr/bin/sips -g pixelWidth -g pixelHeight "$MASTER")
if [[ "$SIZE" != *"pixelWidth: 1024"* || "$SIZE" != *"pixelHeight: 1024"* ]]; then
    echo "AppIcon.png must be a square 1024px master." >&2
    exit 1
fi
mkdir -p "$ICONSET"
for POINTS in 16 32 128 256 512; do
    /usr/bin/sips -z "$POINTS" "$POINTS" "$MASTER" --out "$ICONSET/icon_${POINTS}x${POINTS}.png" >/dev/null
    PIXELS=$((POINTS * 2))
    /usr/bin/sips -z "$PIXELS" "$PIXELS" "$MASTER" --out "$ICONSET/icon_${POINTS}x${POINTS}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$ICONSET" -o "$PROJECT_DIR/credusage/assets/AppIcon.icns"
echo "Generated native app icon from AppIcon.png"
