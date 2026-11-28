#!/usr/bin/env bash
set -euo pipefail
SHARED_DIR="$(dirname "${BASH_SOURCE[0]}")/../_shared"
bash "${SHARED_DIR}/check-elf.sh" /opt/baidunetdisk/baidunetdisk
test -L /opt/bin/baidunetdisk
test -f /usr/share/applications/baidunetdisk.desktop
