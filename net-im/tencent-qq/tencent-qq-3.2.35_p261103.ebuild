# Copyright 2019-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit desktop unpacker xdg

BUILD="54395"

qq_src_uri() {
	printf "${1}? ( https://overlay.locez.com/"
	printf "https://qqdl.gtimg.cn/qqfile/QQNTV2/9.9.37/release/816b3c79/QQ_${PV/p/}_${2:-$1}_01.deb"
	printf " -> ${P}_${1}.deb )"
}

DESCRIPTION="The new version of the official linux-qq"
HOMEPAGE="https://im.qq.com/index/#/linux"
SRC_URI="
	$(qq_src_uri amd64)
	$(qq_src_uri arm64)
"

S="${WORKDIR}"

LICENSE="Tencent-QQ"
SLOT="0"
KEYWORDS="-* ~amd64 ~arm64"

IUSE="bwrap system-fdk-aac system-libssh2 system-openh264 system-zlib gnome"

RESTRICT="strip mirror bindist"

RDEPEND="
	app-accessibility/at-spi2-core:2
	app-crypt/libsecret
	bwrap? (
		sys-apps/bubblewrap
		x11-misc/flatpak-xdg-utils
	)
	dev-libs/expat
	dev-libs/glib:2
	dev-libs/gmp
	dev-libs/nettle
	dev-libs/nspr
	dev-libs/nss
	gnome? ( dev-libs/gjs )
	media-libs/alsa-lib
	media-libs/libglvnd
	media-libs/libpulse
	media-libs/mesa
	media-libs/openslide
	net-libs/gnutls
	net-print/cups[-openssl]
	sys-apps/dbus
	sys-apps/keyutils
	system-fdk-aac? ( media-libs/fdk-aac )
	!system-libssh2? ( dev-libs/libgcrypt )
	system-libssh2? ( net-libs/libssh2 )
	system-openh264? ( media-libs/openh264 )
	system-zlib? ( virtual/zlib )
	virtual/krb5
	virtual/libudev
	x11-libs/cairo
	x11-libs/gtk+:3
	x11-libs/libX11
	x11-libs/libXext
	x11-libs/libXfixes
	x11-libs/libXrandr
	x11-libs/libnotify
	x11-libs/libXdamage
	x11-libs/libXcomposite
	x11-libs/libXft
	x11-libs/libXScrnSaver
	x11-libs/libXtst
	x11-libs/libxcb
	x11-libs/libxkbcommon
	x11-libs/pango
	x11-misc/xdg-utils
"

QA_PREBUILT="opt/QQ/*"

src_prepare() {
	default

	rm -rv usr/share/doc || die

	if use system-fdk-aac; then
		rm -v opt/QQ/resources/app/avsdk/libfdk-aac.so || die
	fi
	if use system-libssh2; then
		rm -v opt/QQ/resources/app/libssh2.so.1 opt/QQ/resources/app/avsdk/bugly/libssh2.so.1 || die
	fi
	if use system-openh264; then
		rm -v opt/QQ/resources/app/avsdk/libopenh264.so || die
	fi
	if use system-zlib; then
		rm -v opt/QQ/libz.so.1 || die
	fi

	sed -i \
		-e 's:^Exec=.*$:Exec=/usr/bin/qq %U:' \
		-e 's:^Icon=.*$:Icon=qq:' \
		usr/share/applications/qq.desktop || die
}

src_install() {
	dodir /opt
	cp -a opt/QQ "${ED}/opt/" || die

	insinto /usr/share
	doins -r usr/share/icons
	domenu usr/share/applications/qq.desktop

	if use bwrap; then
		newbin "${FILESDIR}/bwrap.sh" qq

		insinto /opt/QQ/workarounds
		doins "${FILESDIR}"/{config.json,vercmp.sh}
		fperms +x /opt/QQ/workarounds/vercmp.sh

		sed -i "s|__BASE_VER__|${PV%_*}-${BUILD}|g;s|__CURRENT_VER__|${PV%_*}-${BUILD}|g;s|__BUILD_VER__|${BUILD}|g" \
			"${ED}/opt/QQ/workarounds/config.json" \
			"${ED}/usr/bin/qq" || die
	else
		newbin "${FILESDIR}/qq.sh" qq
	fi
}

pkg_postinst() {
	xdg_pkg_postinst
	if use bwrap; then
		elog "-EN-----------------------------------------------------------------"
		elog "Enabled Bubblewrap support."
		elog "If you want to download files to system download folder in QQ,"
		elog "please set the download folder to the system download folder in the QQ settings."
		elog "You can also define other bwrap parameters in \"~/.config/qq-bwrap-flags.conf\"."
		elog "--------------------------------------------------------------------"
		elog "-ZH-----------------------------------------------------------------"
		elog "Bubblewrap 支持已启用。"
		elog "如果需要在 QQ 中下载文件至系统下载文件夹，"
		elog "请在「设置」->「存储管理」中把下载文件夹更改为系统的下载文件夹。"
		elog "可在 \"~/.config/qq-bwrap-flags.conf\" 中设置其他 bwrap 参数。"
		elog "--------------------------------------------------------------------"
	fi
}
