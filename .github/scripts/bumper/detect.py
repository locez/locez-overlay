"""Upstream version detection: nvchecker + WeChat HEAD probe + Gentoo stable ZFS check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import (
    NVCHECKER_CONF,
    BumpError,
    get_local_version,
    list_release_ebuilds,
    run,
    version_key,
)
from .handlers.kernel import ebuild_forbids_zfs, get_gentoo_stable_zfs_info
from .handlers.tencent import detect_wechat_update


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

        for line in (res.stderr or "").splitlines():
            if "failed" in line.lower() or "error" in line.lower():
                errors.append({"pkg": only_pkg or "nvchecker", "error": line.strip()})

        for pkg, info in sorted(data.items()):
            if only_pkg and pkg != only_pkg:
                continue
            upstream_ver = info.get("version")
            local_ver = get_local_version(pkg)
            if not upstream_ver or not local_ver:
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
