# Copyright 2020 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=7

inherit desktop xdg-utils

DESCRIPTION="Official launcher for Minecraft"
HOMEPAGE="https://minecraft.net"
SRC_URI="https://launcher.mojang.com/download/linux/x86_64/minecraft-launcher_${PV}.tar.gz
			https://minecraft.net/android-icon-192x192.png -> minecraft-launcher.png"

LICENSE="Minecraft-clickwrap-EULA"
SLOT="0"
KEYWORDS="~amd64 ~x86"

RDEPEND="gnome-base/gconf
	sys-apps/dbus
	x11-apps/xrandr
	x11-libs/libXcomposite
	x11-libs/libXcursor
	x11-libs/libXdamage
	x11-libs/libXext
	x11-libs/libXfixes
	x11-libs/libXi
	x11-libs/libXrender
	x11-libs/libXScrnSaver
	x11-libs/libXtst
	x11-libs/xcb-util
	>=x11-libs/gtk+-2.24.32-r1
	media-libs/openal
	virtual/opengl
	virtual/jre"

DEPEND="${RDEPEND}"

S=${WORKDIR}

function src_install() {
	dodir /opt/minecraft-launcher
	insinto /opt
	doins -r .

	fperms +x  /opt/minecraft-launcher/minecraft-launcher
	doicon -s 192 ${DISTDIR}/minecraft-launcher.png
	dosym "../../opt/minecraft-launcher/minecraft-launcher" "/usr/bin/minecraft-launcher"
	make_desktop_entry minecraft-launcher Minecraft minecraft-launcher Game
}

pkg_postinst() {
	einfo "This package has installed the Minecraft launcher."
	einfo "To actually play the game, you need to purchase an account at:"
	einfo "    ${HOMEPAGE}"

	xdg_icon_cache_update
}

pkg_postrm() {
	xdg_icon_cache_update
}
