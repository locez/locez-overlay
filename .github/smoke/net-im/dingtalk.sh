#!/usr/bin/env bash
set -euo pipefail
SHARED_DIR="$(dirname "${BASH_SOURCE[0]}")/../_shared"
test -L /usr/bin/dingtalk
bash "${SHARED_DIR}/check-elf.sh" /opt/apps/com.alibabainc.dingtalk/files/[0-9]*/com.alibabainc.dingtalk
test -f /usr/share/applications/com.alibabainc.dingtalk.desktop
