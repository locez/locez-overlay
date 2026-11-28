#!/usr/bin/env bash
# Reusable ELF shared-library completeness check for prebuilt binary packages.
# Usage: check-elf.sh [--rpath DIR]... <binary_path>...
set -euo pipefail

extra_rpath=""
bins=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rpath)
      shift
      [[ $# -gt 0 ]] || { echo "Missing argument for --rpath" >&2; exit 2; }
      extra_rpath="${extra_rpath:+${extra_rpath}:}$1"
      ;;
    *)
      bins+=("$1")
      ;;
  esac
  shift
done

if [[ ${#bins[@]} -eq 0 ]]; then
  echo "Usage: check-elf.sh [--rpath DIR]... <binary_path>..." >&2
  exit 2
fi

failed=0
for bin in "${bins[@]}"; do
  if [[ ! -f "$bin" || ! -x "$bin" ]]; then
    echo "FAIL: binary not executable or missing: $bin" >&2
    failed=1
    continue
  fi
  magic="$(od -An -tx1 -N4 "$bin" | tr -d ' \n')"
  if [[ "$magic" != "7f454c46" ]]; then
    echo "FAIL: not an ELF binary: $bin" >&2
    failed=1
    continue
  fi
  bin_dir="$(dirname "$bin")"
  lib_path="${bin_dir}${extra_rpath:+:${extra_rpath}}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
  missing="$(LD_LIBRARY_PATH="$lib_path" ldd "$bin" 2>&1 | grep "=> not found" || true)"
  if [[ -n "$missing" ]]; then
    echo "FAIL: unresolved shared libraries for $bin:" >&2
    echo "$missing" >&2
    failed=1
  else
    echo "OK: all shared libraries resolved for $bin"
  fi
done

exit "$failed"
