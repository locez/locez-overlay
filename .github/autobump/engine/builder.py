"""Gentoo stage3 + official binhost build & integration smoke-test runner."""

from __future__ import annotations

import shlex

from .common import list_release_ebuilds
from .schema import AutomationConfig, PackageSpec


def resolve_target_atom(cfg: AutomationConfig, pkg: str, *, live: bool = False) -> str:
    if live:
        return f"={pkg}-9999"
    ebuilds = list_release_ebuilds(cfg.repo_root, pkg)
    if ebuilds:
        return f"={pkg}-{ebuilds[-1][0]}"
    pn = pkg.split("/", 1)[1]
    if (cfg.repo_root / pkg / f"{pn}-9999.ebuild").exists():
        return f"={pkg}-9999"
    return pkg


def _render_smoke_block(
    spec: PackageSpec,
    overlay_mount: str,
    emerge_common_opts: str,
    *,
    python_target: str = "",
) -> list[str]:
    if not spec.smoke_test and not spec.smoke_test_script:
        return []
    block: list[str] = [f"echo '=== [smoke_test] {spec.pkg} ==='"]
    if spec.build.extra_pkgs:
        extra = " ".join(shlex.quote(p) for p in spec.build.extra_pkgs)
        block.append(f"emerge {emerge_common_opts} {extra}")
    block.append("(")
    if python_target and python_target.startswith("python"):
        # Point Gentoo python-exec (/usr/bin/python3) to the target interpreter being validated.
        py_exe = python_target.replace("_", ".")
        block.append(f"  export EPYTHON={shlex.quote(py_exe)}")
    for k, v in sorted(spec.build.env.items()):
        block.append(f"  export {k}={shlex.quote(v)}")
    if spec.smoke_test_script:
        script_path = f"{overlay_mount.rstrip('/')}/{spec.smoke_test_script.lstrip('/')}"
        block.append(f"  bash {shlex.quote(script_path)}")
    if spec.smoke_test:
        block.append(f"  {spec.smoke_test}")
    block.append(")")
    return block


def render_container_test_script(
    cfg: AutomationConfig,
    spec: PackageSpec,
    *,
    atom: str,
    overlay_mount: str = "/overlay",
    log_dir: str = "/build-logs",
    python_target: str = "",
) -> str:
    """Generate a self-contained bash script to run inside gentoo/stage3."""
    mode = spec.build.mode
    repo_name = cfg.overlay.name
    pkg = spec.pkg
    use_sets = spec.build.effective_use_sets()

    binhost_lines: list[str] = []
    if cfg.build.use_binhost:
        binhost_lines = [
            f"BINHOST_URI={shlex.quote(cfg.build.binhost_uri)}",
            'if [[ "${GENTOO_ARCH}" != "amd64" ]]; then',
            '  BINHOST_URI="${BINHOST_URI/\\/amd64\\/binpackages\\///${GENTOO_ARCH}/binpackages/}"',
            '  BINHOST_URI="${BINHOST_URI/\\/x86-64\\///${BINHOST_SUBARCH}/}"',
            "fi",
            "mkdir -p /etc/portage/binrepos.conf",
            "cat > /etc/portage/binrepos.conf/gentoobinhost.conf <<BINHOST",
            "[gentoobinhost]",
            "priority = 1",
            "sync-uri = ${BINHOST_URI}",
            "BINHOST",
        ]

    # Note: omit explicit --binpkg-respect-use=y (already default for --usepkg/--getbinpkg);
    # passing it explicitly causes Portage to disable --autounmask-use.
    emerge_common_opts = (
        '--verbose --quiet-build=y --oneshot --jobs="${NPROC}" --load-average="${NPROC}"'
        f" --usepkg --usepkg-exclude={shlex.quote(pkg)} --buildpkg-exclude={shlex.quote(pkg)}"
        " --binpkg-changed-deps=y"
    )
    if cfg.build.use_binhost:
        emerge_common_opts += " --getbinpkg"

    profile_lines: list[str] = []
    if cfg.build.profile:
        profile_lines = [
            f"PROFILE={shlex.quote(cfg.build.profile)}",
            'PROFILE="${PROFILE/\\/amd64\\///${GENTOO_ARCH}/}"',
            'eselect profile set "${PROFILE}"',
        ]

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'case "$(uname -m)" in',
        '  x86_64)  GENTOO_ARCH="amd64"; BINHOST_SUBARCH="x86-64" ;;',
        '  aarch64) GENTOO_ARCH="arm64"; BINHOST_SUBARCH="arm64" ;;',
        '  *)       GENTOO_ARCH="$(uname -m)"; BINHOST_SUBARCH="$(uname -m)" ;;',
        "esac",
        f"mkdir -p {shlex.quote(log_dir)} /var/cache/binpkgs /etc/portage/repos.conf /etc/portage/package.accept_keywords /etc/portage/package.use /etc/portage/env /etc/portage/package.env",
        "",
        "# 1. Configure main gentoo tree, profile + mounted overlay",
        "if [[ ! -d /var/db/repos/gentoo/metadata ]]; then",
        "  emerge-webrsync -q",
        "fi",
        *profile_lines,
        f"cat > /etc/portage/repos.conf/{repo_name}.conf <<'REPO'",
        f"[{repo_name}]",
        f"location = {overlay_mount}",
        "priority = 100",
        "auto-sync = no",
        "REPO",
        *binhost_lines,
        "",
        "# 2. Global CI Portage settings (accept keywords/licenses, CI binpkg cache, logs)",
        "NPROC=$(nproc)",
        "cat >> /etc/portage/make.conf <<'MAKECONF'",
        'ACCEPT_LICENSE="*"',
        'CONFIG_PROTECT="-*"',
        'FEATURES="${FEATURES} -sandbox -usersandbox -ipc-sandbox -network-sandbox -pid-sandbox -userfetch -merge-sync -binpkg-request-signature buildpkg parallel-fetch parallel-install qa-unresolved-soname-deps"',
        f"PORTAGE_LOGDIR={shlex.quote(log_dir)}",
        "MAKECONF",
        'echo "MAKEOPTS=\\"-j${NPROC}\\"" >> /etc/portage/make.conf',
        "",
        "# Refresh local CI binpkg index if restored from cache",
        "if find /var/cache/binpkgs -name '*.gpkg.tar' -print -quit 2>/dev/null | grep -q .; then",
        "  emaint binhost --fix >/dev/null 2>&1 || true",
        "fi",
        "",
        "# Accept ~arch for overlay packages and ** for live ebuilds being tested",
        f'echo "*/*::{repo_name} ~${{GENTOO_ARCH}}" > /etc/portage/package.accept_keywords/overlay',
        f"echo '{pkg} **' >> /etc/portage/package.accept_keywords/overlay",
        "echo 'FEATURES=\"test\"' > /etc/portage/env/run-tests.conf",
        "",
        "# Capture temp build.log into mounted log_dir on failure",
        "trap '",
        f"  for f in /var/tmp/portage/*/*/temp/build.log; do",
        f'    [[ -f "$f" ]] && cp -a "$f" {shlex.quote(log_dir)}/build.log || true',
        "  done",
        "' ERR",
    ]

    if mode == "dep-resolve":
        lines += [
            f"echo '=== [dep-resolve] {atom} ==='",
            f"emerge --pretend --verbose {shlex.quote(atom)}",
        ]
    elif mode in ("emerge-full", "emerge-bin"):
        target_use = list(spec.build.use)
        if mode == "emerge-full" and spec.build.test:
            # Enable USE=test in package.use so Portage installs test? ( ... ) BDEPENDs
            # (e.g. pytest / pyqt6[testlib]); package.env FEATURES=test alone runs src_test
            # after dep-calc without pulling test dependencies.
            if "test" not in target_use:
                target_use.append("test")
            lines.append(f"echo {shlex.quote(pkg + ' run-tests.conf')} > /etc/portage/package.env/target")
        if python_target:
            target_use.append(f"python_targets_{python_target}")
        if target_use:
            lines.append(
                f"echo {shlex.quote(pkg + ' ' + ' '.join(target_use))} > /etc/portage/package.use/10-target"
            )
        if python_target:
            lines += [
                "mkdir -p /etc/portage/profile",
                f"echo {shlex.quote(f'-python_targets_{python_target}')} >> /etc/portage/profile/use.stable.mask",
                'echo "dev-lang/python ~${GENTOO_ARCH}" >> /etc/portage/package.accept_keywords/overlay',
                "# Prefer single-target mode for dev-python/* + target pkg so heavy deps (pyqt6/cython/lxml)",
                "# compile once instead of twice; auto-fallback to additive mode if dep resolution fails.",
                "cat > /etc/portage/package.use/00-python-target <<'PYTARGET'",
                f"dev-python/* PYTHON_TARGETS: -* {python_target}",
                f"{pkg} PYTHON_TARGETS: -* {python_target}",
                "PYTARGET",
                f"if emerge {emerge_common_opts} --pretend --autounmask=y --autounmask-use=y --autounmask-write=y --autounmask-continue=y {shlex.quote(atom)} >/dev/null 2>&1; then",
                f"  echo 'Using single-target PYTHON_TARGETS={python_target} for dev-python/* and {pkg}'",
                "else",
                f"  echo 'Single-target {python_target} probe failed; falling back to additive PYTHON_TARGETS'",
                "  rm -f /etc/portage/package.use/zz-autounmask",
                f"  echo {shlex.quote(f'*/* PYTHON_TARGETS: {python_target}')} > /etc/portage/package.use/00-python-target",
                "fi",
            ]
        lines += [
            f"echo '=== [{mode}] Installing dependencies & building {atom} ==='",
            f"emerge {emerge_common_opts} --autounmask=y --autounmask-use=y --autounmask-write=y --autounmask-continue=y {shlex.quote(atom)}",
        ]
        lines += _render_smoke_block(spec, overlay_mount, emerge_common_opts, python_target=python_target)
    elif mode == "kernel-sources":
        for idx, flags in enumerate(use_sets, 1):
            flag_str = " ".join(flags)
            lines += [
                f"echo '=== [kernel-sources set #{idx}] {atom} USE=\"{flag_str}\" ==='",
                f"echo {shlex.quote(pkg + ' ' + flag_str)} > /etc/portage/package.use/target",
                f"emerge {emerge_common_opts} --autounmask=y --autounmask-use=y --autounmask-write=y --autounmask-continue=y {shlex.quote(atom)}",
            ]
            lines += _render_smoke_block(spec, overlay_mount, emerge_common_opts)
            if spec.build.check_kconfig:
                lines += [
                    "KDIR=$(ls -dt /usr/src/linux-* | head -n1)",
                    'echo "Running Kconfig defconfig check in ${KDIR}..."',
                    'make -C "${KDIR}" O=/tmp/kconfig-check defconfig >/dev/null',
                    "rm -rf /tmp/kconfig-check",
                ]

    lines.append(f"echo 'BUILD_TEST_PASSED: {atom}'")
    return "\n".join(lines) + "\n"
