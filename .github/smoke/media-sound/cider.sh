#!/usr/bin/env bash
set -euo pipefail
SHARED_DIR="$(dirname "${BASH_SOURCE[0]}")/../_shared"
bash "${SHARED_DIR}/check-elf.sh" /opt/cider/Cider
test -L /usr/bin/cider
test -f /usr/share/applications/cider.desktop
