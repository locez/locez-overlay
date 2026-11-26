"""Handlers for multi-slot Proton packages (proton-ge-bin, dwproton-bin)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..common import (
    REPO_ROOT,
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    list_release_ebuilds,
    parse_manifest,
    run,
    write_manifest,
)

PROTON_KEEP_SLOTS = 3


def bump_proton_ge(pkg: str, new_ver: str, work: Path) -> None:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    if any(v == new_ver for v, _ in ebuilds):
        raise BumpSkip(f"{pkg} already has {new_ver}")
    _, latest_eb = ebuilds[-1]

    my_pv = new_ver.replace(".", "-")
    distname = f"GE-Proton{my_pv}-x86_64.tar.gz"
    url = f"https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton{my_pv}/{distname}"
    dl = download_file(url, work / distname)

    expected_vdf = f"GE-Proton{my_pv}-x86_64/compatibilitytool.vdf"
    res = run(["tar", "-xOf", str(dl), expected_vdf])
    if '"install_path" "."' not in res.stdout:
        raise BumpError(f"{distname}: unexpected compatibilitytool.vdf content")

    new_eb = pdir / f"proton-ge-bin-{new_ver}.ebuild"
    shutil.copy2(latest_eb, new_eb)

    entries = parse_manifest(pdir / "Manifest")
    entries[distname] = dist_manifest_line(distname, dl)

    all_ebuilds = list_release_ebuilds(pkg)
    while len(all_ebuilds) > PROTON_KEEP_SLOTS:
        old_ver, old_eb = all_ebuilds.pop(0)
        old_my_pv = old_ver.replace(".", "-")
        old_eb.unlink()
        for candidate in (f"GE-Proton{old_my_pv}.tar.gz", f"GE-Proton{old_my_pv}-x86_64.tar.gz"):
            entries.pop(candidate, None)

    write_manifest(pdir / "Manifest", entries)


def bump_dwproton(pkg: str, new_ver: str, work: Path) -> None:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    if any(v == new_ver for v, _ in ebuilds):
        raise BumpSkip(f"{pkg} already has {new_ver}")
    _, latest_eb = ebuilds[-1]
    eb_text = latest_eb.read_text(encoding="utf-8")

    my_pv = new_ver.replace("_p", "-")
    distname = f"dwproton-{my_pv}-x86_64.tar.xz"
    url = f"https://dawn.wine/dawn-winery/dwproton/releases/download/dwproton-{my_pv}/{distname}"
    dl = download_file(url, work / distname)

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

    new_eb = pdir / f"dwproton-bin-{new_ver}.ebuild"
    shutil.copy2(latest_eb, new_eb)

    entries = parse_manifest(pdir / "Manifest")
    entries[distname] = dist_manifest_line(distname, dl)

    all_ebuilds = list_release_ebuilds(pkg)
    while len(all_ebuilds) > PROTON_KEEP_SLOTS:
        old_ver, old_eb = all_ebuilds.pop(0)
        old_my_pv = old_ver.replace("_p", "-")
        old_eb.unlink()
        entries.pop(f"dwproton-{old_my_pv}-x86_64.tar.xz", None)

    write_manifest(pdir / "Manifest", entries)
