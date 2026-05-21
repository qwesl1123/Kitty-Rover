#!/bin/bash
set -euo pipefail

logger -t face-screen "starting face screen script"

LOG_FILE="/tmp/rover-face-screen.log"
exec >> "$LOG_FILE" 2>&1
set -x

echo "===== face screen start $(date) ====="
echo "USER=$(id)"
echo "TTY=$(tty || true)"
echo "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-unset}"
echo "XDG_SESSION_ID=${XDG_SESSION_ID:-unset}"
echo "XDG_SEAT=${XDG_SEAT:-unset}"

URL="${1:-http://127.0.0.1:5000/face-screen}"

CHROMIUM_BIN="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROMIUM_BIN" ]; then
    echo "chromium/chromium-browser not found"
    exit 1
fi

pkill -u franklin chromium || true
pkill -u franklin cage || true

exec cage -- "$CHROMIUM_BIN" \
  --kiosk "$URL" \
  --password-store=basic \
  --user-data-dir="${XDG_RUNTIME_DIR}/rover-chromium-profile" \
  --no-first-run \
  --disable-infobars \
  --disable-session-crashed-bubble
