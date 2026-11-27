"""Multi-slot Proton strategies (proton-ge-bin, dwproton-bin)."""

from __future__ import annotations

import re
import shutil

from ..common import (
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    list_release_ebuilds,
    parse_manifest,
    run,
    write_manifest,
)
from ..retention import prune_keep_latest
from .base import BumpContext


def bump_proton_ge(ctx: BumpContext) -> None:
    pdir = ctx.pkg_dir
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    if any(v == ctx.target_ver for v, _ in ebuilds):
        raise BumpSkip(f"{ctx.spec.pkg} already has {ctx.target_ver}")
    _, latest_eb = ebuilds[-1]

    my_pv = ctx.target_ver.replace(".", "-")
    distname = f"GE-Proton{my_pv}-x86_64.tar.gz"
    url = f"https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton{my_pv}/{distname}"
    dl = download_file(url, ctx.work / distname)

    expected_vdf = f"GE-Proton{my_pv}-x86_64/compatibilitytool.vdf"
    res = run(["tar", "-xOf", str(dl), expected_vdf])
    if '"install_path" "."' not in res.stdout:
        raise BumpError(f"{distname}: unexpected compatibilitytool.vdf content")

    shutil.copy2(latest_eb, pdir / f"{ctx.spec.pn}-{ctx.target_ver}.ebuild")
    entries = parse_manifest(pdir / "Manifest")
    entries[distname] = dist_manifest_line(distname, dl)

    prune_keep_latest(
        ctx.repo_root,
        ctx.spec.pkg,
        ctx.spec.retention.count,
        entries,
        lambda v: [
            f"GE-Proton{v.replace('.', '-')}.tar.gz",
            f"GE-Proton{v.replace('.', '-')}-x86_64.tar.gz",
        ],
    )
    write_manifest(pdir / "Manifest", entries)


def bump_dwproton(ctx: BumpContext) -> None:
    pdir = ctx.pkg_dir
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    if any(v == ctx.target_ver for v, _ in ebuilds):
        raise BumpSkip(f"{ctx.spec.pkg} already has {ctx.target_ver}")
    _, latest_eb = ebuilds[-1]
    eb_text = latest_eb.read_text(encoding="utf-8")

    my_pv = ctx.target_ver.replace("_p", "-")
    distname = f"dwproton-{my_pv}-x86_64.tar.xz"
    url = f"https://dawn.wine/dawn-winery/dwproton/releases/download/dwproton-{my_pv}/{distname}"
    dl = download_file(url, ctx.work / distname)

    listing_lines = set(run(["tar", "-tf", str(dl)]).stdout.splitlines())
    prefix = f"dwproton-{my_pv}-x86_64/"
    if f"{prefix}compatibilitytool.vdf" not in listing_lines:
        raise BumpError(f"{distname} missing {prefix}compatibilitytool.vdf")

    expected_relpaths = re.findall(
        r"(files/lib/i386-linux-gnu/lib[^\s\\]+|protonfixes/files/bin/p(?:grep|kill))",
        eb_text,
    )
    missing = [p for p in expected_relpaths if f"{prefix}{p}" not in listing_lines]
    if missing:
        found_libs = sorted(
            line[len(prefix) :]
            for line in listing_lines
            if line.startswith(f"{prefix}files/lib/i386-linux-gnu/lib")
            and ("webp" in line or "sharpyuv" in line)
        )
        raise BumpError(
            f"{distname}: patchelf targets changed!\n"
            f"Missing: {missing}\n"
            f"Present webp/sharpyuv libs: {found_libs}"
        )

    shutil.copy2(latest_eb, pdir / f"{ctx.spec.pn}-{ctx.target_ver}.ebuild")
    entries = parse_manifest(pdir / "Manifest")
    entries[distname] = dist_manifest_line(distname, dl)

    prune_keep_latest(
        ctx.repo_root,
        ctx.spec.pkg,
        ctx.spec.retention.count,
        entries,
        lambda v: [f"dwproton-{v.replace('_p', '-')}-x86_64.tar.xz"],
    )
    write_manifest(pdir / "Manifest", entries)
