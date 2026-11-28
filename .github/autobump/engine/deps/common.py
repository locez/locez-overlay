"""Shared data types, deterministic tar helper, and naming conventions for offline deps tarballs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..common import BumpError, run

DepsKind = Literal["go", "rust", "zig"]
DepsAction = Literal["built_and_uploaded", "reused_existing", "built_local_only"]

DEPS_PLACEHOLDER = "__DEPS_TARBALL_URI__"

DETERMINISTIC_TAR_FLAGS = (
    "--sort=name",
    "--mtime=UTC 1970-01-01",
    "--owner=0",
    "--group=0",
    "--numeric-owner",
)


@dataclass(frozen=True)
class DepsOptions:
    """Per-ecosystem build options passed to offline deps builders."""

    go_build_target: str = "."
    cargo_manifest_dir: str = ""
    zig_manifest_dir: str = ""
    zig_deps: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageOutput:
    """Result of staging a single ecosystem's offline deps inside a temporary tar root."""

    members: tuple[str, ...]
    min_toolchain_ver: str = ""
    has_git_crates: bool = False


@dataclass(frozen=True)
class DepsBuildMeta:
    kind: DepsKind
    min_toolchain_ver: str
    has_git_crates: bool
    tarball: Path


@dataclass(frozen=True)
class DepsResolution:
    pkg: str
    ver: str
    kind: DepsKind
    tag: str
    action: DepsAction
    download_url: str
    min_toolchain_ver: str
    has_git_crates: bool
    tarball: Path


def deps_release_tag(pkg: str, ver: str) -> str:
    cat, pn = pkg.split("/", 1)
    return f"{cat}-{pn}-{ver}"


def deps_tarball_name(pn: str, ver: str) -> str:
    return f"{pn}-{ver}-deps.tar.xz"


def deps_download_url(distfiles_repo: str, pkg: str, ver: str) -> str:
    pn = pkg.split("/", 1)[1]
    return (
        f"https://github.com/{distfiles_repo}/releases/download/"
        f"{deps_release_tag(pkg, ver)}/{deps_tarball_name(pn, ver)}"
    )


def deps_ebuild_uri_template(distfiles_repo: str) -> str:
    return (
        f"https://github.com/{distfiles_repo}/releases/download/"
        "${CATEGORY}-${PN}-${PV}/${P}-deps.tar.xz"
    )


def extract_single_src_root(src_tar: Path, unpack_dir: Path, expected_name: str = "") -> Path:
    unpack_dir.mkdir(parents=True, exist_ok=True)
    run(["tar", "-xf", str(src_tar), "-C", str(unpack_dir)])
    if expected_name and (unpack_dir / expected_name).is_dir():
        return unpack_dir / expected_name
    candidates = [d for d in unpack_dir.iterdir() if d.is_dir()]
    if len(candidates) != 1:
        raise BumpError(f"Unexpected source root layout inside {src_tar.name}: {[c.name for c in candidates]}")
    return candidates[0]


def pack_deterministic_tar_xz(stage_dir: Path, members: list[str] | tuple[str, ...], out_tar: Path) -> Path:
    """Create a reproducible .tar.xz archive containing `members` rooted at `stage_dir`."""
    if not members:
        raise BumpError("Cannot pack an empty deps tarball")
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    run(["tar", *DETERMINISTIC_TAR_FLAGS, "-cJf", str(out_tar), "-C", str(stage_dir), *members])
    return out_tar
