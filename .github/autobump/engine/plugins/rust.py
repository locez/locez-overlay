"""Generic vendor-tarball (Go/Rust) & pycargoebuild (Rust crates) bump strategies."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable

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
from ..deps import (
    DepsKind,
    deps_tarball_name,
    ensure_deps_tarball,
    extract_single_src_root,
    rewrite_ebuild_deps_metadata,
)
from ..retention import prune_keep_latest
from .base import BumpContext


def _resolve_upstream_src(ctx: BumpContext) -> tuple[str, str]:
    spec = ctx.spec
    ver = ctx.target_ver
    if spec.url_template and spec.dist_template:
        return spec.url_template.format(ver=ver), spec.dist_template.format(ver=ver)
    upstream_repo = spec.detect.get("github")
    if not upstream_repo:
        raise BumpError(f"{spec.pkg}: strategy '{spec.strategy}' requires detect.github or url_template/dist_template")
    prefix = spec.detect.get("prefix", "v")
    return (
        f"https://github.com/{upstream_repo}/archive/refs/tags/{prefix}{ver}.tar.gz",
        f"{spec.pn}-{ver}.tar.gz",
    )


def _apply_retention(
    ctx: BumpContext,
    entries: dict[str, str],
    src_dist_for_ver: Callable[[str], str],
) -> None:
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
    """Shared bump flow for Go (go-vendor) and Rust (rust-vendor) ${P}-deps.tar.xz packages."""
    pdir = ctx.pkg_dir
    spec = ctx.spec
    new_ver = ctx.target_ver
    overlay_repo = os.environ.get("GITHUB_REPOSITORY") or ctx.cfg.overlay.repository
    distfiles_repo = ctx.cfg.overlay.effective_distfiles_repo

    ebuilds = list_release_ebuilds(ctx.repo_root, spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{spec.pkg} already at {new_ver}")

    src_url, src_dist = _resolve_upstream_src(ctx)
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
        go_build_target=spec.go_build_target or ".",
        cargo_manifest_dir=spec.cargo_manifest_dir,
        zig_manifest_dir=spec.zig_manifest_dir,
        zig_deps=spec.zig_deps,
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
    _apply_retention(
        ctx,
        entries,
        lambda v: spec.dist_template.format(ver=v) if spec.dist_template else f"{spec.pn}-{v}.tar.gz",
    )
    write_manifest(manifest_path, entries)


def bump_rust_vendor(ctx: BumpContext) -> None:
    """Bump a Rust package by building/reusing ${P}-deps.tar.xz in distfiles_repo."""
    bump_vendor_package(ctx, kind="rust")


def bump_zig_vendor(ctx: BumpContext) -> None:
    """Bump a Zig package by building/reusing ${P}-deps.tar.xz in distfiles_repo."""
    bump_vendor_package(ctx, kind="zig")


def parse_crates_from_ebuild(eb_text: str) -> list[tuple[str, str]]:
    """Parse modern `name@ver` entries from an ebuild's CRATES="..." block."""
    m = re.search(r'CRATES="([^"]*)"', eb_text, re.S)
    if not m:
        return []
    crates: list[tuple[str, str]] = []
    for token in m.group(1).split():
        if "@" in token:
            name, ver = token.split("@", 1)
            crates.append((name, ver))
    return crates


def bump_rust_crates(ctx: BumpContext) -> None:
    """Bump a Rust package using pycargoebuild --inplace (CRATES / GIT_CRATES / LICENSE)."""
    pdir = ctx.pkg_dir
    spec = ctx.spec
    new_ver = ctx.target_ver

    if not shutil.which("pycargoebuild"):
        raise BumpError("pycargoebuild is not installed in PATH (required for rust-crates strategy)")

    ebuilds = list_release_ebuilds(ctx.repo_root, spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{spec.pkg} already at {new_ver}")

    src_url, src_dist = _resolve_upstream_src(ctx)
    src_dl = download_file(src_url, ctx.work / src_dist)
    src_root = extract_single_src_root(src_dl, ctx.work / "rust_src", expected_name=f"{spec.pn}-{new_ver}")
    manifest_root = (src_root / spec.cargo_manifest_dir) if spec.cargo_manifest_dir else src_root

    new_eb = pdir / f"{spec.pn}-{new_ver}.ebuild"
    shutil.copy2(old_eb, new_eb)
    run(["pycargoebuild", "-i", str(new_eb), str(manifest_root)])

    keep_count = spec.retention.count if spec.retention.type == "keep-latest" else 1
    prune_keep_latest(ctx.repo_root, spec.pkg, keep_count, {}, lambda _v: [])

    if shutil.which("pkgdev"):
        run(["pkgdev", "manifest", spec.pkg], cwd=ctx.repo_root)
        return
    if shutil.which("ebuild"):
        run(["ebuild", str(new_eb), "manifest"], cwd=ctx.repo_root)
        return

    # Pure-Python fallback for non-Gentoo CI hosts (e.g. ubuntu-latest): hash crates.io entries directly.
    eb_text = new_eb.read_text(encoding="utf-8")
    if "GIT_CRATES=" in eb_text:
        raise BumpError(
            f"{spec.pkg}: GIT_CRATES requires pkgdev/ebuild to regenerate Manifest (or use strategy = 'rust-vendor')"
        )
    entries: dict[str, str] = {src_dist: dist_manifest_line(src_dist, src_dl)}
    crates_dir = ctx.work / "crates"
    crates_dir.mkdir(parents=True, exist_ok=True)
    for name, cver in parse_crates_from_ebuild(eb_text):
        fname = f"{name}-{cver}.crate"
        crate_url = f"https://crates.io/api/v1/crates/{name}/{cver}/download"
        dl = download_file(crate_url, crates_dir / fname)
        entries[fname] = dist_manifest_line(fname, dl)
    write_manifest(pdir / "Manifest", entries)
