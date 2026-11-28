# Copyright 2023-2024 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit meson

DESCRIPTION="Utilities for containerized apps to launch programs outside the container"
HOMEPAGE="https://github.com/flatpak/flatpak-xdg-utils"
SRC_URI="https://github.com/flatpak/flatpak-xdg-utils/releases/download/${PV}/flatpak-xdg-utils-${PV}.tar.xz"

LICENSE="LGPL-2.1+"
SLOT="0"
RDEPEND="dev-libs/glib:2"
DEPEND="${RDEPEND}"
BDEPEND="virtual/pkgconfig"
KEYWORDS="~amd64 ~arm64 ~loong"

src_configure() {
	local emesonargs=(
		--bindir="/usr/lib/${PN}"
	)
	meson_src_configure
}

src_install() {
	meson_src_install
	dosym -r /usr/lib/"${PN}"/flatpak-spawn /usr/bin/flatpak-spawn
}
