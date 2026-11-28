# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

RUST_MIN_VER="1.96.1"
ZIG_SLOT="0.16"
ZIG_NEEDS_LLVM=1
ZIG_OPTIONAL=1

inherit cargo shell-completion toolchain-funcs zig-utils

DESCRIPTION="Terminal workspace manager and runtime for AI coding agents"
HOMEPAGE="https://herdr.dev https://github.com/herdrdev/herdr"
SRC_URI="
	https://github.com/herdrdev/${PN}/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz
	https://github.com/locez/locez-overlay-distfiles/releases/download/${CATEGORY}-${PN}-${PV}/${P}-deps.tar.xz
"

LICENSE="Apache-2.0"
# Dependent crate and vendored libghostty-vt licenses
LICENSE+="
	0BSD Apache-2.0-with-LLVM-exceptions BSD BSD-2 Boost-1.0 MIT
	Unicode-3.0 Unicode-DFS-2016 Unlicense WTFPL-2 ZLIB
"
SLOT="0"
KEYWORDS="~amd64 ~arm64"
IUSE="+simd"

RDEPEND="!app-misc/herdr-bin"
BDEPEND+="
	|| (
		dev-lang/zig-bin:${ZIG_SLOT}
		dev-lang/zig:${ZIG_SLOT}[llvm(+)]
	)
"

QA_FLAGS_IGNORED="usr/bin/herdr"

DOCS=(
	CHANGELOG.md
	README.md
	README.zh-CN.md
)

pkg_setup() {
	rust_pkg_setup
	zig-utils_setup
}

_herdr_zig_env() {
	export ZIG="${ZIG_EXE}"
	export LIBGHOSTTY_VT_OPTIMIZE="$(usex debug Debug ReleaseFast)"
	export LIBGHOSTTY_VT_SIMD="$(usex simd true false)"
	export LIBGHOSTTY_VT_ZIG_SYSTEM_DIR="${WORKDIR}/zig_packages"
	export ZIG_GLOBAL_CACHE_DIR="${T}/zig-global-cache"
	export ZIG_LOCAL_CACHE_DIR="${T}/zig-local-cache"
}

src_compile() {
	_herdr_zig_env
	cargo_src_compile

	if ! tc-is-cross-compiler; then
		local bin="$(cargo_target_dir)/herdr"
		"${bin}" completion bash > "${T}/herdr.bash" || die
		"${bin}" completion fish > "${T}/herdr.fish" || die
		"${bin}" completion zsh > "${T}/_herdr" || die
	fi
}

src_test() {
	_herdr_zig_env
	cargo_src_test
}

src_install() {
	dobin "$(cargo_target_dir)/herdr"
	einstalldocs

	if ! tc-is-cross-compiler; then
		newbashcomp "${T}/herdr.bash" herdr
		newfishcomp "${T}/herdr.fish" herdr.fish
		newzshcomp "${T}/_herdr" _herdr
	fi
}
