# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

RUST_MIN_VER="1.95.0"

CRATES="
	adler2@2.0.1
	ahash@0.8.12
	aho-corasick@1.1.4
	android-activity@0.6.1
	android-properties@0.2.2
	appendlist@1.4.0
	as-raw-xcb-connection@1.0.1
	async-broadcast@0.7.2
	async-channel@2.5.0
	async-executor@1.14.0
	async-io@2.6.0
	async-lock@3.4.2
	async-process@2.5.0
	async-recursion@1.1.1
	async-signal@0.2.14
	async-task@4.7.1
	async-trait@0.1.91
	atomic-waker@1.1.2
	atomic_float@1.1.0
	autocfg@1.5.1
	bitflags@2.13.1
	block-buffer@0.10.4
	block2@0.6.2
	blocking@1.6.2
	borsh@1.8.0
	bumpalo@3.20.3
	bytemuck@1.25.2
	bytemuck_derive@1.11.0
	bytes@1.12.1
	calloop-wayland-source@0.4.1
	calloop@0.14.4
	cc@1.3.0
	cfg-if@1.0.4
	cfg_aliases@0.2.2
	combine@4.6.7
	concurrent-queue@2.5.0
	cpufeatures@0.2.17
	crc32fast@1.5.0
	crossbeam-utils@0.8.22
	crypto-common@0.1.7
	cursor-icon@1.2.0
	digest@0.10.7
	dispatch2@0.3.1
	dlib@0.5.3
	downcast-rs@1.2.1
	dpi@0.1.2
	drm-ffi@0.9.1
	drm-fourcc@2.2.0
	drm-sys@0.8.1
	drm@0.14.1
	encoding_rs@0.8.35
	endi@1.1.1
	enumflags2@0.7.12
	enumflags2_derive@0.7.12
	equivalent@1.0.2
	errno@0.3.14
	event-listener-strategy@0.5.4
	event-listener@5.4.1
	fastrand@2.5.0
	fdeflate@0.3.7
	find-msvc-tools@0.1.9
	flatbuffers@25.9.23
	flate2@1.1.9
	futures-core@0.3.33
	futures-io@0.3.33
	futures-lite@2.6.1
	futures-task@0.3.33
	futures-util@0.3.33
	gbm-sys@0.4.0
	gbm@0.18.0
	generic-array@0.14.7
	gethostname@1.1.0
	getrandom@0.3.4
	getrandom@0.4.3
	gl_generator@0.14.0
	glam@0.33.2
	hashbrown@0.17.1
	hermit-abi@0.3.9
	hermit-abi@0.5.2
	hex@0.4.3
	indexmap@2.14.0
	input-sys@1.19.0
	input@0.10.0
	io-lifetimes@1.0.11
	itoa@1.0.18
	jni-macros@0.22.4
	jni-sys-macros@0.4.1
	jni-sys@0.3.1
	jni-sys@0.4.1
	jni@0.22.4
	jobserver@0.1.35
	js-sys@0.3.103
	keyboard-types@0.8.3
	khronos_api@3.1.0
	lazy_static@1.5.0
	libc@0.2.188
	libloading@0.8.9
	libloading@0.9.0
	libredox@0.1.18
	libseat-sys@0.2.0
	libseat@0.2.4
	libudev-sys@0.1.4
	linux-raw-sys@0.12.1
	linux-raw-sys@0.4.15
	linux-raw-sys@0.9.4
	log@0.4.33
	matchers@0.2.0
	memchr@2.8.3
	memmap2@0.9.11
	memoffset@0.9.1
	miniz_oxide@0.8.9
	ndk-context@0.1.1
	ndk-sys@0.6.0+11769913
	ndk@0.9.0
	nix@0.31.3
	nu-ansi-term@0.50.3
	num_enum@0.7.6
	num_enum_derive@0.7.6
	objc2-app-kit@0.3.2
	objc2-core-foundation@0.3.2
	objc2-core-graphics@0.3.2
	objc2-core-video@0.3.2
	objc2-encode@4.1.0
	objc2-foundation@0.3.2
	objc2-ui-kit@0.3.2
	objc2@0.6.4
	once_cell@1.21.4
	orbclient@0.3.55
	ordered-stream@0.2.0
	parking@2.2.1
	percent-encoding@2.3.2
	pin-project-internal@1.1.13
	pin-project-lite@0.2.17
	pin-project@1.1.13
	piper@0.2.5
	pkg-config@0.3.33
	plain@0.2.3
	png@0.18.1
	polling@3.11.0
	ppv-lite86@0.2.21
	proc-macro-crate@3.5.0
	proc-macro2@1.0.107
	profiling-procmacros@1.0.18
	profiling@1.0.18
	quick-xml@0.39.4
	quote@1.0.47
	r-efi@5.3.0
	r-efi@6.0.0
	rand@0.9.5
	rand_chacha@0.9.0
	rand_core@0.9.5
	raw-window-handle@0.6.2
	redox_syscall@0.5.18
	redox_syscall@0.9.0
	regex-automata@0.4.16
	regex-syntax@0.8.11
	reis@0.7.0
	rustc_version@0.4.1
	rustix@0.38.44
	rustix@1.1.4
	rustversion@1.0.23
	same-file@1.0.6
	scoped-tls@1.0.1
	scopeguard@1.2.0
	semver@1.0.28
	serde@1.0.229
	serde_core@1.0.229
	serde_derive@1.0.229
	serde_json@1.0.151
	serde_repr@0.1.21
	sha2@0.10.9
	sharded-slab@0.1.7
	shlex@2.0.1
	signal-hook-registry@1.4.8
	simd-adler32@0.3.9
	simd_cesu8@1.2.0
	simdutf8@0.1.5
	slab@0.4.12
	smallvec@1.15.2
	smithay-client-toolkit@0.20.0
	smol_str@0.3.6
	syn@2.0.119
	syn@3.0.2
	tempfile@3.27.0
	thiserror-impl@1.0.69
	thiserror-impl@2.0.19
	thiserror@1.0.69
	thiserror@2.0.19
	thread_local@1.1.10
	toml_datetime@1.1.1+spec-1.1.0
	toml_edit@0.25.13+spec-1.1.0
	toml_parser@1.1.2+spec-1.1.0
	tracing-attributes@0.1.31
	tracing-core@0.1.36
	tracing-log@0.2.0
	tracing-subscriber@0.3.23
	tracing@0.1.44
	typenum@1.20.1
	udev@0.9.3
	uds_windows@1.2.1
	unicode-ident@1.0.24
	unicode-segmentation@1.13.3
	uuid@1.24.0
	valuable@0.1.1
	version_check@0.9.5
	walkdir@2.5.0
	wasip2@1.0.4+wasi-0.2.12
	wasm-bindgen-futures@0.4.76
	wasm-bindgen-macro-support@0.2.126
	wasm-bindgen-macro@0.2.126
	wasm-bindgen-shared@0.2.126
	wasm-bindgen@0.2.126
	wayland-backend@0.3.15
	wayland-client@0.31.14
	wayland-csd-frame@0.3.0
	wayland-cursor@0.31.14
	wayland-egl@0.32.11
	wayland-protocols-experimental@20250721.0.1
	wayland-protocols-misc@0.3.12
	wayland-protocols-plasma@0.3.12
	wayland-protocols-wlr@0.3.12
	wayland-protocols@0.32.13
	wayland-scanner@0.31.10
	wayland-server@0.31.13
	wayland-sys@0.31.11
	web-sys@0.3.103
	web-time@1.1.0
	winapi-util@0.1.11
	windows-link@0.2.1
	windows-sys@0.48.0
	windows-sys@0.59.0
	windows-sys@0.61.2
	windows-targets@0.48.5
	windows-targets@0.52.6
	windows_aarch64_gnullvm@0.48.5
	windows_aarch64_gnullvm@0.52.6
	windows_aarch64_msvc@0.48.5
	windows_aarch64_msvc@0.52.6
	windows_i686_gnu@0.48.5
	windows_i686_gnu@0.52.6
	windows_i686_gnullvm@0.52.6
	windows_i686_msvc@0.48.5
	windows_i686_msvc@0.52.6
	windows_x86_64_gnu@0.48.5
	windows_x86_64_gnu@0.52.6
	windows_x86_64_gnullvm@0.48.5
	windows_x86_64_gnullvm@0.52.6
	windows_x86_64_msvc@0.48.5
	windows_x86_64_msvc@0.52.6
	winit-android@0.31.0-beta.2
	winit-appkit@0.31.0-beta.2
	winit-common@0.31.0-beta.2
	winit-core@0.31.0-beta.2
	winit-orbital@0.31.0-beta.2
	winit-uikit@0.31.0-beta.2
	winit-wayland@0.31.0-beta.2
	winit-web@0.31.0-beta.2
	winit-win32@0.31.0-beta.2
	winit-x11@0.31.0-beta.2
	winit@0.31.0-beta.2
	winnow@1.0.4
	wit-bindgen@0.57.1
	x11-dl@2.21.0
	x11rb-protocol@0.13.2
	x11rb@0.13.2
	xcursor@0.3.10
	xkbcommon-dl@0.4.2
	xkbcommon@0.9.0
	xkeysym@0.2.1
	xml-rs@0.8.28
	zbus@5.18.0
	zbus_macros@5.18.0
	zbus_names@4.3.4
	zerocopy-derive@0.8.55
	zerocopy@0.8.55
	zmij@1.0.23
	zvariant@5.13.1
	zvariant_derive@5.13.1
	zvariant_utils@3.5.0
"
declare -A GIT_CRATES=(
	[smithay-drm-extras]='https://github.com/Smithay/smithay;812bd33259ff58810dadef6086d8385eeac1ca55;smithay-%commit%/smithay-drm-extras'
	[smithay]='https://github.com/Smithay/smithay;812bd33259ff58810dadef6086d8385eeac1ca55;smithay-%commit%'
)

inherit cargo systemd unpacker xdg

DESCRIPTION="Flutter-native Wayland compositor and desktop shell"
HOMEPAGE="https://github.com/denialwm/denial"
SRC_URI="
	https://github.com/denialwm/denial/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz
	https://denialwm.github.io/denial/x86_64/denial-${PV}-1-x86_64.pkg.tar.zst -> ${P}-aot.pkg.tar.zst
	${CARGO_CRATE_URIS}
"
DENIAL_ROOT="${WORKDIR}/denial-${PV}"
DENIAL_AOT_ROOT="${WORKDIR}/usr"
S="${DENIAL_ROOT}/compositor"

LICENSE="GPL-3+ CC-BY-SA-4.0 GPL-3 OFL-1.1 BSD"
SLOT="0"
KEYWORDS="-* ~amd64"
IUSE="test"
RESTRICT="!test? ( test )"
CONFIG_PROTECT="/etc/denial /etc/xdg/xdg-desktop-portal-wlr"

COMMON_DEPEND="
	dev-libs/libinput:=
	dev-libs/wayland
	media-libs/libglvnd
	media-libs/mesa
	sys-auth/seatd:=
	virtual/libudev
	x11-libs/libxkbcommon
"
DEPEND="${COMMON_DEPEND}
	virtual/pkgconfig
"
BDEPEND="app-arch/zstd"
RDEPEND="
	${COMMON_DEPEND}
	~dev-libs/denial-flutter-engine-bin-${PV}
	app-misc/ddcutil
	app-shells/bash
	media-libs/libpulse
	gnome-extra/zenity
	media-libs/fontconfig
	sys-apps/coreutils
	sys-apps/dbus
	sys-apps/xdg-desktop-portal
	sys-apps/xdg-desktop-portal-gtk
	sys-auth/rtkit
	gui-libs/xdg-desktop-portal-wlr
	x11-base/xwayland
	x11-misc/xkeyboard-config
"

src_unpack() {
	cargo_src_unpack
	unpacker "${DISTDIR}/${P}-aot.pkg.tar.zst"
}

QA_PREBUILT="usr/lib/denial/flutter/lib/libapp.so"
QA_PRESTRIPPED="${QA_PREBUILT}"

src_prepare() {
	sed -i 's/^DesktopNames=/X-DesktopNames=/' \
		"${DENIAL_ROOT}/packaging/arch/denial.desktop" || die
	default
}

src_configure() {
	local myfeatures=( flutter )
	cargo_src_configure
}

src_compile() {
	cargo_src_compile --bin deniald --bin denialctl
}

src_test() {
	cargo_src_test --lib --bin deniald --bin denialctl
}

src_install() {
	dobin "$(cargo_target_dir)/deniald"
	dobin "$(cargo_target_dir)/denialctl"
	dobin "${DENIAL_ROOT}/packaging/arch/denial-session"

	systemd_douserunit "${DENIAL_ROOT}/packaging/denial-session.target"

	exeinto /usr/lib/denial/flutter/lib
	doexe "${DENIAL_AOT_ROOT}/lib/denial/flutter/lib/libapp.so"

	insinto /usr/lib/denial/flutter/data
	doins -r "${DENIAL_AOT_ROOT}/lib/denial/flutter/data/flutter_assets"

	insinto /usr/share/wayland-sessions
	doins "${DENIAL_ROOT}/packaging/arch/denial.desktop"
	insinto /usr/share/xdg-desktop-portal
	doins "${DENIAL_ROOT}/packaging/arch/denial-portals.conf"
	insinto /etc/xdg/xdg-desktop-portal-wlr
	doins "${DENIAL_ROOT}/packaging/arch/xdg-desktop-portal-wlr-Denial"

	insinto /etc/denial
	doins "${DENIAL_ROOT}/packaging/arch/outputs.conf" "${DENIAL_ROOT}/packaging/arch/session.conf"

	dodoc "${DENIAL_ROOT}/README.md"
	dodoc "${DENIAL_ROOT}/dart_shell/assets/cursors/BIBATA_MODERN_ICE.md"
	dodoc "${DENIAL_ROOT}/dart_shell/assets/cursors/bibata_modern_ice.sha256"
	dodoc "${DENIAL_ROOT}/dart_shell/assets/fonts/README.md"
	dodoc "${DENIAL_ROOT}/dart_shell/assets/fonts/JetBrainsMono.sha256"
	dodoc "${DENIAL_ROOT}/dart_shell/assets/wallpapers/ATTRIBUTION.md"
	doman "${DENIAL_ROOT}/docs/man/denial-session.1" \
		"${DENIAL_ROOT}/docs/man/denialctl.1" \
		"${DENIAL_ROOT}/docs/man/deniald.1"
	dodoc "${DENIAL_ROOT}/LICENSE" \
		"${DENIAL_ROOT}/LICENSES/GPL-3.0-only.txt" \
		"${DENIAL_ROOT}/LICENSES/CC-BY-SA-4.0.txt"
	dodoc "${DENIAL_ROOT}/dart_shell/assets/fonts/OFL.txt"

	insinto /usr/share/denial
	echo "${PV}" > "${T}/version" || die
	doins "${T}/version"
}
