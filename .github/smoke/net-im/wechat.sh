#!/usr/bin/env bash
set -euo pipefail
SHARED_DIR="$(dirname "${BASH_SOURCE[0]}")/../_shared"
test -x /usr/bin/wechat
bash "${SHARED_DIR}/check-elf.sh" /opt/wechat/wechat
test -f /usr/share/applications/wechat.desktop
