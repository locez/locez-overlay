"""Retention policies & ZFS fallback guard (overlay-agnostic)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .common import list_release_ebuilds, version_key


def ebuild_forbids_zfs(eb_text: str) -> bool:
    m = re.search(r'REQUIRED_USE="([^"]*)"', eb_text, re.S)
    return bool(m and re.search(r"(^|\s)!zfs(\s|$)", m.group(1)))


def ebuild_supports_zfs(ver: str, eb_text: str, zfs_max_series: str) -> bool:
    if ebuild_forbids_zfs(eb_text):
        return False
    series = ".".join(ver.split(".")[:2])
    return version_key(series) <= version_key(zfs_max_series)


def must_keep_for_zfs(
    repo_root: Path,
    pkg: str,
    old_eb: Path,
    new_ver: str,
    new_eb_text: str,
    zfs_max_series: str,
) -> bool:
    """Return True if dropping old_eb would leave zero ZFS-compatible kernels <= zfs_max_series."""
    if ebuild_supports_zfs(new_ver, new_eb_text, zfs_max_series):
        return False
    old_resolved = old_eb.resolve()
    for ver, eb in list_release_ebuilds(repo_root, pkg):
        if eb.resolve() == old_resolved or ver == new_ver:
            continue
        if ebuild_supports_zfs(ver, eb.read_text(encoding="utf-8"), zfs_max_series):
            return False
    return True


def prune_keep_latest(
    repo_root: Path,
    pkg: str,
    keep_count: int,
    entries: dict[str, str],
    dist_candidates_fn: Callable[[str], list[str]],
) -> None:
    """Keep the newest `keep_count` release ebuilds and prune older ebuilds + Manifest entries."""
    all_ebuilds = list_release_ebuilds(repo_root, pkg)
    while len(all_ebuilds) > keep_count:
        old_ver, old_eb = all_ebuilds.pop(0)
        old_eb.unlink()
        for distname in dist_candidates_fn(old_ver):
            entries.pop(distname, None)
