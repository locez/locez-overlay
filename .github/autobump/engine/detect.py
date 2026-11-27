"""Overlay-agnostic update detection driven by metadata/autobump.toml."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .common import BumpError, get_local_version, list_release_ebuilds, run, version_key
from .plugins.kernel import get_gentoo_stable_zfs_info
from .plugins.tencent import detect_wechat_update
from .retention import ebuild_forbids_zfs
from .schema import AutomationConfig, generate_nvchecker_toml


def detect_updates(cfg: AutomationConfig, *, only_pkg: str | None = None) -> dict[str, Any]:
    """Run nvchecker (generated from autobump.toml), custom probes, and ZFS unlock checks."""
    updates: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    zfs_alerts: list[dict[str, str]] = []

    nv_pkgs = [
        pkg
        for pkg, spec in sorted(cfg.packages.items())
        if spec.detect.get("type") == "nvchecker" and (not only_pkg or pkg == only_pkg)
    ]

    if nv_pkgs:
        with tempfile.TemporaryDirectory(prefix="nvchecker-") as tmp:
            tdir = Path(tmp)
            conf_path = generate_nvchecker_toml(cfg, tdir / "nvchecker.toml", tdir / "old.json", tdir / "new.json")
            (tdir / "old.json").write_text('{"version": 2, "data": {}}\n', encoding="utf-8")
            nv_cmd = ["nvchecker", "-c", str(conf_path)]
            if only_pkg:
                nv_cmd.extend(["-e", only_pkg])
            res = run(nv_cmd, check=False)
            new_file = tdir / "new.json"
            if not new_file.exists():
                raise BumpError(f"nvchecker failed to produce output:\n{res.stderr or res.stdout}")
            data = json.loads(new_file.read_text(encoding="utf-8")).get("data", {})

            for line in (res.stderr or "").splitlines():
                if "failed" in line.lower() or "error" in line.lower():
                    errors.append({"pkg": only_pkg or "nvchecker", "error": line.strip()})

            for pkg in nv_pkgs:
                spec = cfg.packages[pkg]
                upstream_ver = data.get(pkg, {}).get("version")
                local_ver = get_local_version(cfg.repo_root, pkg, spec.strategy)
                if not upstream_ver or not local_ver:
                    continue
                if version_key(upstream_ver) > version_key(local_ver):
                    updates.append(
                        {
                            "pkg": pkg,
                            "slug": spec.slug,
                            "strategy": spec.strategy,
                            "local_ver": local_ver,
                            "upstream_ver": upstream_ver,
                            "policy": spec.policy,
                        }
                    )

    for pkg, spec in sorted(cfg.packages.items()):
        if only_pkg and pkg != only_pkg:
            continue
        if spec.detect.get("type") == "wechat-head":
            try:
                wx = detect_wechat_update(cfg, spec)
                if wx:
                    local_ver, probe_tag = wx
                    updates.append(
                        {
                            "pkg": pkg,
                            "slug": spec.slug,
                            "strategy": spec.strategy,
                            "local_ver": local_ver,
                            "upstream_ver": probe_tag,
                            "policy": spec.policy,
                        }
                    )
            except Exception as exc:
                errors.append({"pkg": pkg, "error": str(exc)})

    zfs_guarded_pkgs = [
        pkg
        for pkg, spec in sorted(cfg.packages.items())
        if spec.retention.guard == "keep-last-zfs-compatible" and (not only_pkg or pkg == only_pkg)
    ]
    if zfs_guarded_pkgs:
        try:
            zfs_ver, zfs_max = get_gentoo_stable_zfs_info()
            for kpkg in zfs_guarded_pkgs:
                for ver, eb in list_release_ebuilds(cfg.repo_root, kpkg):
                    series = ".".join(ver.split(".")[:2])
                    if ebuild_forbids_zfs(eb.read_text(encoding="utf-8")) and version_key(series) <= version_key(zfs_max):
                        zfs_alerts.append(
                            {
                                "pkg": kpkg,
                                "zfs_ver": zfs_ver,
                                "zfs_max": zfs_max,
                                "message": (
                                    f"Gentoo stable sys-fs/zfs-{zfs_ver} now declares MODULES_KERNEL_MAX={zfs_max}, "
                                    f"which covers {kpkg}-{ver}. You can now unlock USE=zfs on the {series} series."
                                ),
                            }
                        )
        except Exception as exc:
            errors.append({"pkg": "sys-fs/zfs-check", "error": str(exc)})

    return {"updates": updates, "errors": errors, "zfs_alerts": zfs_alerts}
