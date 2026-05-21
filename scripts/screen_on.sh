#!/bin/bash
set -euo pipefail

STATE_FILE="/run/rover_backlight_brightness"
BACKLIGHT_DIR="$(find /sys/class/backlight -mindepth 1 -maxdepth 1 | head -n 1)"

if [ -n "$BACKLIGHT_DIR" ] && [ -f "$BACKLIGHT_DIR/brightness" ]; then
    if [ -f "$STATE_FILE" ]; then
        VALUE="$(cat "$STATE_FILE")"
    else
        VALUE="$(cat "$BACKLIGHT_DIR/max_brightness")"
    fi

    # Avoid restoring to 0 by accident
    if [ "$VALUE" = "0" ]; then
        VALUE="$(cat "$BACKLIGHT_DIR/max_brightness")"
    fi

    echo "$VALUE" > "$BACKLIGHT_DIR/brightness"
    exit 0
fi

# Fallback for console unblanking
setterm --blank poke < /dev/tty1 > /dev/tty1
