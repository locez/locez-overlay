"""Kernel source strategies (xanmod-sources, cachyos-sources) with Gentoo stable ZFS guard."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from ..common import (
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    download_first_ok,
    gh_api_json,
    http_get_text,
    list_release_ebuilds,
    parse_manifest,
    run,
    version_key,
    write_manifest,
)
from ..retention import ebuild_forbids_zfs, must_keep_for_zfs
from .base import BumpContext


def get_gentoo_genpatches_ver(kernel_ver: str) -> str | None:
    url = (
        "https://raw.githubusercontent.com/gentoo-mirror/gentoo/master/"
        f"sys-kernel/gentoo-sources/gentoo-sources-{kernel_ver}.ebuild"
    )
    try:
        text = http_get_text(url)
    except BumpError:
        return None
    m = re.search(r'^K_GENPATCHES_VER="([^"]+)"', text, re.M)
    return m.group(1) if m else None


def get_gentoo_stable_zfs_info() -> tuple[str, str]:
    """Return (stable_zfs_ver, modules_kernel_max) from gentoo-mirror/gentoo sys-fs/zfs."""
    items = gh_api_json("repos/gentoo-mirror/gentoo/contents/sys-fs/zfs")
    candidates: list[tuple[str, str]] = []
    for item in items:
        name = item.get("name", "")
        if not name.startswith("zfs-") or not name.endswith(".ebuild") or "9999" in name:
            continue
        candidates.append((name[len("zfs-") : -len(".ebuild")], name))
    candidates.sort(key=lambda x: version_key(x[0]), reverse=True)

    for ver, fname in candidates:
        text = http_get_text(
            f"https://raw.githubusercontent.com/gentoo-mirror/gentoo/master/sys-fs/zfs/{fname}"
        )
        kw_m = re.search(r'^\s*KEYWORDS="([^"]+)"', text, re.M)
        max_m = re.search(r"^MODULES_KERNEL_MAX=([0-9]+\.[0-9]+)", text, re.M)
        if kw_m and max_m and re.search(r"(?<!~)\bamd64\b", kw_m.group(1)):
            return (ver, max_m.group(1))
    raise BumpError("Could not determine stable amd64 sys-fs/zfs MODULES_KERNEL_MAX from Gentoo tree")


def _select_same_series(ctx: BumpContext, target_pv: str) -> tuple[str, Path, str]:
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    if not ebuilds:
        raise BumpError(f"No existing release ebuilds found for {ctx.spec.pkg}")
    series = ".".join(target_pv.split(".")[:2])
    same = [(v, eb) for v, eb in ebuilds if ".".join(v.split(".")[:2]) == series]
    if not same:
        raise BumpError(
            f"{ctx.spec.pn} minor series jump ({ebuilds[-1][0]} -> {ctx.target_ver}) requires manual review "
            f"(ZFS compatibility gate and patch verification)."
        )
    old_ver, old_eb = same[-1]
    return old_ver, old_eb, series


def _assert_zfs_gate(pkg: str, ver_label: str, series: str, eb_text: str) -> str:
    zfs_ver, zfs_max = get_gentoo_stable_zfs_info()
    if version_key(series) > version_key(zfs_max) and not ebuild_forbids_zfs(eb_text):
        raise BumpError(
            f"{pkg} {ver_label} exceeds stable sys-fs/zfs-{zfs_ver} MODULES_KERNEL_MAX={zfs_max}, "
            f'but template ebuild does not gate REQUIRED_USE="!zfs".'
        )
    return zfs_max


def _download_genpatches(series: str, gp_ver: str, work: Path) -> tuple[tuple[str, Path], tuple[str, Path]]:
    results = []
    for kind in ("base", "extras"):
        name = f"genpatches-{series}-{gp_ver}.{kind}.tar.xz"
        prefix = hashlib.blake2b(name.encode()).hexdigest()[:2]
        dl = download_first_ok(
            [
                f"https://dev.gentoo.org/~mpagano/dist/genpatches/{name}",
                f"https://distfiles.gentoo.org/distfiles/{prefix}/{name}",
            ],
            work / name,
        )
        results.append((name, dl))
    return results[0], results[1]


def _extract_filesdir_patches(eb_text: str, pdir: Path, pn: str, pv: str) -> list[Path]:
    """Discover local patches referenced via `eapply "${FILESDIR}/..."` in an ebuild."""
    patches: list[Path] = []
    for raw in re.findall(r'eapply\s+"?\$\{FILESDIR\}/([^"\s]+)"?', eb_text):
        resolved = raw.replace("${PN}", pn).replace("${PV}", pv).replace("${P}", f"{pn}-{pv}")
        candidate = pdir / "files" / resolved
        if candidate.is_file():
            patches.append(candidate)
    return patches


def _finalize_kernel_manifest(
    ctx: BumpContext,
    *,
    old_eb: Path,
    old_text: str,
    new_pv: str,
    new_text: str,
    series: str,
    gp_ver: str,
    zfs_max: str,
    old_dist_prefixes: tuple[str, ...],
    new_dists: dict[str, Path],
) -> None:
    pdir = ctx.pkg_dir
    entries = parse_manifest(pdir / "Manifest")
    use_guard = ctx.spec.retention.guard == "keep-last-zfs-compatible"
    keep_old = use_guard and must_keep_for_zfs(ctx.repo_root, ctx.spec.pkg, old_eb, new_pv, new_text, zfs_max)
    if not keep_old:
        old_gp_m = re.search(r'^K_GENPATCHES_VER="([^"]+)"', old_text, re.M)
        old_gp = old_gp_m.group(1) if old_gp_m else None
        old_eb.unlink()
        for k in list(entries):
            if any(k.startswith(pref) for pref in old_dist_prefixes):
                entries.pop(k, None)
        if old_gp and old_gp != gp_ver:
            entries.pop(f"genpatches-{series}-{old_gp}.base.tar.xz", None)
            entries.pop(f"genpatches-{series}-{old_gp}.extras.tar.xz", None)

    for name, path in new_dists.items():
        entries[name] = dist_manifest_line(name, path)
    write_manifest(pdir / "Manifest", entries)


def bump_xanmod(ctx: BumpContext) -> None:
    new_ver = ctx.target_ver
    old_ver, old_eb, series = _select_same_series(ctx, new_ver)
    if old_ver == new_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {new_ver}")

    eb_text = old_eb.read_text(encoding="utf-8")
    zfs_max = _assert_zfs_gate(ctx.spec.pkg, new_ver, series, eb_text)
    gp_ver = get_gentoo_genpatches_ver(new_ver)
    if not gp_ver:
        raise BumpSkip(f"gentoo-sources-{new_ver} (genpatches) not published in Gentoo tree yet")

    xm_m = re.search(r'^XANMOD_VERSION="([^"]+)"', eb_text, re.M)
    xm_ver = xm_m.group(1) if xm_m else "1"
    major = new_ver.split(".")[0]
    linux_dist = f"linux-{series}.tar.xz"
    xm_patch = f"patch-{new_ver}-xanmod{xm_ver}.xz"

    linux_dl = download_first_ok(
        [
            f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/{linux_dist}",
            f"https://mirrors.edge.kernel.org/pub/linux/kernel/v{major}.x/{linux_dist}",
        ],
        ctx.work / linux_dist,
    )
    (gp_base, gp_base_dl), (gp_extras, gp_extras_dl) = _download_genpatches(series, gp_ver, ctx.work)
    xm_dl = download_file(
        f"https://downloads.sourceforge.net/project/xanmod/releases/main/{new_ver}-xanmod{xm_ver}/{xm_patch}",
        ctx.work / xm_patch,
    )

    test_dir = ctx.work / "xanmod_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    for tar in (linux_dl, gp_base_dl, gp_extras_dl):
        run(["tar", "-xf", str(tar), "-C", str(test_dir)])
    xm_raw = test_dir / f"patch-{new_ver}-xanmod{xm_ver}"
    with xm_raw.open("wb") as out:
        res = subprocess.run(["xz", "-dc", str(xm_dl)], stdout=out, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise BumpError(f"Failed to decompress {xm_patch}: {res.stderr.decode()}")

    kdir = test_dir / f"linux-{series}"
    run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(xm_raw)], cwd=kdir)
    for local_patch in _extract_filesdir_patches(eb_text, ctx.pkg_dir, ctx.spec.pn, new_ver):
        run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(local_patch)], cwd=kdir)
    for p in sorted(test_dir.glob("10*linux*patch")):
        p.unlink()
    for p in sorted(test_dir.glob("*.patch")):
        run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(p)], cwd=kdir)
    shutil.rmtree(test_dir)

    new_eb_text = re.sub(r'^K_GENPATCHES_VER="[^"]+"', f'K_GENPATCHES_VER="{gp_ver}"', eb_text, count=1, flags=re.M)
    (ctx.pkg_dir / f"{ctx.spec.pn}-{new_ver}.ebuild").write_text(new_eb_text, encoding="utf-8")

    _finalize_kernel_manifest(
        ctx,
        old_eb=old_eb,
        old_text=eb_text,
        new_pv=new_ver,
        new_text=new_eb_text,
        series=series,
        gp_ver=gp_ver,
        zfs_max=zfs_max,
        old_dist_prefixes=(f"patch-{old_ver}-xanmod{xm_ver}.xz",),
        new_dists={linux_dist: linux_dl, gp_base: gp_base_dl, gp_extras: gp_extras_dl, xm_patch: xm_dl},
    )


def bump_cachyos(ctx: BumpContext) -> None:
    new_full_ver = ctx.target_ver
    m = re.match(r"^([0-9]+\.[0-9]+\.[0-9]+)-([0-9]+)$", new_full_ver)
    if not m:
        raise BumpError(f"Unexpected cachyos version format: {new_full_ver}")
    new_pv, new_rel = m.group(1), m.group(2)

    old_ver, old_eb, series = _select_same_series(ctx, new_pv)
    old_text = old_eb.read_text(encoding="utf-8")
    old_rel_m = re.search(r'^CACHYOS_RELEASE="([^"]+)"', old_text, re.M)
    old_rel = old_rel_m.group(1) if old_rel_m else "1"
    if f"{old_ver}-{old_rel}" == new_full_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {new_full_ver}")
    if new_pv == old_ver:
        raise BumpError(
            f"cachyos-sources same-PV release bump ({old_ver}-{old_rel} -> {new_full_ver}) "
            f"would collide on distfile name {ctx.spec.pn}-{new_pv}.tar.gz; needs -r revision handling."
        )

    zfs_max = _assert_zfs_gate(ctx.spec.pkg, new_full_ver, series, old_text)
    gp_ver = get_gentoo_genpatches_ver(new_pv)
    if not gp_ver:
        raise BumpSkip(f"gentoo-sources-{new_pv} (genpatches) not published in Gentoo tree yet")

    cachy_src = f"cachyos-{new_pv}-{new_rel}"
    src_dist = f"{ctx.spec.pn}-{new_pv}.tar.gz"
    patch_base = f"https://raw.githubusercontent.com/CachyOS/kernel-patches/master/{series}"
    aufs_m = re.search(r"misc/(0001-aufs-[^\s]+)", old_text)
    if not aufs_m:
        raise BumpError("Cannot locate AUFS patch filename in existing cachyos-sources ebuild")

    cachy_patches = [
        (f"{ctx.spec.pn}-{new_pv}-0001-bore-cachy.patch", f"{patch_base}/sched/0001-bore-cachy.patch"),
        (f"{ctx.spec.pn}-{new_pv}-0001-prjc-cachy.patch", f"{patch_base}/sched/0001-prjc-cachy.patch"),
        (f"{ctx.spec.pn}-{new_pv}-0001-acpi-call.patch", f"{patch_base}/misc/0001-acpi-call.patch"),
        (f"{ctx.spec.pn}-{new_pv}-0001-aufs.patch", f"{patch_base}/misc/{aufs_m.group(1)}"),
        (f"{ctx.spec.pn}-{new_pv}-0001-handheld.patch", f"{patch_base}/misc/0001-handheld.patch"),
        (f"{ctx.spec.pn}-{new_pv}-0001-rt-i915.patch", f"{patch_base}/misc/0001-rt-i915.patch"),
        (f"{ctx.spec.pn}-{new_pv}-dkms-clang.patch", f"{patch_base}/misc/dkms-clang.patch"),
    ]

    src_dl = download_file(
        f"https://github.com/CachyOS/linux/releases/download/{cachy_src}/{cachy_src}.tar.gz",
        ctx.work / src_dist,
    )
    (gp_base, gp_base_dl), (gp_extras, gp_extras_dl) = _download_genpatches(series, gp_ver, ctx.work)
    patch_dls = {dname: download_file(url, ctx.work / dname) for dname, url in cachy_patches}

    for sched in ("bore", "prjc"):
        tdir = ctx.work / f"cachy_test_{sched}"
        tdir.mkdir(parents=True, exist_ok=True)
        for tar in (src_dl, gp_base_dl, gp_extras_dl):
            run(["tar", "-xf", str(tar), "-C", str(tdir)])
        kdir = tdir / cachy_src
        if not kdir.is_dir():
            raise BumpError(f"Expected directory {cachy_src} inside {src_dist}")
        if not list(tdir.glob(f"10*linux-{new_pv}.patch")):
            raise BumpError(f"genpatches-{series}-{gp_ver} is missing linux-{new_pv}.patch")
        for p in sorted(tdir.glob("10*linux*patch")):
            p.unlink()
        for p in sorted(tdir.glob("*.patch")):
            run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(p)], cwd=kdir)

        order = [
            f"{ctx.spec.pn}-{new_pv}-0001-{sched}-cachy.patch",
            f"{ctx.spec.pn}-{new_pv}-0001-acpi-call.patch",
            f"{ctx.spec.pn}-{new_pv}-0001-aufs.patch",
            f"{ctx.spec.pn}-{new_pv}-0001-handheld.patch",
        ]
        if sched == "bore":
            order.append(f"{ctx.spec.pn}-{new_pv}-0001-rt-i915.patch")
        order.append(f"{ctx.spec.pn}-{new_pv}-dkms-clang.patch")
        for dname in order:
            run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(patch_dls[dname])], cwd=kdir)
        shutil.rmtree(tdir)

    new_text = re.sub(r'^K_GENPATCHES_VER="[^"]+"', f'K_GENPATCHES_VER="{gp_ver}"', old_text, count=1, flags=re.M)
    new_text = re.sub(r'^CACHYOS_RELEASE="[^"]+"', f'CACHYOS_RELEASE="{new_rel}"', new_text, count=1, flags=re.M)
    (ctx.pkg_dir / f"{ctx.spec.pn}-{new_pv}.ebuild").write_text(new_text, encoding="utf-8")

    new_dists = {src_dist: src_dl, gp_base: gp_base_dl, gp_extras: gp_extras_dl, **patch_dls}
    _finalize_kernel_manifest(
        ctx,
        old_eb=old_eb,
        old_text=old_text,
        new_pv=new_pv,
        new_text=new_text,
        series=series,
        gp_ver=gp_ver,
        zfs_max=zfs_max,
        old_dist_prefixes=(f"{ctx.spec.pn}-{old_ver}",),
        new_dists=new_dists,
    )
