"""Generic single-slot strategies: pypi, github-release (template-driven), cider."""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
from pathlib import Path
from typing import Callable

from ..common import (
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    http_get_text,
    list_release_ebuilds,
    parse_manifest,
    run,
    write_manifest,
)
from .base import BumpContext


def _replace_single_ebuild(
    ctx: BumpContext,
    dist_map: list[tuple[str, str]],
    *,
    verify_fn: Callable[[list[Path], Path], None] | None = None,
    preserve_dists: tuple[str, ...] = (),
) -> None:
    pdir = ctx.pkg_dir
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    if not ebuilds:
        raise BumpError(f"No existing release ebuild found for {ctx.spec.pkg}")
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == ctx.target_ver:
        raise BumpSkip(f"{ctx.spec.pkg} is already at {ctx.target_ver}")

    new_eb = pdir / f"{ctx.spec.pn}-{ctx.target_ver}.ebuild"
    shutil.copy2(old_eb, new_eb)

    entries: dict[str, str] = {}
    if preserve_dists:
        for existing_name, existing_line in parse_manifest(pdir / "Manifest").items():
            if any(fnmatch.fnmatchcase(existing_name, pat) for pat in preserve_dists):
                entries[existing_name] = existing_line

    downloaded: list[Path] = []
    for distname, url in dist_map:
        dl = download_file(url, ctx.work / distname)
        downloaded.append(dl)
        entries[distname] = dist_manifest_line(distname, dl)

    if verify_fn:
        verify_fn(downloaded, new_eb)

    for _, eb in ebuilds:
        if eb != new_eb:
            eb.unlink()
    write_manifest(pdir / "Manifest", entries)


def bump_pypi(ctx: BumpContext) -> None:
    pypi_pkg = ctx.spec.detect.get("pypi") or ctx.spec.pn
    pypi_norm = pypi_pkg.replace("-", "_")
    meta = json.loads(http_get_text(f"https://pypi.org/pypi/{pypi_pkg}/{ctx.target_ver}/json"))
    sdist = next((u for u in meta.get("urls", []) if u.get("packagetype") == "sdist"), None)
    if not sdist:
        raise BumpError(f"No sdist found on PyPI for {pypi_pkg}=={ctx.target_ver}")
    distname = sdist["filename"]
    if not distname.startswith((f"{pypi_pkg}-", f"{pypi_norm}-")):
        raise BumpError(f"Unexpected PyPI sdist filename: {distname}")
    _replace_single_ebuild(ctx, [(distname, sdist["url"])])


def bump_github_release(ctx: BumpContext) -> None:
    opts = ctx.spec.strategy_opts
    url_template = str(opts.get("url_template", ""))
    dist_template = str(opts.get("dist_template", ""))
    if not url_template or not dist_template:
        raise BumpError(
            f"{ctx.spec.pkg}: github-release strategy requires strategy.url_template and strategy.dist_template"
        )
    tag_ver = ctx.target_ver
    strip_prefix = str(opts.get("tag_ver_strip_prefix", ""))
    if strip_prefix and tag_ver.startswith(strip_prefix):
        tag_ver = tag_ver[len(strip_prefix) :]
    fmt = {"ver": ctx.target_ver, "tag_ver": tag_ver, "pn": ctx.spec.pn}
    dist_map: list[tuple[str, str]] = [
        (dist_template.format(**fmt), url_template.format(**fmt))
    ]
    for extra in opts.get("extra_dists", ()):
        dist_map.append((str(extra["dist_template"]).format(**fmt), str(extra["url_template"]).format(**fmt)))
    preserve_dists = tuple(str(p) for p in opts.get("preserve_dists", ()))
    _replace_single_ebuild(ctx, dist_map, preserve_dists=preserve_dists)


def bump_cider(ctx: BumpContext) -> None:
    distname = f"cider-v{ctx.target_ver}-linux-x64.deb"
    url = f"https://repo.cider.sh/apt/pool/main/{distname}"

    def verify_cider(files: list[Path], _eb: Path) -> None:
        deb = files[0]
        ext = ctx.work / "cider_check"
        ext.mkdir(parents=True, exist_ok=True)
        run(["ar", "x", str(deb)], cwd=ext)
        data_tar = next(ext.glob("data.tar*"), None)
        if not data_tar:
            raise BumpError(f"{deb.name} missing data.tar*")
        listing = run(["tar", "-tf", str(data_tar)]).stdout
        for required in ("usr/lib/cider/Cider", "usr/share/applications/cider.desktop"):
            if required not in listing:
                raise BumpError(f"{deb.name} missing expected path: {required}")

    _replace_single_ebuild(ctx, [(distname, url)], verify_fn=verify_cider)
