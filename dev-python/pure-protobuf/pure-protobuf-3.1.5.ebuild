# Copyright 2025 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DISTUTILS_USE_PEP517=poetry
PYTHON_COMPAT=( python3_{10..13} )

inherit distutils-r1 pypi

DESCRIPTION="A pure python implementation of google protobuf"
HOMEPAGE="https://github.com/eigenein/protobuf"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64 ~x86"

RDEPEND="
	dev-python/typing-extensions[${PYTHON_USEDEP}]
"
BDEPEND="
	dev-python/poetry-core[${PYTHON_USEDEP}]
"

distutils_enable_tests pytest

src_prepare() {
	# Patch backend to use poetry-core directly, avoiding poetry-dynamic-versioning dependency.
	# The sdist should already have the version generated/stamped roughly, or we rely on it being correct.
	sed -i \
		-e 's:poetry_dynamic_versioning.backend:poetry.core.masonry.api:' \
		-e 's:"poetry-dynamic-versioning>=[0-9.]*",::' \
		-e 's:"poetry-dynamic-versioning>=[0-9.]*"::' \
		pyproject.toml || die
	distutils-r1_src_prepare
}
