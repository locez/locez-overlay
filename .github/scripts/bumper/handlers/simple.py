"""Handlers for single-slot PyPI, GitHub release, and Cider packages."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable

from ..common import (
    REPO_ROOT,
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    http_get_text,
    list_release_ebuilds,
    run,
    write_manifest,
)


def _replace_single_ebuild(
    pkg: str,
    new_ver: str,
    dist_map: list[tuple[str, str]],
    work: Path,
    *,
    verify_fn: Callable[[list[Path], Path], None] | None = None,
) -> None:
    pdir = REPO_ROOT / pkg
    pn = pkg.split("/", 1)[1]
    ebuilds = list_release_ebuilds(pkg)
    if not ebuilds:
        raise BumpError(f"No existing release ebuild found for {pkg}")
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{pkg} is already at {new_ver}")

    new_eb = pdir / f"{pn}-{new_ver}.ebuild"
    shutil.copy2(old_eb, new_eb)

    entries: dict[str, str] = {}
    downloaded: list[Path] = []
    for distname, url in dist_map:
        dl = download_file(url, work / distname)
        downloaded.append(dl)
        entries[distname] = dist_manifest_line(distname, dl)

    if verify_fn:
        verify_fn(downloaded, new_eb)

    for _, eb in ebuilds:
        if eb != new_eb:
            eb.unlink()
    write_manifest(pdir / "Manifest", entries)


def bump_pypi(pkg: str, new_ver: str, work: Path) -> None:
    pn = pkg.split("/", 1)[1]
    pypi_name = pn.replace("-", "_")
    meta = json.loads(http_get_text(f"https://pypi.org/pypi/{pn}/{new_ver}/json"))
    sdist = next((u for u in meta.get("urls", []) if u.get("packagetype") == "sdist"), None)
    if not sdist:
        raise BumpError(f"No sdist found on PyPI for {pn}=={new_ver}")
    distname = sdist["filename"]
    if not distname.startswith((f"{pn}-", f"{pypi_name}-")):
        raise BumpError(f"Unexpected PyPI sdist filename: {distname}")
    _replace_single_ebuild(pkg, new_ver, [(distname, sdist["url"])], work)


def bump_simple_github(pkg: str, new_ver: str, work: Path) -> None:
    configs: dict[str, tuple[str, str]] = {
        "media-libs/libdicom": (
            f"libdicom-{new_ver}.tar.xz",
            f"https://github.com/ImagingDataCommons/libdicom/releases/download/v{new_ver}/libdicom-{new_ver}.tar.xz",
        ),
        "media-libs/openslide": (
            f"openslide-{new_ver}.tar.xz",
            f"https://github.com/openslide/openslide/releases/download/v{new_ver}/openslide-{new_ver}.tar.xz",
        ),
        "media-plugins/kotonoha": (
            f"kotonoha-{new_ver}.tar.gz",
            f"https://github.com/locez/kotonoha/archive/refs/tags/v{new_ver}.tar.gz",
        ),
        "x11-misc/flatpak-xdg-utils": (
            f"flatpak-xdg-utils-{new_ver}.tar.xz",
            f"https://github.com/flatpak/flatpak-xdg-utils/releases/download/{new_ver}/flatpak-xdg-utils-{new_ver}.tar.xz",
        ),
    }
    distname, url = configs[pkg]
    _replace_single_ebuild(pkg, new_ver, [(distname, url)], work)


def bump_cider(pkg: str, new_ver: str, work: Path) -> None:
    distname = f"cider-v{new_ver}-linux-x64.deb"
    url = f"https://repo.cider.sh/apt/pool/main/{distname}"

    def verify_cider(files: list[Path], _eb: Path) -> None:
        deb = files[0]
        ext = work / "cider_check"
        ext.mkdir(parents=True, exist_ok=True)
        run(["ar", "x", str(deb)], cwd=ext)
        data_tar = next(ext.glob("data.tar*"), None)
        if not data_tar:
            raise BumpError(f"{deb.name} missing data.tar*")
        listing = run(["tar", "-tf", str(data_tar)]).stdout
        for required in ("usr/lib/cider/Cider", "usr/share/applications/cider.desktop"):
            if required not in listing:
                raise BumpError(f"{deb.name} missing expected path: {required}")

    _replace_single_ebuild(pkg, new_ver, [(distname, url)], work, verify_fn=verify_cider)
