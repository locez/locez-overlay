#!/usr/bin/env bash
set -euo pipefail
python3 -c 'from pure_protobuf.annotations import Field; from pure_protobuf.message import BaseMessage'
