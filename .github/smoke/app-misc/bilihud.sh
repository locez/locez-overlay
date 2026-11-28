#!/usr/bin/env bash
set -euo pipefail
python3 -c 'import bilihud'
test -f /usr/share/applications/bilihud.desktop
