"""Unified Go & Rust offline dependency tarball builder & distfiles uploader.

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
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .common import (
    BumpError,
    dist_manifest_line,
    download_file,
    list_release_ebuilds,
    parse_manifest,
    run,
    write_manifest,
)
from .schema import AutomationConfig

DepsKind = Literal["go", "rust"]
DepsAction = Literal["built_and_uploaded", "reused_existing", "built_local_only"]

DEPS_PLACEHOLDER = "__DEPS_TARBALL_URI__"

_DETERMINISTIC_TAR_FLAGS = (
    "--sort=name",
    "--mtime=UTC 1970-01-01",
    "--owner=0",
    "--group=0",
    "--numeric-owner",
)
_EBUILD_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


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


def build_go_deps_tarball(
    src_root: Path,
    unpack_dir: Path,
    out_tar: Path,
    *,
    build_target: str = ".",
) -> DepsBuildMeta:
    go_mod_path = src_root / "go.mod"
    if not go_mod_path.is_file():
        raise BumpError(f"go.mod not found in {src_root}")
    go_mod = go_mod_path.read_text(encoding="utf-8")
    go_m = re.search(r"^go\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", go_mod, re.M)
    go_ver = go_m.group(1) if go_m else ""

    run(["go", "mod", "vendor"], cwd=src_root)
    run(["go", "build", "-mod=vendor", "-buildvcs=false", "-o", os.devnull, build_target or "."], cwd=src_root)

    out_tar.parent.mkdir(parents=True, exist_ok=True)
    run(["tar", *_DETERMINISTIC_TAR_FLAGS, "-cJf", str(out_tar), "-C", str(unpack_dir), f"{src_root.name}/vendor"])
    return DepsBuildMeta(kind="go", min_toolchain_ver=go_ver, has_git_crates=False, tarball=out_tar)


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


def build_rust_deps_tarball(
    src_root: Path,
    out_tar: Path,
    *,
    manifest_dir: str = "",
) -> DepsBuildMeta:
    manifest_root = (src_root / manifest_dir) if manifest_dir else src_root
    cargo_toml = manifest_root / "Cargo.toml"
    if not cargo_toml.is_file():
        raise BumpError(f"Cargo.toml not found in {manifest_root}")

    rust_min_ver = _read_cargo_rust_version(cargo_toml)

    with tempfile.TemporaryDirectory(prefix="cargo-vendor-stage-") as tmp:
        stage = Path(tmp)
        vendor_dir = stage / "cargo_home" / "gentoo"
        vendor_dir.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["cargo", "vendor", "--versioned-dirs", str(vendor_dir)]
        if (manifest_root / "Cargo.lock").is_file():
            cmd.insert(2, "--locked")
        res = run(cmd, cwd=manifest_root)

        git_sources = extract_rust_git_sources(res.stdout)
        if git_sources:
            (stage / "cargo_home" / "git-sources.toml").write_text(git_sources, encoding="utf-8")

        out_tar.parent.mkdir(parents=True, exist_ok=True)
        run(["tar", *_DETERMINISTIC_TAR_FLAGS, "-cJf", str(out_tar), "-C", str(stage), "cargo_home"])
        return DepsBuildMeta(
            kind="rust",
            min_toolchain_ver=rust_min_ver,
            has_git_crates=bool(git_sources),
            tarball=out_tar,
        )


def _gh_env_for_distfiles(distfiles_repo: str, overlay_repo: str) -> dict[str, str]:
    env = dict(os.environ)
    distfiles_token = os.environ.get("DISTFILES_TOKEN", "").strip()
    if distfiles_token:
        env["GH_TOKEN"] = distfiles_token
        env["GITHUB_TOKEN"] = distfiles_token
    elif os.environ.get("GITHUB_ACTIONS") == "true" and distfiles_repo != overlay_repo:
        raise BumpError(
            f"DISTFILES_TOKEN secret is required in CI to access '{distfiles_repo}' "
            f"(different from '{overlay_repo}')"
        )
    return env


def release_asset_exists(distfiles_repo: str, overlay_repo: str, pkg: str, ver: str) -> bool:
    """Return True if <pn>-<ver>-deps.tar.xz already exists on distfiles_repo release <cat>-<pn>-<ver>."""
    if not distfiles_repo:
        return False
    tag = deps_release_tag(pkg, ver)
    asset = deps_tarball_name(pkg.split("/", 1)[1], ver)
    env = _gh_env_for_distfiles(distfiles_repo, overlay_repo)
    res = run(
        ["gh", "release", "view", tag, "-R", distfiles_repo, "--json", "assets", "-q", ".assets[].name"],
        env=env,
        check=False,
    )
    if res.returncode != 0:
        return False
    return asset in {line.strip() for line in res.stdout.splitlines() if line.strip()}


def upload_deps_release(
    distfiles_repo: str,
    overlay_repo: str,
    pkg: str,
    ver: str,
    tarball: Path,
    kind: DepsKind,
) -> str:
    if not distfiles_repo:
        raise BumpError("overlay.distfiles_repo (or overlay.repository) must be configured to upload deps")

    env = _gh_env_for_distfiles(distfiles_repo, overlay_repo)
    tag = deps_release_tag(pkg, ver)
    chk = run(["gh", "release", "view", tag, "-R", distfiles_repo], env=env, check=False)
    if chk.returncode != 0:
        run(
            [
                "gh",
                "release",
                "create",
                tag,
                str(tarball),
                "-R",
                distfiles_repo,
                "--title",
                f"{tag}-deps",
                "--notes",
                f"Auto-generated {kind} offline dependencies tarball for `{pkg}-{ver}`.",
            ],
            env=env,
        )
    else:
        run(["gh", "release", "upload", tag, str(tarball), "--clobber", "-R", distfiles_repo], env=env)

    return deps_download_url(distfiles_repo, pkg, ver)


def ensure_deps_tarball(
    *,
    pkg: str,
    ver: str,
    kind: DepsKind,
    src_tar: Path,
    out_tar: Path,
    distfiles_repo: str,
    overlay_repo: str,
    upload: bool,
    go_build_target: str = ".",
    cargo_manifest_dir: str = "",
) -> DepsResolution:
    """Ensure <pn>-<ver>-deps.tar.xz is materialized locally and (when upload=True) hosted remotely.

    Skips rebuilding and re-uploading if the exact release asset already exists on distfiles_repo.
    """
    pn = pkg.split("/", 1)[1]
    tag = deps_release_tag(pkg, ver)
    url = deps_download_url(distfiles_repo, pkg, ver) if distfiles_repo else ""

    if upload and distfiles_repo and release_asset_exists(distfiles_repo, overlay_repo, pkg, ver):
        download_file(url, out_tar)
        return DepsResolution(
            pkg=pkg,
            ver=ver,
            kind=kind,
            tag=tag,
            action="reused_existing",
            download_url=url,
            min_toolchain_ver="",
            has_git_crates=False,
            tarball=out_tar,
        )

    with tempfile.TemporaryDirectory(prefix=f"deps-src-{pn}-") as tmp:
        unpack_dir = Path(tmp) / "src"
        src_root = extract_single_src_root(src_tar, unpack_dir, expected_name=f"{pn}-{ver}")
        if kind == "go":
            meta = build_go_deps_tarball(src_root, unpack_dir, out_tar, build_target=go_build_target)
        elif kind == "rust":
            meta = build_rust_deps_tarball(src_root, out_tar, manifest_dir=cargo_manifest_dir)
        else:
            raise BumpError(f"Unsupported deps kind '{kind}' (expected 'go' or 'rust')")

    if upload and distfiles_repo:
        url = upload_deps_release(
            distfiles_repo=distfiles_repo,
            overlay_repo=overlay_repo,
            pkg=pkg,
            ver=ver,
            tarball=out_tar,
            kind=kind,
        )
        action: DepsAction = "built_and_uploaded"
    else:
        action = "built_local_only"

    return DepsResolution(
        pkg=pkg,
        ver=ver,
        kind=kind,
        tag=tag,
        action=action,
        download_url=url,
        min_toolchain_ver=meta.min_toolchain_ver,
        has_git_crates=meta.has_git_crates,
        tarball=out_tar,
    )


def rewrite_ebuild_deps_metadata(
    eb_text: str,
    *,
    kind: DepsKind,
    distfiles_repo: str,
    min_toolchain_ver: str = "",
) -> str:
    """Replace placeholder / legacy vendor URL and update minimum Go/Rust version constraint."""
    updated = eb_text
    if distfiles_repo:
        uri_tpl = deps_ebuild_uri_template(distfiles_repo)
        updated = updated.replace(DEPS_PLACEHOLDER, uri_tpl)
        updated = re.sub(
            r"https://github\.com/[^/]+/[^/]+/releases/download/[^/\s]+/\$\{P\}-(?:crates|vendor|deps)\.tar\.xz",
            uri_tpl,
            updated,
        )
    if min_toolchain_ver:
        if kind == "go":
            updated = re.sub(r'BDEPEND=">=dev-lang/go-[^"]+"', f'BDEPEND=">=dev-lang/go-{min_toolchain_ver}:="', updated)
        elif kind == "rust" and "RUST_MIN_VER=" in updated:
            updated = re.sub(r'RUST_MIN_VER="[^"]+"', f'RUST_MIN_VER="{min_toolchain_ver}"', updated)
    return updated


def build_deps_for_package(
    cfg: AutomationConfig,
    *,
    pkg: str,
    ver: str,
    kind: DepsKind,
    src_url: str,
    out_dir: Path,
    upload: bool = False,
    go_build_target: str = ".",
    cargo_manifest_dir: str = "",
) -> dict[str, Any]:
    if "/" not in pkg:
        raise BumpError(f"Expected 'category/name' for --pkg, got '{pkg}'")
    pn = pkg.split("/", 1)[1]
    distfiles_repo = cfg.overlay.effective_distfiles_repo
    overlay_repo = os.environ.get("GITHUB_REPOSITORY") or cfg.overlay.repository

    with tempfile.TemporaryDirectory(prefix=f"build-deps-{pn}-") as tmp:
        work = Path(tmp)
        src_ext = ".tar.xz" if src_url.endswith(".tar.xz") else ".tar.gz"
        src_dist = f"{pn}-{ver}{src_ext}"
        src_dl = download_file(src_url, work / src_dist)

        deps_dist = deps_tarball_name(pn, ver)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_tar = out_dir / deps_dist

        res = ensure_deps_tarball(
            pkg=pkg,
            ver=ver,
            kind=kind,
            src_tar=src_dl,
            out_tar=out_tar,
            distfiles_repo=distfiles_repo,
            overlay_repo=overlay_repo,
            upload=upload,
            go_build_target=go_build_target,
            cargo_manifest_dir=cargo_manifest_dir,
        )
        return {
            **asdict(res),
            "tarball": str(res.tarball),
            "distfiles_repo": distfiles_repo,
            "uploaded": res.action in ("built_and_uploaded", "reused_existing"),
            "ebuild_src_uri": deps_ebuild_uri_template(distfiles_repo),
            "manifest_lines": [
                dist_manifest_line(src_dist, src_dl),
                dist_manifest_line(deps_dist, out_tar),
            ],
        }


def _expand_ebuild_vars(text: str, pkg: str, ver: str, eb_text: str) -> str:
    cat, pn = pkg.split("/", 1)
    vars_map: dict[str, str] = {
        "CATEGORY": cat,
        "PN": pn,
        "PV": ver,
        "P": f"{pn}-{ver}",
    }

    def _subst(src: str) -> str:
        return _EBUILD_VAR_RE.sub(
            lambda m: vars_map.get(m.group(1) or m.group(2), m.group(0)),
            src,
        )

    for m in re.finditer(r'^(MY_P|MY_PV|MY_PN)="([^"]+)"', eb_text, re.M):
        vars_map[m.group(1)] = _subst(m.group(2))
    return _subst(text)


def parse_primary_src_uri(eb_text: str, pkg: str, ver: str, distfiles_repo: str) -> tuple[str, str]:
    """Extract the primary upstream source URL & dist filename from an ebuild's SRC_URI."""
    m = re.search(r'SRC_URI="([^"]*)"', eb_text, re.S)
    if not m:
        raise BumpError(f"{pkg}-{ver}: could not find SRC_URI block in ebuild")
    tokens = m.group(1).split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == DEPS_PLACEHOLDER:
            i += 1
            continue
        if tok.startswith(("http://", "https://")):
            url = _expand_ebuild_vars(tok, pkg, ver, eb_text)
            dist = url.rsplit("/", 1)[-1]
            if i + 2 < len(tokens) and tokens[i + 1] == "->":
                dist = _expand_ebuild_vars(tokens[i + 2], pkg, ver, eb_text)
                i += 3
            else:
                i += 1
            if dist.endswith(("-deps.tar.xz", "-vendor.tar.xz", "-crates.tar.xz")):
                continue
            if distfiles_repo and f"github.com/{distfiles_repo}/" in url:
                continue
            return url, dist
        i += 1
    raise BumpError(f"{pkg}-{ver}: no upstream source URL found in SRC_URI")


def detect_deps_kind(eb_text: str, strategy: str = "") -> DepsKind | None:
    if strategy == "go-vendor":
        return "go"
    if strategy == "rust-vendor":
        return "rust"
    inherit_lines = re.findall(r"^\s*inherit\s+(.+)$", eb_text, re.M)
    eclasses = {ec for line in inherit_lines for ec in line.split()}
    if "go-module" in eclasses:
        return "go"
    if "cargo" in eclasses:
        return "rust"
    return None


def ebuild_needs_deps_resolution(
    eb_text: str,
    distfiles_repo: str,
    manifest_dists: dict[str, str],
    pn: str,
    ver: str,
) -> bool:
    if DEPS_PLACEHOLDER in eb_text:
        return True
    deps_name = deps_tarball_name(pn, ver)
    has_deps_uri = bool(
        re.search(r"/\$\{P\}-deps\.tar\.xz\b", eb_text)
        or (distfiles_repo and f"github.com/{distfiles_repo}/" in eb_text)
    )
    return has_deps_uri and deps_name not in manifest_dists


def _resolve_single_package_deps(
    cfg: AutomationConfig,
    pkg: str,
    *,
    distfiles_repo: str,
    overlay_repo: str,
    upload: bool,
) -> tuple[dict[str, Any] | None, str]:
    pdir = cfg.repo_root / pkg
    if not pdir.is_dir():
        return None, "package directory not found"
    ebuilds = list_release_ebuilds(cfg.repo_root, pkg)
    if not ebuilds:
        return None, "no release ebuild"

    ver, eb_path = ebuilds[-1]
    pn = pkg.split("/", 1)[1]
    eb_text = eb_path.read_text(encoding="utf-8")
    manifest_path = pdir / "Manifest"
    manifest_dists = parse_manifest(manifest_path)

    if not ebuild_needs_deps_resolution(eb_text, distfiles_repo, manifest_dists, pn, ver):
        return None, "no placeholder or missing deps entry"

    spec = cfg.packages.get(pkg)
    kind = detect_deps_kind(eb_text, spec.strategy if spec else "")
    if kind is None:
        raise BumpError(f"{pkg}: has {DEPS_PLACEHOLDER} or deps URI but is neither go-module nor cargo")

    src_url, src_dist = parse_primary_src_uri(eb_text, pkg, ver, distfiles_repo)
    deps_dist = deps_tarball_name(pn, ver)

    with tempfile.TemporaryDirectory(prefix=f"resolve-deps-{pn}-") as tmp:
        work = Path(tmp)
        src_dl = download_file(src_url, work / src_dist)
        deps_tar = work / deps_dist
        res = ensure_deps_tarball(
            pkg=pkg,
            ver=ver,
            kind=kind,
            src_tar=src_dl,
            out_tar=deps_tar,
            distfiles_repo=distfiles_repo,
            overlay_repo=overlay_repo,
            upload=upload,
            go_build_target=(spec.go_build_target if spec else "") or ".",
            cargo_manifest_dir=(spec.cargo_manifest_dir if spec else ""),
        )

        new_eb_text = rewrite_ebuild_deps_metadata(
            eb_text,
            kind=kind,
            distfiles_repo=distfiles_repo,
            min_toolchain_ver=res.min_toolchain_ver,
        )
        if new_eb_text != eb_text:
            eb_path.write_text(new_eb_text, encoding="utf-8")

        manifest_dists[src_dist] = dist_manifest_line(src_dist, src_dl)
        manifest_dists[deps_dist] = dist_manifest_line(deps_dist, deps_tar)
        write_manifest(manifest_path, manifest_dists)

    return (
        {
            "pkg": res.pkg,
            "ver": res.ver,
            "kind": res.kind,
            "tag": res.tag,
            "action": res.action,
            "download_url": res.download_url,
            "has_git_crates": res.has_git_crates,
            "ebuild": eb_path.name,
        },
        "",
    )


def resolve_packages_deps(
    cfg: AutomationConfig,
    pkgs: list[str],
    *,
    upload: bool = True,
) -> dict[str, Any]:
    """Detect & materialize __DEPS_TARBALL_URI__ (or missing distfiles deps) for candidate packages."""
    overlay_repo = os.environ.get("GITHUB_REPOSITORY") or cfg.overlay.repository
    distfiles_repo = cfg.overlay.effective_distfiles_repo
    resolved: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for pkg in sorted(set(pkgs)):
        item, reason = _resolve_single_package_deps(
            cfg,
            pkg,
            distfiles_repo=distfiles_repo,
            overlay_repo=overlay_repo,
            upload=upload,
        )
        if item is not None:
            resolved.append(item)
        elif reason != "package directory not found":
            skipped.append({"pkg": pkg, "reason": reason})

    return {
        "distfiles_repo": distfiles_repo,
        "resolved": resolved,
        "skipped": skipped,
    }
