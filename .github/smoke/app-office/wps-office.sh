#!/usr/bin/env bash
set -euo pipefail
SHARED_DIR="$(dirname "${BASH_SOURCE[0]}")/../_shared"
test -x /usr/bin/wps
bash "${SHARED_DIR}/check-elf.sh" /opt/kingsoft/wps-office/office6/wps
compgen -G '/usr/share/applications/wps-office-*.desktop' >/dev/null
