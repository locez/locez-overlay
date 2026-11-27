#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_shared/proton-compat.sh"
check_proton_compat "proton-ge-*" "/opt/proton-ge"
