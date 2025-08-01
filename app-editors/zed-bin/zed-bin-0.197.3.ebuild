# Copyright 2025 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8
inherit desktop xdg-utils

DESCRIPTION="A highly performant, multiplayer code editor from the creators of Atom and Tree-sitter"
HOMEPAGE="https://zed.dev/"
SRC_URI="https://github.com/zed-industries/zed/releases/download/v${PV}/zed-linux-x86_64.tar.gz -> ${P}.tar.gz"
S="${WORKDIR}"/zed.app
LICENSE="GPL-3+"
# Dependent crate licenses
LICENSE+="
	Apache-2.0 Apache-2.0-with-LLVM-exceptions BSD-2 BSD CC0-1.0
	ISC LGPL-3 MIT MIT-0 MPL-2.0 UoI-NCSA openssl Unicode-3.0 ZLIB
"
SLOT="0"
KEYWORDS="~amd64"

RDEPEND="
	media-libs/alsa-lib
"

src_install() {
	insinto /opt/${PN}
	doins -r *
	fperms +x "/opt/${PN}/libexec/zed-editor"
	cat <<-EOF > "${T}/${PN}"
		#!/bin/bash
		export LD_LIBRARY_PATH="/opt/${PN}/lib":\$LD_LIBRARY_PATH
		exec "/opt/${PN}/libexec/zed-editor" "\$@"
	EOF
	dobin "${T}/${PN}"

	newicon -s 512 "${S}"/share/icons/hicolor/512x512/apps/zed.png zed-bin.png
	newicon -s 1024 "${S}"/share/icons/hicolor/1024x1024/apps/zed.png zed-bin.png
	sed -i "s/zed/zed-bin/g" "${S}/share/applications/zed.desktop"
	sed -i "s/Zed/Zed-bin/g" "${S}/share/applications/zed.desktop"
	domenu "${S}/share/applications/zed.desktop"
}

pkg_postinst() {
	xdg_icon_cache_update
}

pkg_postrm() {
	xdg_icon_cache_update
}
