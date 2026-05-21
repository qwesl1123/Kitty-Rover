#!/bin/bash
set -euo pipefail

URL="${1:-http://127.0.0.1:5000/face-screen}"

if [ "$(id -u)" = "0" ]; then
    echo "Do not run this as root. Run as franklin/local user."
    exit 1
fi

CHROMIUM_BIN="$(command -v chromium || command -v chromium-browser || true)"

if [ -z "$CHROMIUM_BIN" ]; then
    echo "chromium/chromium-browser not found"
    exit 1
fi

export XDG_RUNTIME_DIR="/tmp/rover-cage"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# Clean old kiosk session if one exists.
pkill -f "chromium.*127.0.0.1:5000" || true
pkill cage || true

# Turn screen on if the screen script exists and sudoers allows it.
sudo -n /home/franklin/rover/scripts/screen_on.sh >/dev/null 2>&1 || true

exec cage -- "$CHROMIUM_BIN" \
  --kiosk "$URL" \
  --password-store=basic \
  --user-data-dir=/tmp/rover-chromium-profile \
  --no-first-run \
  --disable-infobars \
  --disable-session-crashed-bubble
