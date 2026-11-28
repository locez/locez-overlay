#!/usr/bin/env bash
set -euo pipefail
SHARED_DIR="$(dirname "${BASH_SOURCE[0]}")/../_shared"
bash "${SHARED_DIR}/check-elf.sh" /opt/bytedance/feishu/feishu
test -L /usr/bin/bytedance-feishu-stable
test -f /usr/share/applications/bytedance-feishu.desktop
