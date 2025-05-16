# Copyright 2025 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop xdg-utils

DESCRIPTION="Official Minecraft Launcher"
HOMEPAGE="https://www.minecraft.net/"
SRC_URI="https://launcher.mojang.com/download/Minecraft.tar.gz -> minecraft-launcher.tar.gz
		https://launcher.mojang.com/download/minecraft-launcher.svg -> minecraft-launcher.svg
		"
S="${WORKDIR}/minecraft-launcher"

LICENSE="Minecraft-EULA"
SLOT="0"
KEYWORDS="~amd64"
RESTRICT="strip mirror"

IUSE="bwrap"

DEPEND=""
RDEPEND="
	bwrap? ( sys-apps/bubblewrap )
	x11-libs/gtk+:3
	dev-libs/openssl
	media-libs/mesa
	x11-libs/libX11
	x11-libs/libXcursor
	x11-libs/libXrandr
	media-libs/fontconfig"
BDEPEND=""

QA_PREBUILT="*"

src_install() {
	insinto /opt/minecraft-launcher
	doins -r *
	launcher=/opt/minecraft-launcher/minecraft-launcher
	fperms +x ${launcher}

	if use bwrap; then
		exeinto /opt/minecraft-launcher
		doexe "${FILESDIR}/minecraft-launcher-wrapper"
		launcher=/opt/minecraft-launcher/minecraft-launcher-wrapper
	fi

	newicon "${DISTDIR}/minecraft-launcher.svg" "minecraft-launcher.svg"
	make_desktop_entry \
		"${launcher}" \
		"Minecraft Launcher" \
	"minecraft-launcher" \
	"Game"
}

pkg_postinst() {
	xdg_icon_cache_update
	elog "before launch："
	elog "1. make sure install a video driver（nvidia-drivers or mesa）"
	elog "2. game data store in ~/.minecraft"
}

pkg_postrm() {
	xdg_icon_cache_update
}
