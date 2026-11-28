"""Unified Go, Rust & Zig offline dependency tarball builders & distfiles uploader.

Follows a single overlay-agnostic convention for hosting vendor/deps tarballs
in a dedicated GitHub repository (overlay.distfiles_repo, defaulting to
overlay.repository):

- Release tag:   <category>-<pn>-<ver>   (e.g. net-misc-tsshd-0.1.10)
- Asset file:    <pn>-<ver>-deps.tar.xz  (e.g. tsshd-0.1.10-deps.tar.xz)
- Ebuild URI:    https://github.com/<distfiles_repo>/releases/download/${CATEGORY}-${PN}-${PV}/${P}-deps.tar.xz

Tarball internal layouts match Gentoo eclasses natively (zero custom unpack boilerplate):
- Go (`inherit go-module`): `<src_root>/vendor/...` -> overlays straight into `${S}/vendor`.
- Rust (`inherit cargo`):   `cargo_home/gentoo/...` -> overlays straight into `${ECARGO_VENDOR}`
  (plus optional `cargo_home/git-sources.toml` when Cargo.lock contains git dependencies).
- Zig (`zig --system`):     `zig_packages/<hash>/...` -> passed directly via `--system "${WORKDIR}/zig_packages"`.
"""

from .common import (
    DEPS_PLACEHOLDER,
    DepsAction,
    DepsBuildMeta,
    DepsKind,
    DepsOptions,
    DepsResolution,
    StageOutput,
    deps_download_url,
    deps_ebuild_uri_template,
    deps_release_tag,
    deps_tarball_name,
    extract_single_src_root,
    pack_deterministic_tar_xz,
)
from .go import build_go_deps_tarball
from .orchestrator import (
    _expand_ebuild_vars,
    build_deps_for_package,
    deps_options_from_spec,
    detect_deps_kind,
    ebuild_needs_deps_resolution,
    ensure_deps_tarball,
    parse_primary_src_uri,
    release_asset_exists,
    resolve_packages_deps,
    rewrite_ebuild_deps_metadata,
    upload_deps_release,
)
from .rust import build_rust_deps_tarball, extract_rust_git_sources, stage_rust_deps
from .zig import ZigZonDep, build_zig_deps_tarball, parse_zig_zon, stage_zig_deps, vendor_zig_deps

__all__ = [
    "DEPS_PLACEHOLDER",
    "DepsAction",
    "DepsBuildMeta",
    "DepsKind",
    "DepsOptions",
    "DepsResolution",
    "StageOutput",
    "ZigZonDep",
    "_expand_ebuild_vars",
    "build_deps_for_package",
    "build_go_deps_tarball",
    "build_rust_deps_tarball",
    "build_zig_deps_tarball",
    "deps_download_url",
    "deps_ebuild_uri_template",
    "deps_options_from_spec",
    "deps_release_tag",
    "deps_tarball_name",
    "detect_deps_kind",
    "ebuild_needs_deps_resolution",
    "ensure_deps_tarball",
    "extract_rust_git_sources",
    "extract_single_src_root",
    "pack_deterministic_tar_xz",
    "parse_primary_src_uri",
    "parse_zig_zon",
    "release_asset_exists",
    "resolve_packages_deps",
    "rewrite_ebuild_deps_metadata",
    "stage_rust_deps",
    "stage_zig_deps",
    "upload_deps_release",
    "vendor_zig_deps",
]
