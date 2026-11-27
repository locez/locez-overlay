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


def _render_smoke_block(spec: PackageSpec, overlay_mount: str, emerge_common_opts: str) -> list[str]:
    if not spec.smoke_test and not spec.smoke_test_script:
        return []
    block: list[str] = [f"echo '=== [smoke_test] {spec.pkg} ==='"]
    if spec.build.extra_pkgs:
        extra = " ".join(shlex.quote(p) for p in spec.build.extra_pkgs)
        block.append(f"emerge {emerge_common_opts} {extra}")
    block.append("(")
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
) -> str:
    """Generate a self-contained bash script to run inside gentoo/stage3."""
    mode = spec.build.mode
    repo_name = cfg.overlay.name
    pkg = spec.pkg
    use_sets = spec.build.effective_use_sets()

    binhost_lines: list[str] = []
    if cfg.build.use_binhost:
        binhost_lines = [
            "mkdir -p /etc/portage/binrepos.conf",
            "cat > /etc/portage/binrepos.conf/gentoobinhost.conf <<'BINHOST'",
            "[gentoobinhost]",
            "priority = 1",
            f"sync-uri = {cfg.build.binhost_uri}",
            "BINHOST",
            "# Initialize Gentoo official binhost GPG trust keys so --getbinpkg verifies signatures",
            "if command -v getuto >/dev/null 2>&1; then",
            "  getuto || true",
            "fi",
        ]

    emerge_common_opts = "--verbose --quiet-build=y --oneshot"
    if cfg.build.use_binhost:
        emerge_common_opts += f" --getbinpkg --binpkg-respect-use=y --usepkg-exclude={shlex.quote(pkg)}"

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"mkdir -p {shlex.quote(log_dir)} /etc/portage/repos.conf /etc/portage/package.accept_keywords /etc/portage/package.use /etc/portage/env /etc/portage/package.env",
        "",
        "# 1. Configure main gentoo tree + mounted overlay",
        "if [[ ! -d /var/db/repos/gentoo/metadata ]]; then",
        "  emerge-webrsync -q",
        "fi",
        f"cat > /etc/portage/repos.conf/{repo_name}.conf <<'REPO'",
        f"[{repo_name}]",
        f"location = {overlay_mount}",
        "priority = 100",
        "auto-sync = no",
        "REPO",
        *binhost_lines,
        "",
        "# 2. Global CI Portage settings (accept keywords/licenses & direct logs to mounted log_dir)",
        "cat >> /etc/portage/make.conf <<'MAKECONF'",
        'ACCEPT_LICENSE="*"',
        'FEATURES="${FEATURES} -sandbox -usersandbox -ipc-sandbox -network-sandbox -pid-sandbox qa-unresolved-soname-deps"',
        f"PORTAGE_LOGDIR={shlex.quote(log_dir)}",
        "MAKECONF",
        'echo "MAKEOPTS=\\"-j$(nproc)\\"" >> /etc/portage/make.conf',
        "",
        "# Accept ~amd64 for overlay packages and ** for live ebuilds being tested",
        f"echo '*/*::{repo_name} ~amd64' > /etc/portage/package.accept_keywords/overlay",
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
        if spec.build.use:
            lines.append(
                f"echo {shlex.quote(pkg + ' ' + ' '.join(spec.build.use))} > /etc/portage/package.use/target"
            )
        if mode == "emerge-full" and spec.build.test:
            lines.append(f"echo {shlex.quote(pkg + ' run-tests.conf')} > /etc/portage/package.env/target")
        lines += [
            f"echo '=== [{mode}] Installing dependencies & building {atom} ==='",
            f"emerge {emerge_common_opts} --autounmask-write=y --autounmask-continue=y {shlex.quote(atom)}",
        ]
        lines += _render_smoke_block(spec, overlay_mount, emerge_common_opts)
    elif mode == "kernel-sources":
        for idx, flags in enumerate(use_sets, 1):
            flag_str = " ".join(flags)
            lines += [
                f"echo '=== [kernel-sources set #{idx}] {atom} USE=\"{flag_str}\" ==='",
                f"echo {shlex.quote(pkg + ' ' + flag_str)} > /etc/portage/package.use/target",
                f"emerge {emerge_common_opts} --autounmask-write=y --autounmask-continue=y {shlex.quote(atom)}",
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
