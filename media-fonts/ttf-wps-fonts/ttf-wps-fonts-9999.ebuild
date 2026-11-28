# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

FONT_PN="wps-fonts"
FONT_SUFFIX="ttf TTF"

inherit font git-r3

DESCRIPTION="Symbol fonts required by wps-office"
HOMEPAGE="https://github.com/dv-anomaly/ttf-wps-fonts"
EGIT_REPO_URI="https://github.com/dv-anomaly/ttf-wps-fonts.git"

LICENSE="all-rights-reserved"
SLOT="0"

# Only installs fonts
RESTRICT="binchecks bindist mirror strip test"

pkg_postinst() {
	unset FONT_CONF # override default message
	font_pkg_postinst
}
