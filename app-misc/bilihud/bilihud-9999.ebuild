# Copyright 2025 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DISTUTILS_USE_PEP517=hatchling
PYTHON_COMPAT=( python3_{10..13} )

inherit distutils-r1 git-r3 desktop

distutils_enable_tests pytest

python_install_all() {
	distutils-r1_python_install_all
	domenu bilihud.desktop
	newicon src/bilihud/assets/icon.png bilihud.png
}

DESCRIPTION="B站弹幕阅读器 - 一个可以在游戏全屏时显示弹幕的Qt应用程序"
HOMEPAGE="https://github.com/locez/bilihud"
EGIT_REPO_URI="https://github.com/locez/bilihud.git"

LICENSE="MIT"
SLOT="0"
KEYWORDS=""

RDEPEND="
	dev-python/pyqt6[${PYTHON_USEDEP}]
	dev-python/aiohttp[${PYTHON_USEDEP}]
	dev-python/qasync[${PYTHON_USEDEP}]
	dev-python/browser-cookie3[${PYTHON_USEDEP}]
	dev-python/brotlicffi[${PYTHON_USEDEP}]
	dev-python/pure-protobuf[${PYTHON_USEDEP}]
"
DEPEND="${RDEPEND}"
