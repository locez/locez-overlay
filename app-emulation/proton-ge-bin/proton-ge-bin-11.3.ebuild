# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

MY_PV="${PV/./-}"
OPT_DIR="/opt/proton-ge/GE-Proton${MY_PV}"
COMPAT_DIR="/usr/share/steam/compatibilitytools.d/proton-ge-${MY_PV}"

DESCRIPTION="GE-Proton compatibility tool for Steam Play (prebuilt x86_64 distribution)"
HOMEPAGE="https://github.com/GloriousEggroll/proton-ge-custom"
SRC_URI="https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton${MY_PV}/GE-Proton${MY_PV}.tar.gz"

S="${WORKDIR}/GE-Proton${MY_PV}"

LICENSE="Proton OFL-1.1 AOM-Patent-1.0"
SLOT="${PV}"
KEYWORDS="-* ~amd64"

# Graphics implementations are supplied by the host; Proton itself runs
# inside Steam Linux Runtime.
RDEPEND="
	virtual/opengl[abi_x86_32(-)]
	media-libs/vulkan-loader[abi_x86_32(-)]
"

RESTRICT="strip"
QA_PREBUILT="${OPT_DIR#/}/*"

src_install() {
	sed -i \
		-e "s|\"install_path\" \"\.\"|\"install_path\" \"${OPT_DIR}\"|" \
		compatibilitytool.vdf || die

	dodir "${OPT_DIR}"
	dodir "${COMPAT_DIR}"
	cp -a ./. "${ED}${OPT_DIR}/" || die
	insinto "${COMPAT_DIR}"
	doins compatibilitytool.vdf
	local item
	for item in *; do
		[[ ${item} == compatibilitytool.vdf ]] && continue
		dosym -r "${OPT_DIR}/${item}" "${COMPAT_DIR}/${item}" || die
	done
	fperms 0644 "${COMPAT_DIR}/compatibilitytool.vdf"
}
