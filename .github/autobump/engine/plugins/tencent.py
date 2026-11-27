"""Strategies for Tencent Linux desktop packages (tencent-qq, wechat)."""

from __future__ import annotations

import json
import re
import shutil

from ..common import (
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    extract_deb_control_field,
    get_local_version,
    http_get_text,
    http_head,
    list_release_ebuilds,
    parse_manifest,
    version_key,
    write_manifest,
)
from ..schema import AutomationConfig, PackageSpec
from .base import BumpContext

WECHAT_X64_URL = "https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb"
WECHAT_ARM64_URL = "https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_arm64.deb"
QQ_CONFIG_URL = "https://cdn-go.cn/qq-web/im.qq.com_new/latest/rainbow/linuxConfig.js"
QQ_PROXY_PREFIX = "https://dlqq.akkariin.moe/"


def bump_tencent_qq(ctx: BumpContext) -> str:
    pdir = ctx.pkg_dir
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == ctx.target_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {ctx.target_ver}")

    js = http_get_text(QQ_CONFIG_URL)
    m = re.search(r"var\s+params\s*=\s*(\{.*?\});", js, re.S)
    if not m:
        raise BumpError("Failed to parse params JSON from Tencent QQ linuxConfig.js")
    params = json.loads(m.group(1))
    amd64_url = params["x64DownloadUrl"]["deb"]
    arm64_url = params["armDownloadUrl"]["deb"]

    url_m = re.match(r"^(https://[^/]+/.+?)/QQ_([0-9.]+_[0-9]+)_amd64_01\.deb$", amd64_url)
    if not url_m:
        raise BumpError(f"Unexpected Tencent QQ amd64 URL format: {amd64_url}")
    base_url = url_m.group(1)
    new_ver = re.sub(r"_([0-9]+)$", r"_p\1", url_m.group(2))

    amd64_dist = f"{ctx.spec.pn}-{new_ver}_amd64.deb"
    arm64_dist = f"{ctx.spec.pn}-{new_ver}_arm64.deb"
    amd64_dl = download_file(QQ_PROXY_PREFIX + amd64_url, ctx.work / amd64_dist)
    arm64_dl = download_file(QQ_PROXY_PREFIX + arm64_url, ctx.work / arm64_dist)

    deb_ver = extract_deb_control_field(amd64_dl, "Version", ctx.work)
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

    new_eb = pdir / f"{ctx.spec.pn}-{new_ver}.ebuild"
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
    return new_ver


def detect_wechat_update(cfg: AutomationConfig, spec: PackageSpec) -> tuple[str, str] | None:
    pdir = cfg.repo_root / spec.pkg
    local_ver = get_local_version(cfg.repo_root, spec.pkg, spec.strategy)
    if not local_ver:
        return None
    manifest = parse_manifest(pdir / "Manifest")
    cur_dist = f"{spec.pn}-{local_ver}_x86_64.deb"
    cur_size = None
    if cur_dist in manifest:
        parts = manifest[cur_dist].split()
        if len(parts) >= 3 and parts[2].isdigit():
            cur_size = int(parts[2])
    hdrs = http_head(WECHAT_X64_URL)
    cl = hdrs.get("content-length", "")
    remote_size = int(cl) if cl.isdigit() else None
    if remote_size is not None and cur_size is not None and remote_size != cur_size:
        return (local_ver, f"size-{remote_size}")
    return None


def bump_wechat(ctx: BumpContext) -> str:
    pdir = ctx.pkg_dir
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    old_base = re.sub(r"-r[0-9]+$", "", old_ver)

    x64_tmp = download_file(WECHAT_X64_URL, ctx.work / "WeChatLinux_x86_64.deb")
    new_ver = extract_deb_control_field(x64_tmp, "Version", ctx.work)
    if new_ver == old_base:
        raise BumpError(
            f"WeChat upstream silent repack detected: Version is still {new_ver} "
            f"but Content-Length changed to {x64_tmp.stat().st_size} bytes. Manual check required."
        )
    if version_key(new_ver) <= version_key(old_base):
        raise BumpError(f"WeChat upstream version {new_ver} is not newer than local {old_base}")

    arm64_tmp = download_file(WECHAT_ARM64_URL, ctx.work / "WeChatLinux_arm64.deb")
    arm_ver = extract_deb_control_field(arm64_tmp, "Version", ctx.work)
    if arm_ver != new_ver:
        raise BumpError(f"WeChat arch version mismatch: x86_64={new_ver}, arm64={arm_ver}")

    x64_dist = f"{ctx.spec.pn}-{new_ver}_x86_64.deb"
    arm64_dist = f"{ctx.spec.pn}-{new_ver}_arm64.deb"

    new_eb = pdir / f"{ctx.spec.pn}-{new_ver}.ebuild"
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
