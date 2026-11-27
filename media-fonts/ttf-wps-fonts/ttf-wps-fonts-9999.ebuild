# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit font git-r3

DESCRIPTION="Symbol fonts required by wps-office"
HOMEPAGE="https://github.com/dv-anomaly/ttf-wps-fonts"
EGIT_REPO_URI="https://github.com/dv-anomaly/ttf-wps-fonts.git"

LICENSE="all-rights-reserved"
SLOT="0"

FONT_SUFFIX="ttf TTF"
FONT_PN="wps-fonts"

# Only installs fonts
RESTRICT="binchecks bindist mirror strip test"

pkg_postinst() {
	unset FONT_CONF # override default message
	font_pkg_postinst
}

src_install() {
	insinto "/usr/share/fonts/${FONT_PN}"
	doins *.{ttf,TTF}
}
