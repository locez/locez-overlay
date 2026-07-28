# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

MY_PV="${PV/_p/-}"
OPT_DIR="/opt/dwproton/${MY_PV}-x86_64"
COMPAT_DIR="/usr/share/steam/compatibilitytools.d/dwproton-${MY_PV}-x86_64"

DESCRIPTION="Dawn Winery's custom Proton compatibility tool (prebuilt x86_64 distribution)"
HOMEPAGE="https://dawn.wine/dawn-winery/dwproton"
SRC_URI="https://dawn.wine/dawn-winery/dwproton/releases/download/dwproton-${MY_PV}/dwproton-${MY_PV}-x86_64.tar.xz"

S="${WORKDIR}/dwproton-${MY_PV}-x86_64"

LICENSE="Proton OFL-1.1 AOM-Patent-1.0"
SLOT="${PV}"
KEYWORDS="-* ~amd64"

BDEPEND="dev-util/patchelf"

# The graphics implementations are supplied by the host; Proton itself runs
# inside Steam Linux Runtime.
RDEPEND="
	virtual/opengl[abi_x86_32(-)]
	media-libs/vulkan-loader[abi_x86_32(-)]
"

RESTRICT="strip"
QA_PREBUILT="${OPT_DIR#/}/*"

src_prepare() {
	default

	# Remove build-host RPATHs while keeping bundled sibling libraries usable.
	local file
	for file in \
		files/lib/i386-linux-gnu/libsharpyuv.so.0.1.1 \
		files/lib/i386-linux-gnu/libwebp.so.7.1.10 \
		files/lib/i386-linux-gnu/libwebpdecoder.so.3.1.10 \
		files/lib/i386-linux-gnu/libwebpdemux.so.2.0.16 \
		files/lib/i386-linux-gnu/libwebpmux.so.3.1.1
	do
		patchelf --set-rpath '$ORIGIN' "${file}" || die
	done

	for file in protonfixes/files/bin/pgrep protonfixes/files/bin/pkill; do
		patchelf --set-rpath '$ORIGIN/../lib/x86_64-linux-gnu' "${file}" || die
	done
}

src_install() {
	sed -i \
		-e "s|\"install_path\" \"\.\"|\"install_path\" \"${OPT_DIR}\"|" \
		compatibilitytool.vdf || die

	dodir "${OPT_DIR}"
	cp -a ./. "${ED}${OPT_DIR}/" || die
	insinto "${COMPAT_DIR}"
	doins compatibilitytool.vdf
	fperms 0644 "${COMPAT_DIR}/compatibilitytool.vdf"
}
