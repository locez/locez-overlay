# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit shell-completion toolchain-funcs

DESCRIPTION="Terminal workspace manager and runtime for AI coding agents (prebuilt binary)"
HOMEPAGE="https://herdr.dev https://github.com/herdrdev/herdr"
SRC_URI="
	amd64? ( https://github.com/herdrdev/herdr/releases/download/v${PV}/herdr-linux-x86_64 -> ${P}-amd64 )
	arm64? ( https://github.com/herdrdev/herdr/releases/download/v${PV}/herdr-linux-aarch64 -> ${P}-arm64 )
"
S="${WORKDIR}"

LICENSE="Apache-2.0"
# Dependent crate and vendored libghostty-vt licenses
LICENSE+="
	0BSD Apache-2.0-with-LLVM-exceptions BSD BSD-2 Boost-1.0 MIT
	Unicode-3.0 Unicode-DFS-2016 Unlicense WTFPL-2 ZLIB
"
SLOT="0"
KEYWORDS="-* ~amd64 ~arm64"
RESTRICT="strip"

RDEPEND="!app-misc/herdr"

QA_PREBUILT="usr/bin/herdr"

src_unpack() {
	:
}

src_install() {
	local bin="${DISTDIR}/${P}-$(usex amd64 amd64 arm64)"
	newbin "${bin}" herdr

	if ! tc-is-cross-compiler; then
		"${ED}/usr/bin/herdr" completion bash > "${T}/herdr.bash" || die
		"${ED}/usr/bin/herdr" completion fish > "${T}/herdr.fish" || die
		"${ED}/usr/bin/herdr" completion zsh > "${T}/_herdr" || die
		newbashcomp "${T}/herdr.bash" herdr
		newfishcomp "${T}/herdr.fish" herdr.fish
		newzshcomp "${T}/_herdr" _herdr
	fi
}
