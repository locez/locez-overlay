"""Go offline dependency tarball builder (`inherit go-module`)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..common import BumpError, run
from .common import DepsBuildMeta, pack_deterministic_tar_xz


def build_go_deps_tarball(
    src_root: Path,
    unpack_dir: Path,
    out_tar: Path,
    *,
    build_target: str = ".",
) -> DepsBuildMeta:
    """Run `go mod vendor` + sanity build and pack `<src_root>/vendor` into `out_tar`."""
    go_mod_path = src_root / "go.mod"
    if not go_mod_path.is_file():
        raise BumpError(f"go.mod not found in {src_root}")
    go_mod = go_mod_path.read_text(encoding="utf-8")
    go_m = re.search(r"^go\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", go_mod, re.M)
    go_ver = go_m.group(1) if go_m else ""

    run(["go", "mod", "vendor"], cwd=src_root)
    run(["go", "build", "-mod=vendor", "-buildvcs=false", "-o", os.devnull, build_target or "."], cwd=src_root)

    pack_deterministic_tar_xz(unpack_dir, [f"{src_root.name}/vendor"], out_tar)
    return DepsBuildMeta(kind="go", min_toolchain_ver=go_ver, has_git_crates=False, tarball=out_tar)
