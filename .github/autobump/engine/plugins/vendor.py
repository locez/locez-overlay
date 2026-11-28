"""Shared ecosystem-agnostic vendor-tarball bump flow for Go, Rust, and Zig strategies."""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from ..common import (
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    list_release_ebuilds,
    parse_manifest,
    write_manifest,
)
from ..deps import (
    DepsKind,
    deps_options_from_spec,
    deps_tarball_name,
    ensure_deps_tarball,
    rewrite_ebuild_deps_metadata,
)
from ..retention import prune_keep_latest
from .base import BumpContext


def resolve_upstream_src(ctx: BumpContext) -> tuple[str, str]:
    """Resolve primary upstream source URL and distfile name from strategy options or GitHub tag."""
    spec = ctx.spec
    ver = ctx.target_ver
    url_tpl = str(spec.strategy_opts.get("url_template", ""))
    dist_tpl = str(spec.strategy_opts.get("dist_template", ""))
    if url_tpl and dist_tpl:
        return url_tpl.format(ver=ver), dist_tpl.format(ver=ver)
    upstream_repo = spec.detect.get("github")
    if not upstream_repo:
        raise BumpError(f"{spec.pkg}: strategy '{spec.strategy}' requires detect.github or strategy url/dist templates")
    prefix = spec.detect.get("prefix", "v")
    return (
        f"https://github.com/{upstream_repo}/archive/refs/tags/{prefix}{ver}.tar.gz",
        f"{spec.pn}-{ver}.tar.gz",
    )


def apply_vendor_retention(
    ctx: BumpContext,
    entries: dict[str, str],
    src_dist_for_ver: Callable[[str], str],
) -> None:
    """Prune superseded ebuilds and vendor distfile Manifest entries according to spec.retention."""
    spec = ctx.spec
    keep_count = spec.retention.count if spec.retention.type == "keep-latest" else 1
    prune_keep_latest(
        ctx.repo_root,
        spec.pkg,
        keep_count,
        entries,
        lambda v: [
            src_dist_for_ver(v),
            deps_tarball_name(spec.pn, v),
            f"{spec.pn}-{v}-vendor.tar.xz",
            f"{spec.pn}-{v}-crates.tar.xz",
        ],
    )


def bump_vendor_package(ctx: BumpContext, *, kind: DepsKind) -> None:
    """Shared bump flow for Go (go-vendor), Rust (rust-vendor), and Zig (zig-vendor) packages."""
    pdir = ctx.pkg_dir
    spec = ctx.spec
    new_ver = ctx.target_ver
    overlay_repo = os.environ.get("GITHUB_REPOSITORY") or ctx.cfg.overlay.repository
    distfiles_repo = ctx.cfg.overlay.effective_distfiles_repo

    ebuilds = list_release_ebuilds(ctx.repo_root, spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{spec.pkg} already at {new_ver}")

    src_url, src_dist = resolve_upstream_src(ctx)
    deps_dist = deps_tarball_name(spec.pn, new_ver)
    src_dl = download_file(src_url, ctx.work / src_dist)
    deps_tar = ctx.work / deps_dist

    res = ensure_deps_tarball(
        pkg=spec.pkg,
        ver=new_ver,
        kind=kind,
        src_tar=src_dl,
        out_tar=deps_tar,
        distfiles_repo=distfiles_repo,
        overlay_repo=overlay_repo,
        upload=ctx.upload_vendor,
        options=deps_options_from_spec(spec),
    )

    eb_text = rewrite_ebuild_deps_metadata(
        old_eb.read_text(encoding="utf-8"),
        kind=kind,
        distfiles_repo=distfiles_repo,
        min_toolchain_ver=res.min_toolchain_ver,
    )
    new_eb = pdir / f"{spec.pn}-{new_ver}.ebuild"
    new_eb.write_text(eb_text, encoding="utf-8")

    manifest_path = pdir / "Manifest"
    entries = parse_manifest(manifest_path) if spec.retention.type == "keep-latest" else {}
    entries[src_dist] = dist_manifest_line(src_dist, src_dl)
    entries[deps_dist] = dist_manifest_line(deps_dist, deps_tar)
    dist_tpl = str(spec.strategy_opts.get("dist_template", ""))
    apply_vendor_retention(
        ctx,
        entries,
        lambda v: dist_tpl.format(ver=v) if dist_tpl else f"{spec.pn}-{v}.tar.gz",
    )
    write_manifest(manifest_path, entries)
