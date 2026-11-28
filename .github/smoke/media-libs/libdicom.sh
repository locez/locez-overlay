#!/usr/bin/env bash
set -euo pipefail
pkg-config --exists libdicom
pkg-config --libs libdicom
