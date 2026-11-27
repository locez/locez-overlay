"""Generic Go vendor tarball generation + compile check strategy."""

from __future__ import annotations

import os
import re

from ..common import (
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    list_release_ebuilds,
    run,
    write_manifest,
)
from .base import BumpContext


def bump_go_vendor(ctx: BumpContext) -> None:
    pdir = ctx.pkg_dir
    spec = ctx.spec
    new_ver = ctx.target_ver
    gh_repo = os.environ.get("GITHUB_REPOSITORY") or ctx.cfg.overlay.repository

    ebuilds = list_release_ebuilds(ctx.repo_root, spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{spec.pkg} already at {new_ver}")

    upstream_repo = spec.detect.get("github")
    if not upstream_repo:
        raise BumpError(f"{spec.pkg}: go-vendor strategy requires detect.github in autobump.toml")

    prefix = spec.detect.get("prefix", "v")
    src_dist = f"{spec.pn}-{new_ver}.tar.gz"
    vendor_dist = f"{spec.pn}-{new_ver}-vendor.tar.xz"
    src_url = f"https://github.com/{upstream_repo}/archive/refs/tags/{prefix}{new_ver}.tar.gz"
    src_dl = download_file(src_url, ctx.work / src_dist)

    unpack_dir = ctx.work / "go_src"
    unpack_dir.mkdir(parents=True, exist_ok=True)
    run(["tar", "-xf", str(src_dl), "-C", str(unpack_dir)])
    src_root = unpack_dir / f"{spec.pn}-{new_ver}"
    if not src_root.is_dir():
        candidates = [d for d in unpack_dir.iterdir() if d.is_dir()]
        if len(candidates) != 1:
            raise BumpError(f"Unexpected source root layout inside {src_dist}")
        src_root = candidates[0]

    go_mod = (src_root / "go.mod").read_text(encoding="utf-8")
    go_m = re.search(r"^go\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", go_mod, re.M)
    go_ver = go_m.group(1) if go_m else None

    build_target = spec.go_build_target or "."
    run(["go", "mod", "vendor"], cwd=src_root)
    run(["go", "build", "-mod=vendor", "-buildvcs=false", "-o", os.devnull, build_target], cwd=src_root)

    vendor_tar = ctx.work / vendor_dist
    run(["tar", "-cJf", str(vendor_tar), "-C", str(unpack_dir), f"{src_root.name}/vendor"])

    tag = f"vendor-{spec.pn}-v{new_ver}"
    if ctx.upload_vendor and gh_repo:
        chk = run(["gh", "release", "view", tag, "-R", gh_repo], check=False)
        if chk.returncode != 0:
            run(
                [
                    "gh",
                    "release",
                    "create",
                    tag,
                    str(vendor_tar),
                    "-R",
                    gh_repo,
                    "--title",
                    f"{spec.pn} {new_ver} Go vendor tarball",
                    "--notes",
                    f"Auto-generated Go vendor tarball for {spec.pkg}-{new_ver}.",
                ]
            )
        else:
            run(["gh", "release", "upload", tag, str(vendor_tar), "--clobber", "-R", gh_repo])

    eb_text = old_eb.read_text(encoding="utf-8")
    if gh_repo:
        eb_text = re.sub(
            r"https://github\.com/[^/]+/[^/]+/releases/download/[^/\s]+/\$\{P\}-vendor\.tar\.xz",
            f"https://github.com/{gh_repo}/releases/download/vendor-{spec.pn}-v${{PV}}/${{P}}-vendor.tar.xz",
            eb_text,
        )
    if go_ver:
        eb_text = re.sub(r'BDEPEND=">=dev-lang/go-[^"]+"', f'BDEPEND=">=dev-lang/go-{go_ver}:="', eb_text)

    new_eb = pdir / f"{spec.pn}-{new_ver}.ebuild"
    new_eb.write_text(eb_text, encoding="utf-8")
    for _, eb in ebuilds:
        if eb != new_eb:
            eb.unlink()

    write_manifest(
        pdir / "Manifest",
        {
            src_dist: dist_manifest_line(src_dist, src_dl),
            vendor_dist: dist_manifest_line(vendor_dist, vendor_tar),
        },
    )
