#!/usr/bin/env bash
set -euo pipefail
test -d /usr/share/fonts/wps-fonts
ls /usr/share/fonts/wps-fonts/*.ttf /usr/share/fonts/wps-fonts/*.TTF >/dev/null 2>&1
