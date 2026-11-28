#!/usr/bin/env bash
set -euo pipefail
pkg-config --exists openslide
pkg-config --libs openslide
