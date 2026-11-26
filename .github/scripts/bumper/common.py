"""Shared utilities: subprocess, HTTP, Manifest I/O, version sorting, ebuild & deb helpers."""

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

REPO_ROOT = Path(__file__).resolve().parents[3]
NVCHECKER_CONF = REPO_ROOT / ".github" / "workflows" / "nvchecker.toml"
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "locez/locez-overlay")
USER_AGENT = "locez-overlay-autobump/1.0"


class BumpError(Exception):
    """Hard failure that should notify the maintainer."""


class BumpSkip(Exception):
    """Temporary/benign skip (e.g. already up-to-date or genpatches not ready yet)."""


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
        raise BumpError(f"Command failed ({res.returncode}): {' '.join(cmd)}\n{detail[-1500:]}")
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


def list_release_ebuilds(pkg: str) -> list[tuple[str, Path]]:
    pdir = REPO_ROOT / pkg
    pn = pkg.split("/", 1)[1]
    result: list[tuple[str, Path]] = []
    for eb in pdir.glob(f"{pn}-*.ebuild"):
        ver = eb.stem[len(pn) + 1 :]
        if ver == "9999":
            continue
        result.append((ver, eb))
    result.sort(key=lambda item: version_key(item[0]))
    return result


def get_local_version(pkg: str) -> str | None:
    ebuilds = list_release_ebuilds(pkg)
    if not ebuilds:
        return None
    ver, eb = ebuilds[-1]
    base_ver = re.sub(r"-r[0-9]+$", "", ver)
    if pkg == "sys-kernel/cachyos-sources":
        m = re.search(r'^CACHYOS_RELEASE="([^"]+)"', eb.read_text(encoding="utf-8"), re.M)
        rel = m.group(1) if m else "1"
        return f"{base_ver}-{rel}"
    return base_ver


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
