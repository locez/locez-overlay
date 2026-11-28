"""Orchestrator for composing Go/Rust/Zig offline deps, uploading releases, and resolving PR placeholders."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..common import (
    BumpError,
    dist_manifest_line,
    download_file,
    list_release_ebuilds,
    parse_manifest,
    run,
    write_manifest,
)
from ..schema import AutomationConfig, PackageSpec
from .common import (
    DEPS_PLACEHOLDER,
    DepsAction,
    DepsBuildMeta,
    DepsKind,
    DepsOptions,
    DepsResolution,
    deps_download_url,
    deps_ebuild_uri_template,
    deps_release_tag,
    deps_tarball_name,
    extract_single_src_root,
    pack_deterministic_tar_xz,
)
from .go import build_go_deps_tarball
from .rust import stage_rust_deps
from .zig import stage_zig_deps

_EBUILD_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def deps_options_from_spec(spec: PackageSpec | None) -> DepsOptions:
    if spec is None:
        return DepsOptions()
    opts = spec.strategy_opts
    if spec.strategy == "go-vendor":
        return DepsOptions(go_build_target=str(opts.get("build_target", ".")) or ".")
    if spec.strategy in ("rust-vendor", "rust-crates"):
        zig_cfg = opts.get("zig") if isinstance(opts.get("zig"), dict) else {}
        return DepsOptions(
            cargo_manifest_dir=str(opts.get("manifest_dir", "")),
            zig_manifest_dir=str(zig_cfg.get("manifest_dir", "")),
            zig_deps=tuple(str(d) for d in zig_cfg.get("deps", ())),
        )
    if spec.strategy == "zig-vendor":
        return DepsOptions(
            zig_manifest_dir=str(opts.get("manifest_dir", "")),
            zig_deps=tuple(str(d) for d in opts.get("deps", ())),
        )
    return DepsOptions()


def _build_deps_archive(
    *,
    kind: DepsKind,
    src_root: Path,
    unpack_dir: Path,
    out_tar: Path,
    options: DepsOptions,
) -> DepsBuildMeta:
    """Compose one or more ecosystem stagers into a single deterministic `${P}-deps.tar.xz`."""
    if kind == "go":
        return build_go_deps_tarball(src_root, unpack_dir, out_tar, build_target=options.go_build_target)

    if kind == "rust":
        with tempfile.TemporaryDirectory(prefix="deps-stage-rust-") as tmp:
            stage = Path(tmp)
            rust_out = stage_rust_deps(src_root, stage, manifest_dir=options.cargo_manifest_dir)
            members = list(rust_out.members)
            if options.zig_manifest_dir:
                zig_out = stage_zig_deps(
                    src_root,
                    stage,
                    manifest_dir=options.zig_manifest_dir,
                    zig_deps=options.zig_deps,
                )
                members.extend(zig_out.members)
            pack_deterministic_tar_xz(stage, members, out_tar)
            return DepsBuildMeta(
                kind="rust",
                min_toolchain_ver=rust_out.min_toolchain_ver,
                has_git_crates=rust_out.has_git_crates,
                tarball=out_tar,
            )

    if kind == "zig":
        with tempfile.TemporaryDirectory(prefix="deps-stage-zig-") as tmp:
            stage = Path(tmp)
            zig_out = stage_zig_deps(
                src_root,
                stage,
                manifest_dir=options.zig_manifest_dir,
                zig_deps=options.zig_deps,
            )
            pack_deterministic_tar_xz(stage, zig_out.members, out_tar)
            return DepsBuildMeta(
                kind="zig",
                min_toolchain_ver=zig_out.min_toolchain_ver,
                has_git_crates=False,
                tarball=out_tar,
            )

    raise BumpError(f"Unsupported deps kind '{kind}' (expected 'go', 'rust', or 'zig')")


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
    options: DepsOptions | None = None,
) -> DepsResolution:
    """Ensure <pn>-<ver>-deps.tar.xz is materialized locally and (when upload=True) hosted remotely.

    Skips rebuilding and re-uploading if the exact release asset already exists on distfiles_repo.
    """
    opts = options or DepsOptions()
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
        meta = _build_deps_archive(
            kind=kind,
            src_root=src_root,
            unpack_dir=unpack_dir,
            out_tar=out_tar,
            options=opts,
        )

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
    options: DepsOptions | None = None,
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
            options=options,
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
    if strategy == "zig-vendor":
        return "zig"
    inherit_lines = re.findall(r"^\s*inherit\s+(.+)$", eb_text, re.M)
    eclasses = {ec for line in inherit_lines for ec in line.split()}
    if "go-module" in eclasses:
        return "go"
    if "cargo" in eclasses:
        return "rust"
    if "zig" in eclasses or "zig-utils" in eclasses:
        return "zig"
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
        raise BumpError(f"{pkg}: has {DEPS_PLACEHOLDER} or deps URI but is neither go-module, cargo, nor zig")

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
            options=deps_options_from_spec(spec),
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
