"""Zig ZON parser and offline `--system` dependency stager & tarball builder."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..common import BumpError, download_file, run
from .common import DepsBuildMeta, StageOutput, pack_deterministic_tar_xz


@dataclass(frozen=True)
class ZigZonDep:
    name: str
    url: str = ""
    hash: str = ""
    path: str = ""
    lazy: bool = False


def _strip_zig_comments(text: str) -> str:
    """Strip // line comments from Zig ZON while preserving string literals."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_matching_brace(text: str, open_idx: int) -> int:
    """Return index of '}' matching '{' at open_idx, ignoring braces inside string literals."""
    depth = 0
    i = open_idx
    n = len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise BumpError("Unclosed '{' while parsing build.zig.zon")


def parse_zig_zon(zon_text: str) -> tuple[str, list[ZigZonDep]]:
    """Parse minimum_zig_version and .dependencies entries from a build.zig.zon file."""
    cleaned = _strip_zig_comments(zon_text)
    min_m = re.search(r'\.minimum_zig_version\s*=\s*"([^"]+)"', cleaned)
    min_zig_ver = min_m.group(1) if min_m else ""

    deps_m = re.search(r"\.dependencies\s*=\s*\.\{", cleaned)
    if not deps_m:
        return min_zig_ver, []

    open_idx = deps_m.end() - 1
    close_idx = _find_matching_brace(cleaned, open_idx)
    body = cleaned[open_idx + 1 : close_idx]

    deps: list[ZigZonDep] = []
    pos = 0
    entry_re = re.compile(r'\.(@"((?:\\.|[^"\\])+)"|([A-Za-z_][A-Za-z0-9_]*))\s*=\s*\.\{')
    while pos < len(body):
        m = entry_re.search(body, pos)
        if not m:
            break
        name = m.group(2) if m.group(2) is not None else m.group(3)
        entry_open = m.end() - 1
        entry_close = _find_matching_brace(body, entry_open)
        entry_body = body[entry_open + 1 : entry_close]

        url_m = re.search(r'\.url\s*=\s*"([^"]+)"', entry_body)
        hash_m = re.search(r'\.hash\s*=\s*"([^"]+)"', entry_body)
        path_m = re.search(r'\.path\s*=\s*"([^"]+)"', entry_body)
        lazy_m = re.search(r"\.lazy\s*=\s*(true|false)\b", entry_body)
        deps.append(
            ZigZonDep(
                name=name,
                url=url_m.group(1) if url_m else "",
                hash=hash_m.group(1) if hash_m else "",
                path=path_m.group(1) if path_m else "",
                lazy=(lazy_m.group(1) == "true") if lazy_m else False,
            )
        )
        pos = entry_close + 1
    return min_zig_ver, deps


def _extract_zig_package(url: str, dest_dir: Path, work_dir: Path) -> None:
    """Fetch and unpack a single Zig package (tarball, zip, or git+https) into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if url.startswith(("git+http://", "git+https://")):
        raw = url[len("git+") :]
        if "#" not in raw:
            raise BumpError(f"Unsupported Zig git dependency without revision fragment: {url}")
        repo_part, rev = raw.split("#", 1)
        repo_url = repo_part.split("?", 1)[0]
        with tempfile.TemporaryDirectory(prefix="zig-git-", dir=work_dir) as tmp:
            repo_dir = Path(tmp) / "repo"
            repo_dir.mkdir()
            archive_tar = Path(tmp) / "archive.tar"
            run(["git", "init", "-q"], cwd=repo_dir)
            run(["git", "fetch", "--depth", "1", repo_url, rev], cwd=repo_dir)
            run(["git", "archive", "--format=tar", "-o", str(archive_tar), "FETCH_HEAD"], cwd=repo_dir)
            run(["tar", "-xf", str(archive_tar), "-C", str(dest_dir)])
        return

    if not url.startswith(("http://", "https://")):
        raise BumpError(f"Unsupported Zig dependency URL scheme: {url}")

    fname = url.rsplit("/", 1)[-1].split("?", 1)[0] or "pkg.tar.gz"
    dl_path = work_dir / f"{dest_dir.name}-{fname}"
    download_file(url, dl_path)

    with tempfile.TemporaryDirectory(prefix="zig-extract-", dir=work_dir) as tmp:
        extract_tmp = Path(tmp)
        if fname.endswith(".zip"):
            with zipfile.ZipFile(dl_path) as zf:
                zf.extractall(extract_tmp)
        else:
            run(["tar", "-xf", str(dl_path), "-C", str(extract_tmp)])

        entries = [e for e in extract_tmp.iterdir() if e.name != "pax_global_header"]
        root_src = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_tmp
        for child in root_src.iterdir():
            shutil.move(str(child), str(dest_dir / child.name))

    git_meta = dest_dir / ".git"
    if git_meta.exists():
        shutil.rmtree(git_meta, ignore_errors=True)


def vendor_zig_deps(
    manifest_root: Path,
    out_dir: Path,
    *,
    src_root: Path | None = None,
    selected_lazy: set[str] | None = None,
) -> tuple[str, int]:
    """Recursively crawl build.zig.zon and populate a `zig build --system` directory at out_dir."""
    root_zon = manifest_root / "build.zig.zon"
    if not root_zon.is_file():
        raise BumpError(f"build.zig.zon not found in {manifest_root}")

    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_src_root = (src_root or manifest_root).resolve()
    min_zig_ver, _ = parse_zig_zon(root_zon.read_text(encoding="utf-8"))
    visited_dirs: set[Path] = {manifest_root.resolve()}
    fetched_hashes: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="zig-vendor-dl-") as dl_tmp:
        dl_work = Path(dl_tmp)

        def _crawl(zon_dir: Path, *, lazy_allowlist: set[str] | None) -> None:
            zon_path = zon_dir / "build.zig.zon"
            if not zon_path.is_file():
                return
            _, deps = parse_zig_zon(zon_path.read_text(encoding="utf-8"))
            for dep in deps:
                if dep.lazy and lazy_allowlist is not None and dep.name not in lazy_allowlist:
                    continue
                if dep.path:
                    target = (zon_dir / dep.path).resolve()
                    try:
                        target.relative_to(resolved_src_root)
                    except ValueError:
                        raise BumpError(
                            f"Zig local path dependency '{dep.name}' ({dep.path}) escapes source root {resolved_src_root}"
                        )
                    if target.is_dir() and target not in visited_dirs:
                        visited_dirs.add(target)
                        _crawl(target, lazy_allowlist=None)
                elif dep.url:
                    if not dep.hash:
                        raise BumpError(f"Zig dependency '{dep.name}' ({dep.url}) is missing .hash in {zon_path}")
                    if dep.hash in fetched_hashes:
                        continue
                    fetched_hashes.add(dep.hash)
                    pkg_dir = out_dir / dep.hash
                    _extract_zig_package(dep.url, pkg_dir, dl_work)
                    _crawl(pkg_dir, lazy_allowlist=None)

        _crawl(manifest_root.resolve(), lazy_allowlist=selected_lazy)

    return min_zig_ver, len(fetched_hashes)


def stage_zig_deps(
    src_root: Path,
    stage_dir: Path,
    *,
    manifest_dir: str = "",
    zig_deps: tuple[str, ...] = (),
) -> StageOutput:
    """Populate `stage_dir/zig_packages/<hash>/...` from `build.zig.zon`."""
    manifest_root = (src_root / manifest_dir) if manifest_dir else src_root
    min_zig_ver, _ = vendor_zig_deps(
        manifest_root,
        stage_dir / "zig_packages",
        src_root=src_root,
        selected_lazy=set(zig_deps) if zig_deps else None,
    )
    return StageOutput(members=("zig_packages",), min_toolchain_ver=min_zig_ver)


def build_zig_deps_tarball(
    src_root: Path,
    out_tar: Path,
    *,
    manifest_dir: str = "",
    zig_deps: tuple[str, ...] = (),
) -> DepsBuildMeta:
    """Build a standalone Zig `${P}-deps.tar.xz` containing `zig_packages/`."""
    with tempfile.TemporaryDirectory(prefix="zig-vendor-stage-") as tmp:
        stage = Path(tmp)
        staged = stage_zig_deps(src_root, stage, manifest_dir=manifest_dir, zig_deps=zig_deps)
        pack_deterministic_tar_xz(stage, staged.members, out_tar)
        return DepsBuildMeta(
            kind="zig",
            min_toolchain_ver=staged.min_toolchain_ver,
            has_git_crates=False,
            tarball=out_tar,
        )
