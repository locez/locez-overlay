"""Strategies for multi-arch proprietary Linux desktop .deb apps (Feishu, DingTalk)."""

from __future__ import annotations

import json
import re
import shutil

from ..common import (
    BumpError,
    BumpSkip,
    dist_manifest_line,
    download_file,
    http_get_redirect_location,
    http_get_text,
    list_release_ebuilds,
    write_manifest,
)
from .base import BumpContext

FEISHU_API_URL = "https://www.feishu.cn/api/downloads"
DINGTALK_AMD64_REDIRECT = "https://www.dingtalk.com/win/d/qd=linux_amd64"
DINGTALK_ARM64_REDIRECT = "https://www.dingtalk.com/win/d/qd=linux_arm64"
WPS_LINUX_PAGE = "https://linux.wps.cn/"
WPS_PROXY_PREFIX = "https://wps302.wcbing.top/"


def bump_feishu(ctx: BumpContext) -> str:
    """Bump app-office/bytedance-feishu using the official Feishu downloads JSON API."""
    pdir = ctx.pkg_dir
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == ctx.target_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {ctx.target_ver}")

    data = json.loads(http_get_text(FEISHU_API_URL))
    versions = data.get("versions", {})
    x64_url = versions.get("Linux_deb_x64", {}).get("download_link", "")
    arm_url = versions.get("Linux_deb_arm", {}).get("download_link", "")
    if not x64_url or not arm_url:
        raise BumpError("Missing Linux_deb_x64 or Linux_deb_arm download_link in Feishu API response")

    m_x64 = re.search(r"/Feishu-linux_x64-([0-9]+(?:\.[0-9]+)+)\.deb$", x64_url)
    m_arm = re.search(r"/Feishu-linux_arm64-([0-9]+(?:\.[0-9]+)+)\.deb$", arm_url)
    if not m_x64 or not m_arm:
        raise BumpError(f"Unexpected Feishu download URLs: x64={x64_url}, arm64={arm_url}")
    new_ver = m_x64.group(1)
    if m_arm.group(1) != new_ver:
        raise BumpSkip(f"Feishu arm64 ({m_arm.group(1)}) lags behind amd64 ({new_ver}); waiting for sync")
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {new_ver}")

    x64_dist = f"Feishu-linux_x64-{new_ver}.deb"
    arm_dist = f"Feishu-linux_arm64-{new_ver}.deb"
    x64_dl = download_file(x64_url, ctx.work / x64_dist)
    arm_dl = download_file(arm_url, ctx.work / arm_dist)

    x64_tpl = x64_url.replace(f"-{new_ver}.deb", "-${PV}.deb")
    arm_tpl = arm_url.replace(f"-{new_ver}.deb", "-${PV}.deb")

    eb_text = old_eb.read_text(encoding="utf-8")
    eb_new = re.sub(r"https://[^\s)]+/Feishu-linux_x64-\$\{PV\}\.deb", x64_tpl, eb_text, count=1)
    eb_new = re.sub(r"https://[^\s)]+/Feishu-linux_arm64-\$\{PV\}\.deb", arm_tpl, eb_new, count=1)
    if x64_tpl not in eb_new or arm_tpl not in eb_new:
        raise BumpError("Failed to update amd64/arm64 SRC_URI URLs in bytedance-feishu ebuild")

    new_eb = pdir / f"{ctx.spec.pn}-{new_ver}.ebuild"
    new_eb.write_text(eb_new, encoding="utf-8")
    for _, eb in ebuilds:
        if eb != new_eb:
            eb.unlink()

    write_manifest(
        pdir / "Manifest",
        {
            x64_dist: dist_manifest_line(x64_dist, x64_dl),
            arm_dist: dist_manifest_line(arm_dist, arm_dl),
        },
    )
    return new_ver


def bump_dingtalk(ctx: BumpContext) -> str:
    """Bump net-im/dingtalk using official amd64/arm64 Linux deb download redirects."""
    pdir = ctx.pkg_dir
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == ctx.target_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {ctx.target_ver}")

    amd64_url = http_get_redirect_location(DINGTALK_AMD64_REDIRECT)
    arm64_url = http_get_redirect_location(DINGTALK_ARM64_REDIRECT)

    m_amd64 = re.search(r"com\.alibabainc\.dingtalk_([0-9]+(?:\.[0-9]+)+)_amd64\.deb$", amd64_url)
    m_arm64 = re.search(r"com\.alibabainc\.dingtalk_([0-9]+(?:\.[0-9]+)+)_arm64\.deb$", arm64_url)
    if not m_amd64 or not m_arm64:
        raise BumpError(f"Unexpected DingTalk redirect URLs: amd64={amd64_url}, arm64={arm64_url}")

    new_ver = m_amd64.group(1)
    if m_arm64.group(1) != new_ver:
        raise BumpSkip(f"DingTalk arm64 ({m_arm64.group(1)}) lags behind amd64 ({new_ver}); waiting for sync")
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {new_ver}")

    amd64_dist = f"com.alibabainc.{ctx.spec.pn}_{new_ver}_amd64.deb"
    arm64_dist = f"com.alibabainc.{ctx.spec.pn}_{new_ver}_arm64.deb"
    amd64_dl = download_file(amd64_url, ctx.work / amd64_dist)
    arm64_dl = download_file(arm64_url, ctx.work / arm64_dist)

    new_eb = pdir / f"{ctx.spec.pn}-{new_ver}.ebuild"
    shutil.copy2(old_eb, new_eb)
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


def bump_wps_office(ctx: BumpContext) -> str:
    """Bump app-office/wps-office (12.x Chinese personal edition) via linux.wps.cn."""
    pdir = ctx.pkg_dir
    ebuilds = list_release_ebuilds(ctx.repo_root, ctx.spec.pkg)
    old_ver, old_eb = ebuilds[-1]
    if re.sub(r"-r[0-9]+$", "", old_ver) == ctx.target_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {ctx.target_ver}")

    html = http_get_text(WPS_LINUX_PAGE)
    m = re.search(
        r"wps-office_([0-9]+(?:\.[0-9]+)+)\.AK\.preread\.sw\.Personal_([0-9]+)_amd64\.deb",
        html,
    )
    if not m:
        raise BumpError("Could not parse WPS Office Linux Personal amd64 deb filename from linux.wps.cn")
    new_ver, build_id = m.group(1), m.group(2)
    if re.sub(r"-r[0-9]+$", "", old_ver) == new_ver:
        raise BumpSkip(f"{ctx.spec.pkg} already at {new_ver}")

    build_num = new_ver.rsplit(".", 1)[-1]
    upstream_url = (
        f"https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/{build_num}/"
        f"wps-office_{new_ver}.AK.preread.sw.Personal_{build_id}_amd64.deb"
    )
    distname = f"{ctx.spec.pn}_{new_ver}_amd64.deb"
    dl = download_file(WPS_PROXY_PREFIX + upstream_url, ctx.work / distname)

    eb_text = old_eb.read_text(encoding="utf-8")
    eb_new = re.sub(r'^MY_BUILD_ID="[^"]+"', f'MY_BUILD_ID="{build_id}"', eb_text, count=1, flags=re.M)
    if f'MY_BUILD_ID="{build_id}"' not in eb_new:
        raise BumpError("Failed to update MY_BUILD_ID in wps-office ebuild")

    new_eb = pdir / f"{ctx.spec.pn}-{new_ver}.ebuild"
    new_eb.write_text(eb_new, encoding="utf-8")
    for _, eb in ebuilds:
        if eb != new_eb:
            eb.unlink()

    write_manifest(pdir / "Manifest", {distname: dist_manifest_line(distname, dl)})
    return new_ver
