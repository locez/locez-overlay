# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

inherit systemd unpacker xdg

DESCRIPTION="Flutter-native Wayland compositor and desktop shell (binary package)"
HOMEPAGE="https://github.com/denialwm/denial"
SRC_URI="https://denialwm.github.io/denial/x86_64/denial-${PV}-1-x86_64.pkg.tar.zst -> ${P}.pkg.tar.zst"
S="${WORKDIR}"

LICENSE="GPL-3+ CC-BY-SA-4.0 GPL-3 OFL-1.1"
SLOT="0"
KEYWORDS="-* ~amd64"
CONFIG_PROTECT="/etc/denial /etc/xdg/xdg-desktop-portal-wlr"

RDEPEND="
	!gui-wm/denial
	dev-libs/libinput:=
	dev-libs/wayland
	media-fonts/source-han-sans
	media-libs/libglvnd
	media-libs/mesa
	sys-auth/seatd:=
	sys-libs/pam
	virtual/libudev
	x11-libs/libxkbcommon
	~dev-libs/denial-flutter-engine-bin-${PV}
	app-misc/ddcutil
	app-shells/bash
	media-libs/fontconfig
	media-libs/libpulse
	gnome-extra/zenity
	sys-apps/coreutils
	sys-apps/dbus
	sys-apps/xdg-desktop-portal
	sys-apps/xdg-desktop-portal-gtk
	sys-auth/rtkit
	gui-libs/xdg-desktop-portal-wlr
	x11-base/xwayland
	x11-misc/xkeyboard-config
"
BDEPEND="
	app-arch/gzip
	app-arch/zstd
"

RESTRICT="strip"
QA_PREBUILT="
	usr/bin/denialctl
	usr/bin/deniald
	usr/lib/denial/flutter/lib/libapp.so
"
QA_PRESTRIPPED="usr/lib/denial/flutter/lib/libapp.so"

src_prepare() {
	sed -i 's/^DesktopNames=/X-DesktopNames=/' \
		usr/share/wayland-sessions/denial.desktop || die
	gunzip usr/share/man/man1/*.gz || die
	default
}

src_install() {
	dobin usr/bin/denial-session usr/bin/denialctl usr/bin/deniald
	systemd_douserunit usr/lib/systemd/user/denial-session.target

	exeinto /usr/lib/denial/flutter/lib
	doexe usr/lib/denial/flutter/lib/libapp.so

	insinto /usr/lib/denial/flutter/data
	doins -r usr/lib/denial/flutter/data/flutter_assets

	insinto /usr/share/wayland-sessions
	doins usr/share/wayland-sessions/denial.desktop
	insinto /usr/share/xdg-desktop-portal
	doins usr/share/xdg-desktop-portal/denial-portals.conf

	insinto /etc/xdg/xdg-desktop-portal-wlr
	doins etc/xdg/xdg-desktop-portal-wlr/Denial
	insinto /etc/denial
	doins etc/denial/outputs.conf etc/denial/session.conf

	insinto /usr/share/denial
	doins usr/share/denial/version

	dodoc \
		usr/share/doc/denial/README.md \
		usr/share/doc/denial/CURSORS.md \
		usr/share/doc/denial/FONTS.md \
		usr/share/doc/denial/BIBATA_MODERN_ICE.sha256 \
		usr/share/doc/denial/JETBRAINS_MONO.sha256 \
		usr/share/doc/denial/WALLPAPERS.md \
		usr/share/doc/denial/copyright
	dodoc usr/share/licenses/denial/*
	doman usr/share/man/man1/denial-session.1 \
		usr/share/man/man1/denialctl.1 \
		usr/share/man/man1/deniald.1
}
