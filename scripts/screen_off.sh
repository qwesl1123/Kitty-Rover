#!/bin/bash
set -euo pipefail

STATE_FILE="/run/rover_backlight_brightness"
BACKLIGHT_DIR="$(find /sys/class/backlight -mindepth 1 -maxdepth 1 | head -n 1)"

if [ -n "$BACKLIGHT_DIR" ] && [ -f "$BACKLIGHT_DIR/brightness" ]; then
    cat "$BACKLIGHT_DIR/brightness" > "$STATE_FILE"
    echo 0 > "$BACKLIGHT_DIR/brightness"
    exit 0
fi

# Fallback for console blanking
setterm --blank force < /dev/tty1 > /dev/tty1
