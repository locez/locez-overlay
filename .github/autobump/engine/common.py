"""Overlay-agnostic helpers: subprocess, HTTP, thin Manifest I/O, version/ebuild/deb utils."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "gentoo-overlay-autobump/2.0"


class BumpError(Exception):
    """Hard failure that should notify the maintainer or trigger AI repair."""


class BumpSkip(Exception):
    """Benign skip (already up-to-date, or upstream dependency like genpatches not ready yet)."""


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    res = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
        env=merged_env,
    )
    if check and res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        raise BumpError(f"Command failed ({res.returncode}): {' '.join(cmd)}\n{detail[-2000:]}")
    return res


def http_get_text(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> str:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise BumpError(f"HTTP GET failed for {url}: {exc}") from exc


def http_head(url: str, timeout: int = 30) -> dict[str, str]:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {k.lower(): v for k, v in resp.headers.items()}
    except Exception as exc:
        raise BumpError(f"HTTP HEAD failed for {url}: {exc}") from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def http_get_redirect_location(url: str, timeout: int = 30) -> str:
    """Issue a non-following HTTP GET and return the 3xx Location header."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=timeout) as resp:
            loc = resp.headers.get("Location") or ""
            if loc:
                return loc
            raise BumpError(f"Expected redirect Location header from {url}, got HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        loc = exc.headers.get("Location") if exc.headers else None
        if loc:
            return loc
        raise BumpError(f"HTTP GET redirect probe failed for {url}: HTTP {exc.code}") from exc
    except BumpError:
        raise
    except Exception as exc:
        raise BumpError(f"HTTP GET redirect probe failed for {url}: {exc}") from exc


def gh_api_json(path: str) -> Any:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return json.loads(http_get_text(f"https://api.github.com/{path.lstrip('/')}", headers=headers))


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "curl",
            "-fsSL",
            "--retry",
            "3",
            "--retry-delay",
            "3",
            "-A",
            USER_AGENT,
            "-o",
            str(dest),
            url,
        ]
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise BumpError(f"Downloaded empty file from {url}")
    return dest


def download_first_ok(urls: list[str], dest: Path) -> Path:
    last_err = ""
    for url in urls:
        try:
            return download_file(url, dest)
        except BumpError as exc:
            last_err = str(exc)
    raise BumpError(f"All mirrors failed for {dest.name}:\n{last_err}")


def dist_manifest_line(distname: str, filepath: Path) -> str:
    b2 = hashlib.blake2b()
    s5 = hashlib.sha512()
    size = 0
    with filepath.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            size += len(chunk)
            b2.update(chunk)
            s5.update(chunk)
    return f"DIST {distname} {size} BLAKE2B {b2.hexdigest()} SHA512 {s5.hexdigest()}"


def parse_manifest(mpath: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not mpath.exists():
        return entries
    for line in mpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DIST "):
            parts = line.split()
            if len(parts) >= 3:
                entries[parts[1]] = line
    return entries


def write_manifest(mpath: Path, entries: dict[str, str]) -> None:
    lines = [entries[k] for k in sorted(entries)]
    if lines:
        mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mpath.exists():
        mpath.unlink()


def version_key(ver: str) -> tuple[Any, ...]:
    """Natural comparison key handling Gentoo _p / -r / -cachyos suffixes."""
    base = re.sub(r"-r[0-9]+$", "", ver)
    rev_m = re.search(r"-r([0-9]+)$", ver)
    rev = int(rev_m.group(1)) if rev_m else 0
    tokens: list[tuple[int, Any]] = []
    for part in re.split(r"([0-9]+)", base):
        if not part:
            continue
        tokens.append((0, int(part)) if part.isdigit() else (1, part))
    return (tuple(tokens), rev)


def list_release_ebuilds(repo_root: Path, pkg: str) -> list[tuple[str, Path]]:
    pdir = repo_root / pkg
    pn = pkg.split("/", 1)[1]
    result: list[tuple[str, Path]] = []
    for eb in pdir.glob(f"{pn}-*.ebuild"):
        ver = eb.stem[len(pn) + 1 :]
        if ver == "9999":
            continue
        result.append((ver, eb))
    result.sort(key=lambda item: version_key(item[0]))
    return result


def get_local_version(repo_root: Path, pkg: str, strategy: str = "") -> str | None:
    ebuilds = list_release_ebuilds(repo_root, pkg)
    if not ebuilds:
        return None
    ver, eb = ebuilds[-1]
    base_ver = re.sub(r"-r[0-9]+$", "", ver)
    if strategy == "kernel-cachyos":
        m = re.search(r'^CACHYOS_RELEASE="([^"]+)"', eb.read_text(encoding="utf-8"), re.M)
        rel = m.group(1) if m else "1"
        return f"{base_ver}-{rel}"
    return base_ver


def detect_ebuild_arches(
    repo_root: Path,
    pkg: str,
    *,
    configured_arches: tuple[str, ...] = (),
    live: bool = False,
) -> list[str]:
    """Determine supported CI architectures ('x64', 'arm64') from explicit config or ebuild KEYWORDS."""
    if configured_arches:
        return list(configured_arches)
    pn = pkg.split("/", 1)[1]
    ebuilds = list_release_ebuilds(repo_root, pkg)
    eb_path: Path | None = None
    if not live and ebuilds:
        eb_path = ebuilds[-1][1]
    elif (repo_root / pkg / f"{pn}-9999.ebuild").is_file():
        eb_path = repo_root / pkg / f"{pn}-9999.ebuild"
    elif ebuilds:
        eb_path = ebuilds[-1][1]
    if eb_path is None:
        return ["x64"]

    text = eb_path.read_text(encoding="utf-8")
    kw_matches = re.findall(r'^\s*KEYWORDS="([^"]*)"', text, flags=re.M)
    if not kw_matches:
        return ["x64", "arm64"]
    tokens: set[str] = set()
    for m in kw_matches:
        tokens.update(m.split())
    arches: list[str] = []
    if "amd64" in tokens or "~amd64" in tokens:
        arches.append("x64")
    if "arm64" in tokens or "~arm64" in tokens:
        arches.append("arm64")
    return arches or ["x64"]


def extract_deb_control_field(deb_path: Path, field: str, workdir: Path) -> str:
    ext = workdir / f"deb_{deb_path.stem}"
    if ext.exists():
        shutil.rmtree(ext)
    ext.mkdir(parents=True)
    run(["ar", "x", str(deb_path)], cwd=ext)
    ctrl_tar = next(ext.glob("control.tar*"), None)
    if not ctrl_tar:
        raise BumpError(f"No control.tar* found in {deb_path.name}")
    run(["tar", "-xf", str(ctrl_tar)], cwd=ext)
    ctrl_file = ext / "control"
    if not ctrl_file.exists():
        ctrl_file = ext / "./control"
    text = ctrl_file.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, re.M)
    if not m:
        raise BumpError(f"Field '{field}' not found in {deb_path.name} DEBIAN/control")
    return m.group(1).strip()
