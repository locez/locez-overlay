# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI="8"

ETYPE="sources"
K_GENPATCHES_VER="23"
K_SECURITY_UNSUPPORTED="1"
K_WANT_GENPATCHES="base extras"

inherit check-reqs kernel-2
detect_version
detect_arch

XANMOD_VERSION="1"
EXTRAVERSION="-xanmod${XANMOD_VERSION}"
ZFS_KERNEL_MAX="7.0"

DESCRIPTION="Linux kernel sources with XanMod and Gentoo patches"
HOMEPAGE="
	https://xanmod.org
	https://gitlab.com/xanmod/linux
	https://dev.gentoo.org/~alicef/genpatches
"
SRC_URI="
	${KERNEL_URI}
	${GENPATCHES_URI}
	${ARCH_URI}
	https://downloads.sourceforge.net/project/xanmod/releases/main/${PV}-xanmod${XANMOD_VERSION}/patch-${PV}-xanmod${XANMOD_VERSION}.xz
"
S="${WORKDIR}/linux-${PV}-xanmod${XANMOD_VERSION}"

LICENSE+=" CDDL"
KEYWORDS="~amd64"
IUSE="zfs"
RDEPEND="
	zfs? (
		|| (
			( >=sys-fs/zfs-kmod-2.3.7 <sys-fs/zfs-kmod-2.4.0_rc1 )
			>=sys-fs/zfs-2.4.2[modules]
		)
	)
"

KV_FULL="${PV}${EXTRAVERSION}"
KV="${KV_FULL}"

pkg_pretend() {
	CHECKREQS_DISK_BUILD="4G"
	check-reqs_pkg_pretend

	if use zfs; then
		: "${ZFS_KERNEL_MAX//[^.]/}"
		local -i zfs_kernel_max_components=${#_}

		if ver_test "$(ver_cut 1-$(( zfs_kernel_max_components + 1 )) "${PV}")" \
			-gt "${ZFS_KERNEL_MAX}"
		then
			eerror
			eerror "USE=zfs is enabled for ${PN}, but this ebuild's ZFS module"
			eerror "dependency gate is only valid through Linux ${ZFS_KERNEL_MAX}.x."
			eerror "Do not bump ${PN} past this cap until the zfs? dependencies"
			eerror "are updated to versions that support the target Linux series."
			die "ZFS kernel compatibility cap exceeded"
		fi
	fi
}

pkg_setup() {
	ewarn
	ewarn "${PN} is not supported by the Gentoo Kernel Project."
	ewarn "If you need support, please contact the XanMod developers directly."
	ewarn "Do not open bugs in Gentoo's bugzilla unless you have issues with"
	ewarn "the ebuild."
	ewarn

	kernel-2_pkg_setup
}

src_unpack() {
	default

	mv "${WORKDIR}/linux-${KV_MAJOR}.${KV_MINOR}" "${S}" ||
		die "Failed to rename kernel source directory"
}

src_prepare() {
	eapply "${WORKDIR}/patch-${PV}-xanmod${XANMOD_VERSION}"
	eapply "${FILESDIR}/${PN}-x86-native-cache-shift.patch"

	# XanMod already includes the upstream stable updates, so drop the
	# vanilla update patches from Gentoo genpatches before applying them.
	if [[ ${K_GENPATCHES_VER} -ne 1 ]]; then
		find "${WORKDIR}" -type f -name '10*linux*patch' -delete ||
			die "Failed to delete vanilla Linux patches from genpatches"
	fi

	eapply "${WORKDIR}"/*.patch

	rm "${S}/tools/testing/selftests/tc-testing/action-ebpf" || die
	sed -i -e "s:^\(EXTRAVERSION =\).*:\1 ${EXTRAVERSION}:" \
		"${S}/Makefile" || die
	rm -f "${S}/localversion" || die
	default
}

pkg_postinst() {
	kernel-2_pkg_postinst
	elog "For more information about XanMod, see https://xanmod.org."
}

pkg_postrm() {
	kernel-2_pkg_postrm
}
