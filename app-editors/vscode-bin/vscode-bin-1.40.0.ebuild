# Copyright 1999-2019 Gentoo Foundation
# Distributed under the terms of the GNU General Public License v2
# $Header: $

EAPI=6

inherit eutils pax-utils
HASHID='8795a9889db74563ddd43eb0a897a2384129a619'
DATEID='1573664143'
DESCRIPTION="Multiplatform Visual Studio Code from Microsoft"
HOMEPAGE="https://code.visualstudio.com"
SRC_URI="https://vscode.cdn.azure.cn/stable/${HASHID}/code-stable-${DATEID}.tar.gz -> ${P}.tar.gz"
RESTRICT="mirror strip"
LICENSE="Microsoft"
SLOT="0"
KEYWORDS="~amd64"
IUSE=""
DEPEND="
	sys-libs/glibc
	sys-devel/gcc
	>=x11-libs/gtk+-2.24.8-r1:2
	x11-libs/cairo
	x11-libs/pango
	dev-libs/atk
	x11-libs/gdk-pixbuf
	dev-libs/glib
	media-libs/freetype
	media-libs/fontconfig
	sys-apps/dbus
	x11-libs/libX11
	x11-libs/libxcb
	x11-libs/libXi
	x11-libs/libXcursor
	x11-libs/libXdamage
	x11-libs/libXrandr
	x11-libs/libXcomposite
	x11-libs/libXext
	x11-libs/libXfixes
	x11-libs/libXrender
	x11-libs/libXtst
	x11-libs/libXScrnSaver
	gnome-base/gconf
	dev-libs/nss
	dev-libs/nspr
	media-libs/alsa-lib
	net-print/cups
	dev-libs/expat
	x11-libs/libXinerama
	x11-libs/pixman
	media-libs/mesa
	media-libs/libpng
	sys-libs/zlib
	dev-libs/libffi
	app-arch/bzip2
	x11-libs/libXau
	x11-libs/libXdmcp
	dev-libs/dbus-glib
	dev-libs/libpcre
	net-libs/gnutls
	media-libs/harfbuzz
	x11-libs/libxshmfence
	x11-libs/libdrm
	x11-libs/libXxf86vm
	dev-libs/libbsd
	dev-libs/libunistring
	dev-libs/libtasn1
	dev-libs/nettle
	dev-libs/gmp
	net-dns/libidn2
	media-gfx/graphite2
"

RDEPEND="${DEPEND}"

pkg_setup() {
	S="${WORKDIR}/VSCode-linux-x64"
}

src_install(){
	pax-mark m code
	insinto "/opt/${PN}"
	doins -r *
	dosym "/opt/${PN}/code" "/usr/bin/vscode"
	make_wrapper "${PN}" "/opt/${PN}/code"
	domenu ${FILESDIR}/${PN}.desktop
	doicon ${FILESDIR}/${PN}.png
	fperms +x "/opt/${PN}/code"
	fperms +x "/opt/${PN}/libEGL.so"
	fperms +x "/opt/${PN}/libGLESv2.so"
	fperms +x "/opt/${PN}/libffmpeg.so"

	#fix Spawn EACESS bug #25848
	fperms +x "/opt/${PN}/resources/app/node_modules.asar.unpacked/vscode-ripgrep/bin/rg"

	insinto "/usr/share/licenses/${PN}"
	newins "resources/app/LICENSE.rtf" "LICENSE.rtf"
}

pkg_postinst(){
	elog "You may install some additional utils, so check them in:"
	elog "https://code.visualstudio.com/Docs/setup#_additional-tools"
}
