# Copyright 2019 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=7

DESCRIPTION="Cross-platform library for building Telegram clients"
HOMEPAGE="https://core.telegram.org/tdlib"
SRC_URI="https://github.com/tdlib/td/archive/v${PV}.tar.gz"
S="${WORKDIR}/td-${PV}"
LICENSE="BSL-1.0"
SLOT="0"
KEYWORDS="amd64 ~x86"
IUSE=""

DEPEND="sys-devel/gcc
	dev-libs/openssl
	sys-libs/zlib
	dev-util/gperf
	dev-util/cmake
"
RDEPEND="${DEPEND}"
BDEPEND=""

src_compile(){
	mkdir ${S}/build
	cd ${S}/build
	cmake -GNinja -DCMAKE_BUILD_TYPE=Release ..
	ninja

}

src_install(){
	doheader -r ${S}/td
	doheader -r ${S}/build/td
	dolib.so ${S}/build/libtdjson.so ${S}/build/libtdjson.so.1.5.0
}
