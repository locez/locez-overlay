# Copyright 2020 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=7

inherit unpacker eutils desktop

DESCRIPTION="QQ Music"
HOMEPAGE="https://y.qq.com"
SRC_URI="http://dldir1.qq.com/music/clntupate/linux/deb/qqmusic_${PV}_amd64.deb -> ${P}_amd64.deb"

LICENSE=""
SLOT="0"
KEYWORDS="~amd64"

RDEPEND="
		app-accessibility/at-spi2-atk
		app-accessibility/at-spi2-core
		app-arch/bzip2
		app-arch/lz4
		app-arch/zstd
		dev-libs/atk
		dev-libs/expat
		dev-libs/fribidi
		dev-libs/glib
		dev-libs/gmp
		dev-libs/libbsd
		dev-libs/libffi
		dev-libs/libgcrypt
		dev-libs/libgpg-error
		dev-libs/libpcre
		dev-libs/libtasn1
		dev-libs/libunistring
		dev-libs/nettle
		dev-libs/nspr
		dev-libs/nss
		media-gfx/graphite2
		media-libs/alsa-lib
		media-libs/fontconfig
		media-libs/freetype
		media-libs/harfbuzz
		media-libs/libepoxy
		media-libs/libglvnd
		media-libs/libpng
		net-dns/libidn2
		net-libs/gnutls
		net-print/cups
		sys-apps/dbus
		sys-apps/systemd
		sys-apps/util-linux
		sys-devel/gcc
		sys-libs/glibc
		sys-libs/libcap
		sys-libs/zlib
		x11-libs/cairo
		x11-libs/gdk-pixbuf
		x11-libs/gtk+
		x11-libs/libX11
		x11-libs/libXScrnSaver
		x11-libs/libXau
		x11-libs/libXcomposite
		x11-libs/libXcursor
		x11-libs/libXdamage
		x11-libs/libXdmcp
		x11-libs/libXext
		x11-libs/libXfixes
		x11-libs/libXi
		x11-libs/libXinerama
		x11-libs/libXrandr
		x11-libs/libXrender
		x11-libs/libXtst
		x11-libs/libxcb
		x11-libs/pango
		x11-libs/pixman"

DEPEND="${RDEPEND}"

pkg_setup() {
		S="${WORKDIR}/"
}


src_install(){
	insinto "/opt/${PN}/"
	doins -r opt/QQmusic/*

	dosym "/opt/${PN}/qqmusic" "/usr/bin/qqmusic"
	sed -i "s/QQmusic/${PN}/g" usr/share/applications/${PN}.desktop
	domenu usr/share/applications/${PN}.desktop
	doicon usr/share/icons/hicolor/64x64/apps/${PN}.png
	fperms +x "/opt/${PN}/qqmusic"
	fperms +x "/opt/${PN}/libEGL.so"
	fperms +x "/opt/${PN}/libGLESv2.so"
	fperms +x "/opt/${PN}/libffmpeg.so"
	fperms +x "/opt/${PN}/libvk_swiftshader.so"
}
