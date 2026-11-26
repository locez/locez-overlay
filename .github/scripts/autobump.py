#!/usr/bin/env python3
"""Automated ebuild bump engine for locez-overlay.

Works identically in GitHub Actions and locally:
  - Detect outdated packages (combining nvchecker output + WeChat HEAD probe)
  - Bump Tier 1 (simple/multi-slot), Tier 2 (QQ, WeChat, tsshd, Cider),
    and Tier 3 (same-minor XanMod & CachyOS kernels with full patch verification)
  - Update thin Manifest files (BLAKE2B + SHA512)
  - Return structured status ("bumped", "skipped", "failed") for CI PR/Issue automation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
NVCHECKER_CONF = REPO_ROOT / ".github" / "workflows" / "nvchecker.toml"
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "locez/locez-overlay")

USER_AGENT = "locez-overlay-autobump/1.0"
PROTON_KEEP_SLOTS = 3

WECHAT_X64_URL = "https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb"
WECHAT_ARM64_URL = "https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_arm64.deb"
QQ_CONFIG_URL = "https://cdn-go.cn/qq-web/im.qq.com_new/latest/rainbow/linuxConfig.js"
QQ_PROXY_PREFIX = "https://dlqq.akkariin.moe/"


class BumpError(Exception):
    """Hard failure that should notify the maintainer."""


class BumpSkip(Exception):
    """Temporary/benign skip (e.g. Gentoo genpatches not published yet)."""


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


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
        if part.isdigit():
            tokens.append((0, int(part)))
        else:
            tokens.append((1, part))
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


def gh_api_json(path: str) -> Any:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return json.loads(http_get_text(f"https://api.github.com/{path.lstrip('/')}", headers=headers))


def get_gentoo_genpatches_ver(kernel_ver: str) -> str | None:
    """Look up K_GENPATCHES_VER from gentoo-mirror/gentoo for a given kernel PV."""
    url = (
        "https://raw.githubusercontent.com/gentoo-mirror/gentoo/master/"
        f"sys-kernel/gentoo-sources/gentoo-sources-{kernel_ver}.ebuild"
    )
    try:
        text = http_get_text(url)
    except BumpError:
        return None
    m = re.search(r'^K_GENPATCHES_VER="([^"]+)"', text, re.M)
    return m.group(1) if m else None


def get_gentoo_stable_zfs_info() -> tuple[str, str]:
    """Return (stable_zfs_ver, modules_kernel_max) from gentoo-mirror/gentoo sys-fs/zfs."""
    items = gh_api_json("repos/gentoo-mirror/gentoo/contents/sys-fs/zfs")
    candidates: list[tuple[str, str]] = []
    for item in items:
        name = item.get("name", "")
        if not name.startswith("zfs-") or not name.endswith(".ebuild") or "9999" in name:
            continue
        ver = name[len("zfs-") : -len(".ebuild")]
        candidates.append((ver, name))
    candidates.sort(key=lambda x: version_key(x[0]), reverse=True)

    for ver, fname in candidates:
        text = http_get_text(
            f"https://raw.githubusercontent.com/gentoo-mirror/gentoo/master/sys-fs/zfs/{fname}"
        )
        kw_m = re.search(r'^\s*KEYWORDS="([^"]+)"', text, re.M)
        max_m = re.search(r'^MODULES_KERNEL_MAX=([0-9]+\.[0-9]+)', text, re.M)
        if not kw_m or not max_m:
            continue
        # Stable amd64 keyword (amd64 without leading ~)
        if re.search(r"(?<!~)\bamd64\b", kw_m.group(1)):
            return (ver, max_m.group(1))
    raise BumpError("Could not determine stable amd64 sys-fs/zfs MODULES_KERNEL_MAX from Gentoo tree")


def ebuild_forbids_zfs(eb_text: str) -> bool:
    m = re.search(r'REQUIRED_USE="([^"]*)"', eb_text, re.S)
    return bool(m and re.search(r"(^|\s)!zfs(\s|$)", m.group(1)))


def ebuild_supports_zfs(ver: str, eb_text: str, zfs_max_series: str) -> bool:
    if ebuild_forbids_zfs(eb_text):
        return False
    series = ".".join(ver.split(".")[:2])
    return version_key(series) <= version_key(zfs_max_series)


def keep_old_kernel_for_zfs(
    pkg: str,
    old_eb: Path,
    new_ver: str,
    new_eb_text: str,
    zfs_max_series: str,
) -> bool:
    """Return True if old_eb must be kept because dropping it would leave no ZFS-compatible kernel."""
    if ebuild_supports_zfs(new_ver, new_eb_text, zfs_max_series):
        return False
    old_resolved = old_eb.resolve()
    for ver, eb in list_release_ebuilds(pkg):
        if eb.resolve() == old_resolved or ver == new_ver:
            continue
        if ebuild_supports_zfs(ver, eb.read_text(encoding="utf-8"), zfs_max_series):
            return False
    # No other ebuild (nor the new one) supports ZFS <= zfs_max_series: keep old_eb!
    return True


# ---------------------------------------------------------------------------
# Package-specific bump handlers
# ---------------------------------------------------------------------------


def _replace_single_ebuild(
    pkg: str,
    new_ver: str,
    dist_map: list[tuple[str, str]],
    work: Path,
    *,
    verify_fn: Any = None,
) -> None:
    pdir = REPO_ROOT / pkg
    pn = pkg.split("/", 1)[1]
    ebuilds = list_release_ebuilds(pkg)
    if not ebuilds:
        raise BumpError(f"No existing release ebuild found for {pkg}")
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{pkg} is already at {new_ver}")

    new_eb = pdir / f"{pn}-{new_ver}.ebuild"
    shutil.copy2(old_eb, new_eb)

    entries: dict[str, str] = {}
    downloaded: list[Path] = []
    for distname, url in dist_map:
        dl = download_file(url, work / distname)
        downloaded.append(dl)
        entries[distname] = dist_manifest_line(distname, dl)

    if verify_fn:
        verify_fn(downloaded, new_eb)

    for ver, eb in ebuilds:
        if eb != new_eb:
            eb.unlink()
    write_manifest(pdir / "Manifest", entries)


def bump_pypi(pkg: str, new_ver: str, work: Path) -> None:
    pn = pkg.split("/", 1)[1]
    pypi_name = pn.replace("-", "_")
    meta = json.loads(http_get_text(f"https://pypi.org/pypi/{pn}/{new_ver}/json"))
    sdist = next((u for u in meta.get("urls", []) if u.get("packagetype") == "sdist"), None)
    if not sdist:
        raise BumpError(f"No sdist found on PyPI for {pn}=={new_ver}")
    distname = sdist["filename"]
    url = sdist["url"]
    if not distname.startswith((f"{pn}-", f"{pypi_name}-")):
        raise BumpError(f"Unexpected PyPI sdist filename: {distname}")
    _replace_single_ebuild(pkg, new_ver, [(distname, url)], work)


def bump_simple_github(pkg: str, new_ver: str, work: Path) -> None:
    configs: dict[str, tuple[str, str]] = {
        "media-libs/libdicom": (
            f"libdicom-{new_ver}.tar.xz",
            f"https://github.com/ImagingDataCommons/libdicom/releases/download/v{new_ver}/libdicom-{new_ver}.tar.xz",
        ),
        "media-libs/openslide": (
            f"openslide-{new_ver}.tar.xz",
            f"https://github.com/openslide/openslide/releases/download/v{new_ver}/openslide-{new_ver}.tar.xz",
        ),
        "media-plugins/kotonoha": (
            f"kotonoha-{new_ver}.tar.gz",
            f"https://github.com/locez/kotonoha/archive/refs/tags/v{new_ver}.tar.gz",
        ),
        "x11-misc/flatpak-xdg-utils": (
            f"flatpak-xdg-utils-{new_ver}.tar.xz",
            f"https://github.com/flatpak/flatpak-xdg-utils/releases/download/{new_ver}/flatpak-xdg-utils-{new_ver}.tar.xz",
        ),
    }
    distname, url = configs[pkg]
    _replace_single_ebuild(pkg, new_ver, [(distname, url)], work)


def bump_cider(pkg: str, new_ver: str, work: Path) -> None:
    distname = f"cider-v{new_ver}-linux-x64.deb"
    url = f"https://repo.cider.sh/apt/pool/main/{distname}"

    def verify_cider(files: list[Path], _eb: Path) -> None:
        deb = files[0]
        ext = work / "cider_check"
        ext.mkdir(parents=True, exist_ok=True)
        run(["ar", "x", str(deb)], cwd=ext)
        data_tar = next(ext.glob("data.tar*"), None)
        if not data_tar:
            raise BumpError(f"{deb.name} missing data.tar*")
        listing = run(["tar", "-tf", str(data_tar)]).stdout
        for required in ("usr/lib/cider/Cider", "usr/share/applications/cider.desktop"):
            if required not in listing:
                raise BumpError(f"{deb.name} missing expected path: {required}")

    _replace_single_ebuild(pkg, new_ver, [(distname, url)], work, verify_fn=verify_cider)


def bump_proton_ge(pkg: str, new_ver: str, work: Path) -> None:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    if any(v == new_ver for v, _ in ebuilds):
        raise BumpSkip(f"{pkg} already has {new_ver}")
    _, latest_eb = ebuilds[-1]
    my_pv = new_ver.replace(".", "-")
    distname = f"GE-Proton{my_pv}-x86_64.tar.gz"
    url = f"https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton{my_pv}/{distname}"

    dl = download_file(url, work / distname)
    # Verify expected top-level directory and compatibilitytool.vdf
    expected_vdf = f"GE-Proton{my_pv}-x86_64/compatibilitytool.vdf"
    res = run(["tar", "-xOf", str(dl), expected_vdf])
    if '"install_path" "."' not in res.stdout:
        raise BumpError(f"{distname}: unexpected compatibilitytool.vdf content")

    new_eb = pdir / f"proton-ge-bin-{new_ver}.ebuild"
    shutil.copy2(latest_eb, new_eb)

    entries = parse_manifest(pdir / "Manifest")
    entries[distname] = dist_manifest_line(distname, dl)

    # Prune oldest slots beyond PROTON_KEEP_SLOTS
    all_ebuilds = list_release_ebuilds(pkg)
    while len(all_ebuilds) > PROTON_KEEP_SLOTS:
        old_ver, old_eb = all_ebuilds.pop(0)
        old_my_pv = old_ver.replace(".", "-")
        old_eb.unlink()
        for candidate in (f"GE-Proton{old_my_pv}.tar.gz", f"GE-Proton{old_my_pv}-x86_64.tar.gz"):
            entries.pop(candidate, None)

    write_manifest(pdir / "Manifest", entries)


def bump_dwproton(pkg: str, new_ver: str, work: Path) -> None:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    if any(v == new_ver for v, _ in ebuilds):
        raise BumpSkip(f"{pkg} already has {new_ver}")
    _, latest_eb = ebuilds[-1]
    eb_text = latest_eb.read_text(encoding="utf-8")

    my_pv = new_ver.replace("_p", "-")
    distname = f"dwproton-{my_pv}-x86_64.tar.xz"
    url = f"https://dawn.wine/dawn-winery/dwproton/releases/download/dwproton-{my_pv}/{distname}"
    dl = download_file(url, work / distname)

    # Verify compatibilitytool.vdf and every patchelf target referenced in ebuild
    listing_lines = set(run(["tar", "-tf", str(dl)]).stdout.splitlines())
    prefix = f"dwproton-{my_pv}-x86_64/"
    if f"{prefix}compatibilitytool.vdf" not in listing_lines:
        raise BumpError(f"{distname} missing {prefix}compatibilitytool.vdf")

    expected_relpaths = re.findall(r"(files/lib/i386-linux-gnu/lib[^\s\\]+|protonfixes/files/bin/p(?:grep|kill))", eb_text)
    missing = [p for p in expected_relpaths if f"{prefix}{p}" not in listing_lines]
    if missing:
        found_libs = sorted(
            line[len(prefix) :]
            for line in listing_lines
            if line.startswith(f"{prefix}files/lib/i386-linux-gnu/lib")
            and ("webp" in line or "sharpyuv" in line)
        )
        raise BumpError(
            f"{distname}: patchelf targets changed!\n"
            f"Missing: {missing}\n"
            f"Present webp/sharpyuv libs: {found_libs}"
        )

    new_eb = pdir / f"dwproton-bin-{new_ver}.ebuild"
    shutil.copy2(latest_eb, new_eb)

    entries = parse_manifest(pdir / "Manifest")
    entries[distname] = dist_manifest_line(distname, dl)

    all_ebuilds = list_release_ebuilds(pkg)
    while len(all_ebuilds) > PROTON_KEEP_SLOTS:
        old_ver, old_eb = all_ebuilds.pop(0)
        old_my_pv = old_ver.replace("_p", "-")
        old_eb.unlink()
        entries.pop(f"dwproton-{old_my_pv}-x86_64.tar.xz", None)

    write_manifest(pdir / "Manifest", entries)


def bump_tencent_qq(pkg: str, new_ver: str, work: Path) -> None:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{pkg} already at {new_ver}")

    js = http_get_text(QQ_CONFIG_URL)
    m = re.search(r"var\s+params\s*=\s*(\{.*?\});", js, re.S)
    if not m:
        raise BumpError("Failed to parse params JSON from Tencent QQ linuxConfig.js")
    params = json.loads(m.group(1))
    amd64_url = params["x64DownloadUrl"]["deb"]
    arm64_url = params["armDownloadUrl"]["deb"]

    # Extract path segment between qqdl.gtimg.cn/qqfile/QQNTV2/ and /QQ_...
    url_m = re.match(r"^(https://[^/]+/.+?)/QQ_([0-9.]+_[0-9]+)_amd64_01\.deb$", amd64_url)
    if not url_m:
        raise BumpError(f"Unexpected Tencent QQ amd64 URL format: {amd64_url}")
    base_url = url_m.group(1)
    detected_pv = re.sub(r"_([0-9]+)$", r"_p\1", url_m.group(2))
    if detected_pv != new_ver:
        new_ver = detected_pv

    amd64_dist = f"tencent-qq-{new_ver}_amd64.deb"
    arm64_dist = f"tencent-qq-{new_ver}_arm64.deb"
    amd64_dl = download_file(QQ_PROXY_PREFIX + amd64_url, work / amd64_dist)
    arm64_dl = download_file(QQ_PROXY_PREFIX + arm64_url, work / arm64_dist)

    deb_ver = extract_deb_control_field(amd64_dl, "Version", work)
    # Typical format: 3.2.35-54395
    build_m = re.search(r"-([0-9]+)$", deb_ver)
    if not build_m:
        raise BumpError(f"Cannot extract BUILD number from QQ deb Version '{deb_ver}'")
    new_build = build_m.group(1)

    eb_text = old_eb.read_text(encoding="utf-8")
    eb_text_new = re.sub(r'^BUILD="[^"]+"', f'BUILD="{new_build}"', eb_text, count=1, flags=re.M)
    eb_text_new = re.sub(
        r'printf "https://[^"]+/QQ_\$\{PV/p/\}_\$\{2:-\$1\}_01\.deb"',
        f'printf "{base_url}/QQ_${{PV/p/}}_${{2:-$1}}_01.deb"',
        eb_text_new,
        count=1,
    )
    if f'BUILD="{new_build}"' not in eb_text_new or base_url not in eb_text_new:
        raise BumpError("Failed to update BUILD or qq_src_uri() in tencent-qq ebuild")

    new_eb = pdir / f"tencent-qq-{new_ver}.ebuild"
    new_eb.write_text(eb_text_new, encoding="utf-8")
    for _, eb in ebuilds:
        if eb != new_eb:
            eb.unlink()

    write_manifest(
        pdir / "Manifest",
        {
            amd64_dist: dist_manifest_line(amd64_dist, amd64_dl),
            arm64_dist: dist_manifest_line(arm64_dist, arm64_dl),
        },
    )


def detect_wechat_update() -> tuple[str, str] | None:
    """Return (local_ver, 'probe') if WeChat Content-Length changed vs Manifest."""
    pdir = REPO_ROOT / "net-im" / "wechat"
    local_ver = get_local_version("net-im/wechat")
    if not local_ver:
        return None
    manifest = parse_manifest(pdir / "Manifest")
    cur_dist = f"wechat-{local_ver}_x86_64.deb"
    cur_size = None
    if cur_dist in manifest:
        parts = manifest[cur_dist].split()
        if len(parts) >= 3 and parts[2].isdigit():
            cur_size = int(parts[2])
    hdrs = http_head(WECHAT_X64_URL)
    remote_size = int(hdrs["content-length"]) if "content-length" in hdrs and hdrs["content-length"].isdigit() else None
    if remote_size is not None and cur_size is not None and remote_size != cur_size:
        return (local_ver, f"size-{remote_size}")
    return None


def bump_wechat(pkg: str, _target: str, work: Path) -> str:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    old_ver, old_eb = ebuilds[-1]
    old_base = re.sub(r"-r[0-9]+$", "", old_ver)

    x64_tmp = download_file(WECHAT_X64_URL, work / "WeChatLinux_x86_64.deb")
    new_ver = extract_deb_control_field(x64_tmp, "Version", work)
    if new_ver == old_base:
        raise BumpError(
            f"WeChat upstream silent repack detected: Version is still {new_ver} "
            f"but Content-Length changed to {x64_tmp.stat().st_size} bytes. Manual check required."
        )
    if version_key(new_ver) <= version_key(old_base):
        raise BumpError(f"WeChat upstream version {new_ver} is not newer than local {old_base}")

    arm64_tmp = download_file(WECHAT_ARM64_URL, work / "WeChatLinux_arm64.deb")
    arm_ver = extract_deb_control_field(arm64_tmp, "Version", work)
    if arm_ver != new_ver:
        raise BumpError(f"WeChat arch version mismatch: x86_64={new_ver}, arm64={arm_ver}")

    x64_dist = f"wechat-{new_ver}_x86_64.deb"
    arm64_dist = f"wechat-{new_ver}_arm64.deb"

    new_eb = pdir / f"wechat-{new_ver}.ebuild"
    shutil.copy2(old_eb, new_eb)
    for _, eb in ebuilds:
        if eb != new_eb:
            eb.unlink()

    write_manifest(
        pdir / "Manifest",
        {
            x64_dist: dist_manifest_line(x64_dist, x64_tmp),
            arm64_dist: dist_manifest_line(arm64_dist, arm64_tmp),
        },
    )
    return new_ver


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

    # Detect minimum Go version from go.mod
    go_mod = (src_root / "go.mod").read_text(encoding="utf-8")
    go_m = re.search(r"^go\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", go_mod, re.M)
    go_ver = go_m.group(1) if go_m else None

    # Build vendor tarball and verify Go build compiles cleanly
    run(["go", "mod", "vendor"], cwd=src_root)
    run(["go", "build", "-mod=vendor", "-buildvcs=false", "-o", os.devnull, "./cmd/tsshd"], cwd=src_root)
    vendor_tar = work / vendor_dist
    run(["tar", "-cJf", str(vendor_tar), "-C", str(unpack_dir), f"tsshd-{new_ver}/vendor"])

    tag = f"vendor-tsshd-v{new_ver}"
    vendor_url = f"https://github.com/{GH_REPO}/releases/download/{tag}/{vendor_dist}"
    if upload_vendor:
        # Create or replace GitHub release asset on this repo
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


def bump_xanmod(pkg: str, new_ver: str, work: Path) -> None:
    pdir = REPO_ROOT / pkg
    ebuilds = list_release_ebuilds(pkg)
    new_series = ".".join(new_ver.split(".")[:2])
    same_series = [(v, eb) for v, eb in ebuilds if ".".join(v.split(".")[:2]) == new_series]
    if not same_series:
        latest_ver = ebuilds[-1][0]
        raise BumpError(
            f"xanmod-sources minor series jump ({latest_ver} -> {new_ver}) requires manual review "
            f"(ZFS compatibility gate and patch verification)."
        )
    old_ver, old_eb = same_series[-1]
    if old_ver == new_ver:
        raise BumpSkip(f"{pkg} already at {new_ver}")

    zfs_ver, zfs_max = get_gentoo_stable_zfs_info()

    gp_ver = get_gentoo_genpatches_ver(new_ver)
    if not gp_ver:
        raise BumpSkip(f"gentoo-sources-{new_ver} (genpatches) not published in Gentoo tree yet")

    eb_text = old_eb.read_text(encoding="utf-8")
    if version_key(new_series) > version_key(zfs_max) and not ebuild_forbids_zfs(eb_text):
        raise BumpError(
            f"xanmod-sources {new_ver} exceeds stable sys-fs/zfs-{zfs_ver} MODULES_KERNEL_MAX={zfs_max}, "
            f"but template ebuild does not gate REQUIRED_USE=\"!zfs\"."
        )
    xm_m = re.search(r'^XANMOD_VERSION="([^"]+)"', eb_text, re.M)
    xm_ver = xm_m.group(1) if xm_m else "1"

    major = new_ver.split(".")[0]
    linux_dist = f"linux-{new_series}.tar.xz"
    gp_base = f"genpatches-{new_series}-{gp_ver}.base.tar.xz"
    gp_extras = f"genpatches-{new_series}-{gp_ver}.extras.tar.xz"
    xm_patch = f"patch-{new_ver}-xanmod{xm_ver}.xz"

    linux_dl = download_first_ok(
        [
            f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/{linux_dist}",
            f"https://mirrors.edge.kernel.org/pub/linux/kernel/v{major}.x/{linux_dist}",
        ],
        work / linux_dist,
    )
    gp_base_dl = download_first_ok(
        [
            f"https://dev.gentoo.org/~mpagano/dist/genpatches/{gp_base}",
            f"https://distfiles.gentoo.org/distfiles/{hashlib.blake2b(gp_base.encode()).hexdigest()[:2]}/{gp_base}",
        ],
        work / gp_base,
    )
    gp_extras_dl = download_first_ok(
        [
            f"https://dev.gentoo.org/~mpagano/dist/genpatches/{gp_extras}",
            f"https://distfiles.gentoo.org/distfiles/{hashlib.blake2b(gp_extras.encode()).hexdigest()[:2]}/{gp_extras}",
        ],
        work / gp_extras,
    )
    xm_dl = download_file(
        f"https://downloads.sourceforge.net/project/xanmod/releases/main/{new_ver}-xanmod{xm_ver}/{xm_patch}",
        work / xm_patch,
    )

    # Actually test applying every patch!
    test_dir = work / "xanmod_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    run(["tar", "-xf", str(linux_dl), "-C", str(test_dir)])
    run(["tar", "-xf", str(gp_base_dl), "-C", str(test_dir)])
    run(["tar", "-xf", str(gp_extras_dl), "-C", str(test_dir)])
    xm_raw = test_dir / f"patch-{new_ver}-xanmod{xm_ver}"
    with xm_raw.open("wb") as out:
        res = subprocess.run(["xz", "-dc", str(xm_dl)], stdout=out, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise BumpError(f"Failed to decompress {xm_patch}: {res.stderr.decode()}")

    kdir = test_dir / f"linux-{new_series}"
    run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(xm_raw)], cwd=kdir)

    local_patch = pdir / "files" / "xanmod-sources-7.2-x86-native-cache-shift.patch"
    if local_patch.exists() and local_patch.name in eb_text:
        run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(local_patch)], cwd=kdir)

    for p in sorted(test_dir.glob("10*linux*patch")):
        p.unlink()
    for p in sorted(test_dir.glob("*.patch")):
        run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(p)], cwd=kdir)

    shutil.rmtree(test_dir)

    # Write new ebuild; never drop old_eb if it is the last ZFS-compatible fallback
    new_eb_text = re.sub(r'^K_GENPATCHES_VER="[^"]+"', f'K_GENPATCHES_VER="{gp_ver}"', eb_text, count=1, flags=re.M)
    new_eb = pdir / f"xanmod-sources-{new_ver}.ebuild"
    new_eb.write_text(new_eb_text, encoding="utf-8")

    entries = parse_manifest(pdir / "Manifest")
    if not keep_old_kernel_for_zfs(pkg, old_eb, new_ver, new_eb_text, zfs_max):
        old_gp_m = re.search(r'^K_GENPATCHES_VER="([^"]+)"', eb_text, re.M)
        old_gp = old_gp_m.group(1) if old_gp_m else None
        old_eb.unlink()
        entries.pop(f"patch-{old_ver}-xanmod{xm_ver}.xz", None)
        if old_gp and old_gp != gp_ver:
            entries.pop(f"genpatches-{new_series}-{old_gp}.base.tar.xz", None)
            entries.pop(f"genpatches-{new_series}-{old_gp}.extras.tar.xz", None)

    entries[linux_dist] = dist_manifest_line(linux_dist, linux_dl)
    entries[gp_base] = dist_manifest_line(gp_base, gp_base_dl)
    entries[gp_extras] = dist_manifest_line(gp_extras, gp_extras_dl)
    entries[xm_patch] = dist_manifest_line(xm_patch, xm_dl)
    write_manifest(pdir / "Manifest", entries)


def bump_cachyos(pkg: str, new_full_ver: str, work: Path) -> None:
    """new_full_ver is e.g. '7.3.6-4' or '7.3.7-1'."""
    pdir = REPO_ROOT / pkg
    m = re.match(r"^([0-9]+\.[0-9]+\.[0-9]+)-([0-9]+)$", new_full_ver)
    if not m:
        raise BumpError(f"Unexpected cachyos version format: {new_full_ver}")
    new_pv, new_rel = m.group(1), m.group(2)

    ebuilds = list_release_ebuilds(pkg)
    new_series = ".".join(new_pv.split(".")[:2])
    same_series = [(v, eb) for v, eb in ebuilds if ".".join(v.split(".")[:2]) == new_series]
    if not same_series:
        latest_ver = ebuilds[-1][0]
        raise BumpError(
            f"cachyos-sources minor series jump ({latest_ver} -> {new_full_ver}) "
            f"requires manual review of CachyOS/{new_series} patch list and ZFS gate."
        )
    old_ver, old_eb = same_series[-1]
    old_text = old_eb.read_text(encoding="utf-8")
    old_rel_m = re.search(r'^CACHYOS_RELEASE="([^"]+)"', old_text, re.M)
    old_rel = old_rel_m.group(1) if old_rel_m else "1"
    if f"{old_ver}-{old_rel}" == new_full_ver:
        raise BumpSkip(f"{pkg} already at {new_full_ver}")

    if new_pv == old_ver:
        raise BumpError(
            f"cachyos-sources same-PV release bump ({old_ver}-{old_rel} -> {new_full_ver}) "
            f"would collide on distfile name cachyos-sources-{new_pv}.tar.gz; needs -r revision handling."
        )

    zfs_ver, zfs_max = get_gentoo_stable_zfs_info()
    if version_key(new_series) > version_key(zfs_max) and not ebuild_forbids_zfs(old_text):
        raise BumpError(
            f"cachyos-sources {new_full_ver} exceeds stable sys-fs/zfs-{zfs_ver} MODULES_KERNEL_MAX={zfs_max}, "
            f"but template ebuild does not gate REQUIRED_USE=\"!zfs\"."
        )

    gp_ver = get_gentoo_genpatches_ver(new_pv)
    if not gp_ver:
        raise BumpSkip(f"gentoo-sources-{new_pv} (genpatches) not published in Gentoo tree yet")

    cachy_src = f"cachyos-{new_pv}-{new_rel}"
    src_dist = f"cachyos-sources-{new_pv}.tar.gz"
    gp_base = f"genpatches-{new_series}-{gp_ver}.base.tar.xz"
    gp_extras = f"genpatches-{new_series}-{gp_ver}.extras.tar.xz"

    # Extract exact upstream patch URLs from the existing same-series ebuild
    patch_base = f"https://raw.githubusercontent.com/CachyOS/kernel-patches/master/{new_series}"
    aufs_m = re.search(r"misc/(0001-aufs-[^\s]+)", old_text)
    if not aufs_m:
        raise BumpError("Cannot locate AUFS patch filename in existing cachyos-sources ebuild")
    aufs_file = aufs_m.group(1)

    cachy_patches = [
        (f"cachyos-sources-{new_pv}-0001-bore-cachy.patch", f"{patch_base}/sched/0001-bore-cachy.patch"),
        (f"cachyos-sources-{new_pv}-0001-prjc-cachy.patch", f"{patch_base}/sched/0001-prjc-cachy.patch"),
        (f"cachyos-sources-{new_pv}-0001-acpi-call.patch", f"{patch_base}/misc/0001-acpi-call.patch"),
        (f"cachyos-sources-{new_pv}-0001-aufs.patch", f"{patch_base}/misc/{aufs_file}"),
        (f"cachyos-sources-{new_pv}-0001-handheld.patch", f"{patch_base}/misc/0001-handheld.patch"),
        (f"cachyos-sources-{new_pv}-0001-rt-i915.patch", f"{patch_base}/misc/0001-rt-i915.patch"),
        (f"cachyos-sources-{new_pv}-dkms-clang.patch", f"{patch_base}/misc/dkms-clang.patch"),
    ]

    src_dl = download_file(
        f"https://github.com/CachyOS/linux/releases/download/{cachy_src}/{cachy_src}.tar.gz",
        work / src_dist,
    )
    gp_base_dl = download_first_ok(
        [
            f"https://dev.gentoo.org/~mpagano/dist/genpatches/{gp_base}",
            f"https://distfiles.gentoo.org/distfiles/{hashlib.blake2b(gp_base.encode()).hexdigest()[:2]}/{gp_base}",
        ],
        work / gp_base,
    )
    gp_extras_dl = download_first_ok(
        [
            f"https://dev.gentoo.org/~mpagano/dist/genpatches/{gp_extras}",
            f"https://distfiles.gentoo.org/distfiles/{hashlib.blake2b(gp_extras.encode()).hexdigest()[:2]}/{gp_extras}",
        ],
        work / gp_extras,
    )
    patch_dls: dict[str, Path] = {}
    for dname, url in cachy_patches:
        patch_dls[dname] = download_file(url, work / dname)

    # Test both bore+rt and prjc patch sets
    for sched in ("bore", "prjc"):
        tdir = work / f"cachy_test_{sched}"
        tdir.mkdir(parents=True, exist_ok=True)
        run(["tar", "-xf", str(src_dl), "-C", str(tdir)])
        run(["tar", "-xf", str(gp_base_dl), "-C", str(tdir)])
        run(["tar", "-xf", str(gp_extras_dl), "-C", str(tdir)])
        kdir = tdir / cachy_src
        if not kdir.is_dir():
            raise BumpError(f"Expected directory {cachy_src} inside {src_dist}")
        if not list(tdir.glob(f"10*linux-{new_pv}.patch")):
            raise BumpError(f"genpatches-{new_series}-{gp_ver} is missing linux-{new_pv}.patch")
        for p in sorted(tdir.glob("10*linux*patch")):
            p.unlink()
        for p in sorted(tdir.glob("*.patch")):
            run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(p)], cwd=kdir)
        order = [
            f"cachyos-sources-{new_pv}-0001-{sched}-cachy.patch",
            f"cachyos-sources-{new_pv}-0001-acpi-call.patch",
            f"cachyos-sources-{new_pv}-0001-aufs.patch",
            f"cachyos-sources-{new_pv}-0001-handheld.patch",
        ]
        if sched == "bore":
            order.append(f"cachyos-sources-{new_pv}-0001-rt-i915.patch")
        order.append(f"cachyos-sources-{new_pv}-dkms-clang.patch")
        for dname in order:
            run(["patch", "-p1", "-s", "--no-backup-if-mismatch", "-i", str(patch_dls[dname])], cwd=kdir)
        shutil.rmtree(tdir)

    new_text = re.sub(r'^K_GENPATCHES_VER="[^"]+"', f'K_GENPATCHES_VER="{gp_ver}"', old_text, count=1, flags=re.M)
    new_text = re.sub(r'^CACHYOS_RELEASE="[^"]+"', f'CACHYOS_RELEASE="{new_rel}"', new_text, count=1, flags=re.M)
    new_eb = pdir / f"cachyos-sources-{new_pv}.ebuild"
    new_eb.write_text(new_text, encoding="utf-8")

    entries = parse_manifest(pdir / "Manifest")
    if not keep_old_kernel_for_zfs(pkg, old_eb, new_pv, new_text, zfs_max):
        old_gp_m = re.search(r'^K_GENPATCHES_VER="([^"]+)"', old_text, re.M)
        old_gp = old_gp_m.group(1) if old_gp_m else None
        old_eb.unlink()
        for k in list(entries):
            if k.startswith(f"cachyos-sources-{old_ver}"):
                entries.pop(k, None)
        if old_gp and old_gp != gp_ver:
            entries.pop(f"genpatches-{new_series}-{old_gp}.base.tar.xz", None)
            entries.pop(f"genpatches-{new_series}-{old_gp}.extras.tar.xz", None)

    entries[src_dist] = dist_manifest_line(src_dist, src_dl)
    entries[gp_base] = dist_manifest_line(gp_base, gp_base_dl)
    entries[gp_extras] = dist_manifest_line(gp_extras, gp_extras_dl)
    for dname, path in patch_dls.items():
        entries[dname] = dist_manifest_line(dname, path)
    write_manifest(pdir / "Manifest", entries)


# ---------------------------------------------------------------------------
# Dispatcher & detection
# ---------------------------------------------------------------------------


def bump_package(pkg: str, target_ver: str, *, upload_vendor: bool = False) -> dict[str, Any]:
    local_ver = get_local_version(pkg) or "unknown"
    work = Path(tempfile.mkdtemp(prefix=f"autobump-{pkg.replace('/', '_')}-"))
    try:
        actual_ver = target_ver
        if pkg in ("dev-python/pure-protobuf", "dev-python/qasync"):
            bump_pypi(pkg, target_ver, work)
        elif pkg in (
            "media-libs/libdicom",
            "media-libs/openslide",
            "media-plugins/kotonoha",
            "x11-misc/flatpak-xdg-utils",
        ):
            bump_simple_github(pkg, target_ver, work)
        elif pkg == "media-sound/cider":
            bump_cider(pkg, target_ver, work)
        elif pkg == "app-emulation/proton-ge-bin":
            bump_proton_ge(pkg, target_ver, work)
        elif pkg == "app-emulation/dwproton-bin":
            bump_dwproton(pkg, target_ver, work)
        elif pkg == "net-im/tencent-qq":
            bump_tencent_qq(pkg, target_ver, work)
        elif pkg == "net-im/wechat":
            actual_ver = bump_wechat(pkg, target_ver, work)
        elif pkg == "net-misc/tsshd":
            bump_tsshd(pkg, target_ver, work, upload_vendor=upload_vendor)
        elif pkg == "sys-kernel/xanmod-sources":
            bump_xanmod(pkg, target_ver, work)
        elif pkg == "sys-kernel/cachyos-sources":
            bump_cachyos(pkg, target_ver, work)
        else:
            raise BumpError(f"Unsupported package for autobump: {pkg}")

        return {
            "pkg": pkg,
            "slug": pkg.replace("/", "-"),
            "local_ver": local_ver,
            "target_ver": actual_ver,
            "status": "bumped",
            "message": f"Bumped {pkg}: {local_ver} -> {actual_ver}",
        }
    except BumpSkip as exc:
        return {
            "pkg": pkg,
            "slug": pkg.replace("/", "-"),
            "local_ver": local_ver,
            "target_ver": target_ver,
            "status": "skipped",
            "message": str(exc),
        }
    except Exception as exc:
        return {
            "pkg": pkg,
            "slug": pkg.replace("/", "-"),
            "local_ver": local_ver,
            "target_ver": target_ver,
            "status": "failed",
            "message": str(exc),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def detect_updates(only_pkg: str | None = None) -> dict[str, Any]:
    """Run nvchecker + WeChat HEAD probe and compare against local ebuilds."""
    old_file = Path("/tmp/nvchecker-old.json")
    new_file = Path("/tmp/nvchecker-new.json")
    old_file.write_text('{"version": 2, "data": {}}\n', encoding="utf-8")
    if new_file.exists():
        new_file.unlink()

    nv_cmd = ["nvchecker", "-c", str(NVCHECKER_CONF)]
    if only_pkg and only_pkg != "net-im/wechat":
        nv_cmd.extend(["-e", only_pkg])

    errors: list[dict[str, str]] = []
    updates: list[dict[str, str]] = []

    if only_pkg != "net-im/wechat":
        res = run(nv_cmd, check=False)
        if not new_file.exists():
            raise BumpError(f"nvchecker failed to produce output:\n{res.stderr or res.stdout}")
        data = json.loads(new_file.read_text(encoding="utf-8")).get("data", {})

        # Capture nvchecker per-package errors from stderr if any
        for line in (res.stderr or "").splitlines():
            if "failed" in line.lower() or "error" in line.lower():
                errors.append({"pkg": only_pkg or "nvchecker", "error": line.strip()})

        for pkg, info in sorted(data.items()):
            if only_pkg and pkg != only_pkg:
                continue
            upstream_ver = info.get("version")
            if not upstream_ver:
                continue
            local_ver = get_local_version(pkg)
            if not local_ver:
                continue
            if version_key(upstream_ver) > version_key(local_ver):
                updates.append(
                    {
                        "pkg": pkg,
                        "slug": pkg.replace("/", "-"),
                        "local_ver": local_ver,
                        "upstream_ver": upstream_ver,
                    }
                )

    if only_pkg in (None, "net-im/wechat"):
        try:
            wx = detect_wechat_update()
            if wx:
                local_ver, probe_tag = wx
                updates.append(
                    {
                        "pkg": "net-im/wechat",
                        "slug": "net-im-wechat",
                        "local_ver": local_ver,
                        "upstream_ver": probe_tag,
                    }
                )
        except Exception as exc:
            errors.append({"pkg": "net-im/wechat", "error": str(exc)})

    # Notify if Gentoo stable sys-fs/zfs now supports a kernel series currently gated by !zfs
    if only_pkg in (None, "sys-kernel/xanmod-sources", "sys-kernel/cachyos-sources"):
        try:
            zfs_ver, zfs_max = get_gentoo_stable_zfs_info()
            for kpkg in ("sys-kernel/xanmod-sources", "sys-kernel/cachyos-sources"):
                if only_pkg and only_pkg != kpkg:
                    continue
                for ver, eb in list_release_ebuilds(kpkg):
                    series = ".".join(ver.split(".")[:2])
                    if ebuild_forbids_zfs(eb.read_text(encoding="utf-8")) and version_key(series) <= version_key(zfs_max):
                        errors.append(
                            {
                                "pkg": kpkg,
                                "error": (
                                    f"Gentoo stable sys-fs/zfs-{zfs_ver} now declares MODULES_KERNEL_MAX={zfs_max}, "
                                    f"which covers {kpkg}-{ver}. You can now unlock USE=zfs on the {series} series!"
                                ),
                            }
                        )
        except Exception as exc:
            errors.append({"pkg": "sys-fs/zfs-check", "error": str(exc)})

    return {"updates": updates, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="locez-overlay automated package bumper")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--detect", action="store_true", help="Detect outdated packages as JSON")
    mode.add_argument("--pkg", help="Bump a single package (category/name)")
    mode.add_argument("--all", action="store_true", help="Detect and bump all outdated packages")
    parser.add_argument("--only", help="Filter --detect to a single package")
    parser.add_argument("--target", help="Target version for --pkg (auto-detected if omitted)")
    parser.add_argument("--upload-vendor", action="store_true", help="Upload Go vendor tarball to GitHub Releases")
    parser.add_argument("--result-file", help="Write JSON result to this file")
    args = parser.parse_args()

    if args.detect:
        out = detect_updates(only_pkg=args.only)
        text = json.dumps(out, indent=2)
        if args.result_file:
            Path(args.result_file).write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0

    if args.pkg:
        target = args.target
        if not target:
            det = detect_updates(only_pkg=args.pkg)
            if det["updates"]:
                target = det["updates"][0]["upstream_ver"]
            else:
                result = {
                    "pkg": args.pkg,
                    "slug": args.pkg.replace("/", "-"),
                    "local_ver": get_local_version(args.pkg) or "unknown",
                    "target_ver": get_local_version(args.pkg) or "unknown",
                    "status": "skipped",
                    "message": f"{args.pkg} is already up-to-date",
                }
                text = json.dumps(result, indent=2)
                if args.result_file:
                    Path(args.result_file).write_text(text + "\n", encoding="utf-8")
                print(text)
                return 0
        result = bump_package(args.pkg, target, upload_vendor=args.upload_vendor)
        text = json.dumps(result, indent=2)
        if args.result_file:
            Path(args.result_file).write_text(text + "\n", encoding="utf-8")
        print(text)
        return 1 if result["status"] == "failed" else 0

    # --all
    det = detect_updates()
    results = []
    any_failed = bool(det["errors"])
    for item in det["updates"]:
        res = bump_package(item["pkg"], item["upstream_ver"], upload_vendor=args.upload_vendor)
        results.append(res)
        if res["status"] == "failed":
            any_failed = True
    payload = {"results": results, "detect_errors": det["errors"]}
    text = json.dumps(payload, indent=2)
    if args.result_file:
        Path(args.result_file).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
