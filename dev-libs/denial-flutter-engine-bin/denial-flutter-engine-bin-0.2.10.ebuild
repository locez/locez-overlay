# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit unpacker

DESCRIPTION="Prebuilt Flutter Engine runtime for Denial"
HOMEPAGE="https://github.com/denialwm/denial"
SRC_URI="https://denialwm.github.io/denial/x86_64/denial-flutter-engine-1:${PV}-1-x86_64.pkg.tar.zst -> ${P}.pkg.tar.zst"
S="${WORKDIR}"

LICENSE="BSD"
SLOT="0"
KEYWORDS="-* ~amd64"

RDEPEND="media-libs/fontconfig"
BDEPEND="app-arch/zstd"

RESTRICT="strip"
QA_PREBUILT="usr/lib/denial/flutter/lib/libflutter_engine.so"
QA_PRESTRIPPED="${QA_PREBUILT}"

src_install() {
	insinto /usr/lib/denial/flutter/lib
	doins usr/lib/denial/flutter/lib/libflutter_engine.so
	fperms 0755 /usr/lib/denial/flutter/lib/libflutter_engine.so

	insinto /usr/lib/denial/flutter/data
	doins usr/lib/denial/flutter/data/icudtl.dat

	insinto /usr/share/denial/flutter-engine
	doins -r usr/share/denial/flutter-engine/.

	dodoc usr/share/doc/denial-flutter-engine/BUILD_INFO.md
	dodoc usr/share/licenses/denial-flutter-engine/LICENSE.flutter
	dodoc usr/share/licenses/denial-flutter-engine/LICENSE.third_party
}
