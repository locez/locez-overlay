"""Rust offline dependency stager & tarball builder (`inherit cargo`)."""

from __future__ import annotations

import re
import tempfile
import tomllib
from pathlib import Path

from ..common import BumpError, run
from .common import DepsBuildMeta, StageOutput, pack_deterministic_tar_xz


def extract_rust_git_sources(vendor_stdout: str) -> str:
    """Extract non-crates.io [source.*] entries from `cargo vendor` output for cargo.eclass."""
    blocks: list[str] = []
    cur_header = ""
    cur_lines: list[str] = []

    def _flush() -> None:
        if not cur_header or cur_header in ("[source.crates-io]", "[source.vendored-sources]"):
            return
        rewritten = [
            re.sub(r'replace-with\s*=\s*"vendored-sources"', 'replace-with = "gentoo"', ln)
            for ln in cur_lines
        ]
        blocks.append("\n".join(rewritten).strip())

    for raw in vendor_stdout.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            _flush()
            cur_header = line
            cur_lines = [line]
        elif cur_header and line:
            cur_lines.append(raw.rstrip())
    _flush()
    return ("\n\n".join(b for b in blocks if b) + "\n") if blocks else ""


def _read_cargo_rust_version(cargo_toml: Path) -> str:
    if not cargo_toml.is_file():
        return ""
    try:
        data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    for section in ("package", "workspace"):
        sec = data.get(section)
        if isinstance(sec, dict):
            if isinstance(sec.get("rust-version"), str):
                return sec["rust-version"]
            pkg = sec.get("package")
            if isinstance(pkg, dict) and isinstance(pkg.get("rust-version"), str):
                return pkg["rust-version"]
    return ""


def stage_rust_deps(
    src_root: Path,
    stage_dir: Path,
    *,
    manifest_dir: str = "",
) -> StageOutput:
    """Vendor Rust crates into `stage_dir/cargo_home/gentoo` (plus optional `git-sources.toml`)."""
    manifest_root = (src_root / manifest_dir) if manifest_dir else src_root
    cargo_toml = manifest_root / "Cargo.toml"
    if not cargo_toml.is_file():
        raise BumpError(f"Cargo.toml not found in {manifest_root}")

    rust_min_ver = _read_cargo_rust_version(cargo_toml)
    vendor_dir = stage_dir / "cargo_home" / "gentoo"
    vendor_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["cargo", "vendor", "--versioned-dirs", str(vendor_dir)]
    if (manifest_root / "Cargo.lock").is_file():
        cmd.insert(2, "--locked")
    res = run(cmd, cwd=manifest_root)

    git_sources = extract_rust_git_sources(res.stdout)
    if git_sources:
        (stage_dir / "cargo_home" / "git-sources.toml").write_text(git_sources, encoding="utf-8")

    return StageOutput(
        members=("cargo_home",),
        min_toolchain_ver=rust_min_ver,
        has_git_crates=bool(git_sources),
    )


def build_rust_deps_tarball(
    src_root: Path,
    out_tar: Path,
    *,
    manifest_dir: str = "",
) -> DepsBuildMeta:
    """Build a standalone Rust `${P}-deps.tar.xz` containing `cargo_home/`."""
    with tempfile.TemporaryDirectory(prefix="cargo-vendor-stage-") as tmp:
        stage = Path(tmp)
        staged = stage_rust_deps(src_root, stage, manifest_dir=manifest_dir)
        pack_deterministic_tar_xz(stage, staged.members, out_tar)
        return DepsBuildMeta(
            kind="rust",
            min_toolchain_ver=staged.min_toolchain_ver,
            has_git_crates=staged.has_git_crates,
            tarball=out_tar,
        )
