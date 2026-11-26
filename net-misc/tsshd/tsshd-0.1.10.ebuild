# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit go-module

DESCRIPTION="UDP-based SSH server with roaming support"
HOMEPAGE="https://github.com/trzsz/tsshd https://trzsz.github.io/tsshd"
SRC_URI="
	https://github.com/trzsz/${PN}/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz
	https://github.com/gentoo-zh-drafts/tsshd/releases/download/v${PV}/${P}-vendor.tar.xz
"

LICENSE="Apache-2.0 BSD BSD-2 ISC MIT"
SLOT="0"
KEYWORDS="~amd64 ~arm64"

# Upstream requires Go 1.26.0 or newer.
BDEPEND=">=dev-lang/go-1.26.0:="

src_prepare() {
	rm -r examples || die
	default
}

src_compile() {
	ego build -buildvcs=false -trimpath -o "${T}/${PN}" ./cmd/tsshd
}

src_test() {
	# bash execs the last command of "sh -c", so python3 inherits the PTY
	# session leadership and os.setsid() fails with EPERM
	ego test ./... -skip '^TestSessionWaitWithDescendantPTY$'
}

src_install() {
	dobin "${T}/${PN}"
	dodoc README.md README.cn.md
}
