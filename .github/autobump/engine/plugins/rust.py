"""Generic Rust bump strategies (`rust-vendor` tarball & `rust-crates` via pycargoebuild)."""

from __future__ import annotations

import re
import shutil

from ..common import (
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    list_release_ebuilds,
    run,
    write_manifest,
)
from ..deps import extract_single_src_root
from ..retention import prune_keep_latest
from .base import BumpContext
from .vendor import bump_vendor_package, resolve_upstream_src


def bump_rust_vendor(ctx: BumpContext) -> None:
    """Bump a Rust package by building/reusing ${P}-deps.tar.xz in distfiles_repo."""
    bump_vendor_package(ctx, kind="rust")


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

    src_url, src_dist = resolve_upstream_src(ctx)
    src_dl = download_file(src_url, ctx.work / src_dist)
    src_root = extract_single_src_root(src_dl, ctx.work / "rust_src", expected_name=f"{spec.pn}-{new_ver}")
    cargo_manifest_dir = str(spec.strategy_opts.get("manifest_dir", ""))
    manifest_root = (src_root / cargo_manifest_dir) if cargo_manifest_dir else src_root

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
