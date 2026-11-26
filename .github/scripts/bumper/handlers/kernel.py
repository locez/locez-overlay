"""Kernel source handlers (xanmod-sources, cachyos-sources) with ZFS compatibility guards."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from ..common import (
    REPO_ROOT,
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


def get_gentoo_genpatches_ver(kernel_ver: str) -> str | None:
    """Look up K_GENPATCHES_VER from gentoo-mirror/gentoo for a given kernel PV."""
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


def ebuild_forbids_zfs(eb_text: str) -> bool:
    m = re.search(r'REQUIRED_USE="([^"]*)"', eb_text, re.S)
    return bool(m and re.search(r"(^|\s)!zfs(\s|$)", m.group(1)))


def ebuild_supports_zfs(ver: str, eb_text: str, zfs_max_series: str) -> bool:
    if ebuild_forbids_zfs(eb_text):
        return False
    series = ".".join(ver.split(".")[:2])
    return version_key(series) <= version_key(zfs_max_series)


def keep_old_kernel_for_zfs(
    pkg: str,
    old_eb: Path,
    new_ver: str,
    new_eb_text: str,
    zfs_max_series: str,
) -> bool:
    """Return True if old_eb must be kept because dropping it would leave no ZFS-compatible kernel."""
    if ebuild_supports_zfs(new_ver, new_eb_text, zfs_max_series):
        return False
    old_resolved = old_eb.resolve()
    for ver, eb in list_release_ebuilds(pkg):
        if eb.resolve() == old_resolved or ver == new_ver:
            continue
        if ebuild_supports_zfs(ver, eb.read_text(encoding="utf-8"), zfs_max_series):
            return False
    return True


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


def bump_xanmod(pkg: str, new_ver: str, work: Path) -> None:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    new_series = ".".join(new_ver.split(".")[:2])
    same_series = [(v, eb) for v, eb in ebuilds if ".".join(v.split(".")[:2]) == new_series]
    if not same_series:
        raise BumpError(
            f"xanmod-sources minor series jump ({ebuilds[-1][0]} -> {new_ver}) requires manual review "
            f"(ZFS compatibility gate and patch verification)."
        )
    old_ver, old_eb = same_series[-1]
    if old_ver == new_ver:
        raise BumpSkip(f"{pkg} already at {new_ver}")

    zfs_ver, zfs_max = get_gentoo_stable_zfs_info()
    gp_ver = get_gentoo_genpatches_ver(new_ver)
    if not gp_ver:
        raise BumpSkip(f"gentoo-sources-{new_ver} (genpatches) not published in Gentoo tree yet")

    eb_text = old_eb.read_text(encoding="utf-8")
    if version_key(new_series) > version_key(zfs_max) and not ebuild_forbids_zfs(eb_text):
        raise BumpError(
            f"xanmod-sources {new_ver} exceeds stable sys-fs/zfs-{zfs_ver} MODULES_KERNEL_MAX={zfs_max}, "
            f'but template ebuild does not gate REQUIRED_USE="!zfs".'
        )
    xm_m = re.search(r'^XANMOD_VERSION="([^"]+)"', eb_text, re.M)
    xm_ver = xm_m.group(1) if xm_m else "1"

    major = new_ver.split(".")[0]
    linux_dist = f"linux-{new_series}.tar.xz"
    xm_patch = f"patch-{new_ver}-xanmod{xm_ver}.xz"

    linux_dl = download_first_ok(
        [
            f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/{linux_dist}",
            f"https://mirrors.edge.kernel.org/pub/linux/kernel/v{major}.x/{linux_dist}",
        ],
        work / linux_dist,
    )
    (gp_base, gp_base_dl), (gp_extras, gp_extras_dl) = _download_genpatches(new_series, gp_ver, work)
    xm_dl = download_file(
        f"https://downloads.sourceforge.net/project/xanmod/releases/main/{new_ver}-xanmod{xm_ver}/{xm_patch}",
        work / xm_patch,
    )

    test_dir = work / "xanmod_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    for tar in (linux_dl, gp_base_dl, gp_extras_dl):
        run(["tar", "-xf", str(tar), "-C", str(test_dir)])
    xm_raw = test_dir / f"patch-{new_ver}-xanmod{xm_ver}"
    with xm_raw.open("wb") as out:
        res = subprocess.run(["xz", "-dc", str(xm_dl)], stdout=out, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise BumpError(f"Failed to decompress {xm_patch}: {res.stderr.decode()}")

    kdir = test_dir / f"linux-{new_series}"
    run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(xm_raw)], cwd=kdir)

    local_patch = pdir / "files" / "xanmod-sources-7.2-x86-native-cache-shift.patch"
    if local_patch.exists() and local_patch.name in eb_text:
        run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(local_patch)], cwd=kdir)

    for p in sorted(test_dir.glob("10*linux*patch")):
        p.unlink()
    for p in sorted(test_dir.glob("*.patch")):
        run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(p)], cwd=kdir)
    shutil.rmtree(test_dir)

    new_eb_text = re.sub(r'^K_GENPATCHES_VER="[^"]+"', f'K_GENPATCHES_VER="{gp_ver}"', eb_text, count=1, flags=re.M)
    new_eb = pdir / f"xanmod-sources-{new_ver}.ebuild"
    new_eb.write_text(new_eb_text, encoding="utf-8")

    entries = parse_manifest(pdir / "Manifest")
    if not keep_old_kernel_for_zfs(pkg, old_eb, new_ver, new_eb_text, zfs_max):
        old_gp_m = re.search(r'^K_GENPATCHES_VER="([^"]+)"', eb_text, re.M)
        old_gp = old_gp_m.group(1) if old_gp_m else None
        old_eb.unlink()
        entries.pop(f"patch-{old_ver}-xanmod{xm_ver}.xz", None)
        if old_gp and old_gp != gp_ver:
            entries.pop(f"genpatches-{new_series}-{old_gp}.base.tar.xz", None)
            entries.pop(f"genpatches-{new_series}-{old_gp}.extras.tar.xz", None)

    for name, path in ((linux_dist, linux_dl), (gp_base, gp_base_dl), (gp_extras, gp_extras_dl), (xm_patch, xm_dl)):
        entries[name] = dist_manifest_line(name, path)
    write_manifest(pdir / "Manifest", entries)


def bump_cachyos(pkg: str, new_full_ver: str, work: Path) -> None:
    """new_full_ver is e.g. '7.3.7-1'."""
    pdir = REPO_ROOT / pkg
    m = re.match(r"^([0-9]+\.[0-9]+\.[0-9]+)-([0-9]+)$", new_full_ver)
    if not m:
        raise BumpError(f"Unexpected cachyos version format: {new_full_ver}")
    new_pv, new_rel = m.group(1), m.group(2)

    ebuilds = list_release_ebuilds(pkg)
    new_series = ".".join(new_pv.split(".")[:2])
    same_series = [(v, eb) for v, eb in ebuilds if ".".join(v.split(".")[:2]) == new_series]
    if not same_series:
        raise BumpError(
            f"cachyos-sources minor series jump ({ebuilds[-1][0]} -> {new_full_ver}) "
            f"requires manual review of CachyOS/{new_series} patch list and ZFS gate."
        )
    old_ver, old_eb = same_series[-1]
    old_text = old_eb.read_text(encoding="utf-8")
    old_rel_m = re.search(r'^CACHYOS_RELEASE="([^"]+)"', old_text, re.M)
    old_rel = old_rel_m.group(1) if old_rel_m else "1"
    if f"{old_ver}-{old_rel}" == new_full_ver:
        raise BumpSkip(f"{pkg} already at {new_full_ver}")
    if new_pv == old_ver:
        raise BumpError(
            f"cachyos-sources same-PV release bump ({old_ver}-{old_rel} -> {new_full_ver}) "
            f"would collide on distfile name cachyos-sources-{new_pv}.tar.gz; needs -r revision handling."
        )

    zfs_ver, zfs_max = get_gentoo_stable_zfs_info()
    if version_key(new_series) > version_key(zfs_max) and not ebuild_forbids_zfs(old_text):
        raise BumpError(
            f"cachyos-sources {new_full_ver} exceeds stable sys-fs/zfs-{zfs_ver} MODULES_KERNEL_MAX={zfs_max}, "
            f'but template ebuild does not gate REQUIRED_USE="!zfs".'
        )

    gp_ver = get_gentoo_genpatches_ver(new_pv)
    if not gp_ver:
        raise BumpSkip(f"gentoo-sources-{new_pv} (genpatches) not published in Gentoo tree yet")

    cachy_src = f"cachyos-{new_pv}-{new_rel}"
    src_dist = f"cachyos-sources-{new_pv}.tar.gz"
    patch_base = f"https://raw.githubusercontent.com/CachyOS/kernel-patches/master/{new_series}"
    aufs_m = re.search(r"misc/(0001-aufs-[^\s]+)", old_text)
    if not aufs_m:
        raise BumpError("Cannot locate AUFS patch filename in existing cachyos-sources ebuild")
    aufs_file = aufs_m.group(1)

    cachy_patches = [
        (f"cachyos-sources-{new_pv}-0001-bore-cachy.patch", f"{patch_base}/sched/0001-bore-cachy.patch"),
        (f"cachyos-sources-{new_pv}-0001-prjc-cachy.patch", f"{patch_base}/sched/0001-prjc-cachy.patch"),
        (f"cachyos-sources-{new_pv}-0001-acpi-call.patch", f"{patch_base}/misc/0001-acpi-call.patch"),
        (f"cachyos-sources-{new_pv}-0001-aufs.patch", f"{patch_base}/misc/{aufs_file}"),
        (f"cachyos-sources-{new_pv}-0001-handheld.patch", f"{patch_base}/misc/0001-handheld.patch"),
        (f"cachyos-sources-{new_pv}-0001-rt-i915.patch", f"{patch_base}/misc/0001-rt-i915.patch"),
        (f"cachyos-sources-{new_pv}-dkms-clang.patch", f"{patch_base}/misc/dkms-clang.patch"),
    ]

    src_dl = download_file(
        f"https://github.com/CachyOS/linux/releases/download/{cachy_src}/{cachy_src}.tar.gz",
        work / src_dist,
    )
    (gp_base, gp_base_dl), (gp_extras, gp_extras_dl) = _download_genpatches(new_series, gp_ver, work)
    patch_dls = {dname: download_file(url, work / dname) for dname, url in cachy_patches}

    for sched in ("bore", "prjc"):
        tdir = work / f"cachy_test_{sched}"
        tdir.mkdir(parents=True, exist_ok=True)
        for tar in (src_dl, gp_base_dl, gp_extras_dl):
            run(["tar", "-xf", str(tar), "-C", str(tdir)])
        kdir = tdir / cachy_src
        if not kdir.is_dir():
            raise BumpError(f"Expected directory {cachy_src} inside {src_dist}")
        if not list(tdir.glob(f"10*linux-{new_pv}.patch")):
            raise BumpError(f"genpatches-{new_series}-{gp_ver} is missing linux-{new_pv}.patch")
        for p in sorted(tdir.glob("10*linux*patch")):
            p.unlink()
        for p in sorted(tdir.glob("*.patch")):
            run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(p)], cwd=kdir)

        order = [
            f"cachyos-sources-{new_pv}-0001-{sched}-cachy.patch",
            f"cachyos-sources-{new_pv}-0001-acpi-call.patch",
            f"cachyos-sources-{new_pv}-0001-aufs.patch",
            f"cachyos-sources-{new_pv}-0001-handheld.patch",
        ]
        if sched == "bore":
            order.append(f"cachyos-sources-{new_pv}-0001-rt-i915.patch")
        order.append(f"cachyos-sources-{new_pv}-dkms-clang.patch")
        for dname in order:
            run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(patch_dls[dname])], cwd=kdir)
        shutil.rmtree(tdir)

    new_text = re.sub(r'^K_GENPATCHES_VER="[^"]+"', f'K_GENPATCHES_VER="{gp_ver}"', old_text, count=1, flags=re.M)
    new_text = re.sub(r'^CACHYOS_RELEASE="[^"]+"', f'CACHYOS_RELEASE="{new_rel}"', new_text, count=1, flags=re.M)
    new_eb = pdir / f"cachyos-sources-{new_pv}.ebuild"
    new_eb.write_text(new_text, encoding="utf-8")

    entries = parse_manifest(pdir / "Manifest")
    if not keep_old_kernel_for_zfs(pkg, old_eb, new_pv, new_text, zfs_max):
        old_gp_m = re.search(r'^K_GENPATCHES_VER="([^"]+)"', old_text, re.M)
        old_gp = old_gp_m.group(1) if old_gp_m else None
        old_eb.unlink()
        for k in list(entries):
            if k.startswith(f"cachyos-sources-{old_ver}"):
                entries.pop(k, None)
        if old_gp and old_gp != gp_ver:
            entries.pop(f"genpatches-{new_series}-{old_gp}.base.tar.xz", None)
            entries.pop(f"genpatches-{new_series}-{old_gp}.extras.tar.xz", None)

    for name, path in ((src_dist, src_dl), (gp_base, gp_base_dl), (gp_extras, gp_extras_dl)):
        entries[name] = dist_manifest_line(name, path)
    for dname, path in patch_dls.items():
        entries[dname] = dist_manifest_line(dname, path)
    write_manifest(pdir / "Manifest", entries)
