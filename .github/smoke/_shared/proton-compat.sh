#!/usr/bin/env bash
# Shared smoke test helper for Steam Play Proton compatibility tools.
# Usage: check_proton_compat <compat_glob> <opt_prefix>
set -euo pipefail

check_proton_compat() {
	local compat_glob="$1"
	local opt_prefix="$2"

	shopt -s nullglob
	local links=(/usr/share/steam/compatibilitytools.d/${compat_glob})
	if [[ ${#links[@]} -lt 1 ]]; then
		echo "Expected at least one compatibilitytools.d symlink matching ${compat_glob}" >&2
		return 1
	fi

	local link
	for link in "${links[@]}"; do
		test -L "${link}"
		test -d "${link}"
	done

	local vdfs=(${opt_prefix}/*/compatibilitytool.vdf)
	if [[ ${#vdfs[@]} -lt 1 ]]; then
		echo "Expected compatibilitytool.vdf under ${opt_prefix}" >&2
		return 1
	fi

	grep -q "${opt_prefix}/" "${vdfs[@]}"
}
