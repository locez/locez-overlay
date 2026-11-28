#!/usr/bin/env bash
set -euo pipefail
python3 -c 'import kotonoha'
test -f /usr/share/applications/kotonoha.desktop
