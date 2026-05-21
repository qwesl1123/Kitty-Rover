#!/bin/bash
set -euo pipefail

pkill -f "chromium.*127.0.0.1:5000" || true
pkill cage || true

echo "face screen stopped"
