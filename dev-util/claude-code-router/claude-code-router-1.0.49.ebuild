# Copyright 1999-2025 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="A powerful tool to route Claude Code"
HOMEPAGE="https://github.com/musistudio/claude-code-router"
SRC_URI="https://registry.npmjs.org/@musistudio/claude-code-router/-/claude-code-router-${PV}.tgz"
S="${WORKDIR}/package"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"

RESTRICT="strip"

RDEPEND="dev-util/claude-code"

src_compile() {
	# Skip, nothing to compile here.
	:
}

src_install() {
	dodoc README.md LICENSE

	insinto /opt/${PN}
	doins -r ./dist
	fperms a+x opt/${PN}/dist/cli.js

	dodir /opt/bin
	dosym -r /opt/${PN}/dist/cli.js /opt/bin/ccr

	# nodejs defaults to disabling deprecation warnings when running code
	# from any path containing a node_modules directory. Since we're installing
	# outside of the realm of npm, explicitly pass an option to disable
	# deprecation warnings so it behaves the same as it does if installed via
	# npm. It's proprietary; not like Gentoo users can fix the warnings anyway.
	sed -i 's/env node/env -S node --no-deprecation/' "${ED}/opt/{PN}/dist/cli.js"
}
