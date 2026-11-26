"""Handler for Go packages needing generated vendor tarballs (net-misc/tsshd)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..common import (
    GH_REPO,
    REPO_ROOT,
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    list_release_ebuilds,
    run,
    write_manifest,
)


def bump_tsshd(pkg: str, new_ver: str, work: Path, *, upload_vendor: bool) -> None:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{pkg} already at {new_ver}")

    src_dist = f"tsshd-{new_ver}.tar.gz"
    vendor_dist = f"tsshd-{new_ver}-vendor.tar.xz"
    src_url = f"https://github.com/trzsz/tsshd/archive/refs/tags/v{new_ver}.tar.gz"
    src_dl = download_file(src_url, work / src_dist)

    unpack_dir = work / "tsshd_src"
    unpack_dir.mkdir(parents=True, exist_ok=True)
    run(["tar", "-xf", str(src_dl), "-C", str(unpack_dir)])
    src_root = unpack_dir / f"tsshd-{new_ver}"
    if not src_root.is_dir():
        raise BumpError(f"Unexpected tsshd source root inside {src_dist}")

    go_mod = (src_root / "go.mod").read_text(encoding="utf-8")
    go_m = re.search(r"^go\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", go_mod, re.M)
    go_ver = go_m.group(1) if go_m else None

    # Build vendor tarball and verify Go build compiles cleanly
    run(["go", "mod", "vendor"], cwd=src_root)
    run(["go", "build", "-mod=vendor", "-buildvcs=false", "-o", os.devnull, "./cmd/tsshd"], cwd=src_root)
    vendor_tar = work / vendor_dist
    run(["tar", "-cJf", str(vendor_tar), "-C", str(unpack_dir), f"tsshd-{new_ver}/vendor"])

    tag = f"vendor-tsshd-v{new_ver}"
    if upload_vendor:
        chk = run(["gh", "release", "view", tag, "-R", GH_REPO], check=False)
        if chk.returncode != 0:
            run(
                [
                    "gh",
                    "release",
                    "create",
                    tag,
                    str(vendor_tar),
                    "-R",
                    GH_REPO,
                    "--title",
                    f"tsshd {new_ver} Go vendor tarball",
                    "--notes",
                    f"Auto-generated Go vendor tarball for net-misc/tsshd-{new_ver}.",
                ]
            )
        else:
            run(["gh", "release", "upload", tag, str(vendor_tar), "--clobber", "-R", GH_REPO])

    eb_text = old_eb.read_text(encoding="utf-8")
    eb_text = re.sub(
        r"https://github\.com/[^/]+/[^/]+/releases/download/[^/\s]+/\$\{P\}-vendor\.tar\.xz",
        f"https://github.com/{GH_REPO}/releases/download/vendor-tsshd-v${{PV}}/${{P}}-vendor.tar.xz",
        eb_text,
    )
    if go_ver:
        eb_text = re.sub(r'BDEPEND=">=dev-lang/go-[^"]+"', f'BDEPEND=">=dev-lang/go-{go_ver}:="', eb_text)

    new_eb = pdir / f"tsshd-{new_ver}.ebuild"
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
