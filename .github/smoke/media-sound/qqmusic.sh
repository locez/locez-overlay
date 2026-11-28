#!/usr/bin/env bash
set -euo pipefail
SHARED_DIR="$(dirname "${BASH_SOURCE[0]}")/../_shared"
test -x /usr/bin/qqmusic
bash "${SHARED_DIR}/check-elf.sh" /opt/qqmusic/qqmusic
