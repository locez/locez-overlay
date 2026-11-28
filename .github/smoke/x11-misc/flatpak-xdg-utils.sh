#!/usr/bin/env bash
set -euo pipefail
test -x /usr/bin/flatpak-spawn
test -x /usr/lib/flatpak-xdg-utils/xdg-open
