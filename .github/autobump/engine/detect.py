"""Overlay-agnostic update detection driven by metadata/autobump.toml."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .common import BumpError, detect_ebuild_arches, get_local_version, list_release_ebuilds, run, version_key
from .plugins.kernel import get_gentoo_stable_zfs_info
from .plugins.tencent import detect_wechat_update
from .retention import ebuild_forbids_zfs
from .schema import AutomationConfig, generate_nvchecker_toml


def _fetch_open_failure_titles(cfg: AutomationConfig) -> list[str]:
    repo = cfg.overlay.repository
    label = cfg.notify.github_label
    if not repo or not cfg.notify.github_issue:
        return []
    res = run(
        [
            "gh",
            "issue",
            "list",
            "-R",
            repo,
            "--state",
            "open",
            "--label",
            label,
            "--limit",
            "100",
            "--json",
            "title",
        ],
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        return [str(item.get("title", "")) for item in json.loads(res.stdout)]
    except json.JSONDecodeError:
        return []


def filter_throttled_updates(
    updates: list[dict[str, str]],
    open_issue_titles: list[str],
    label: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Skip updates whose exact (pkg, upstream_ver) already has an open failure issue."""
    kept: list[dict[str, str]] = []
    throttled: list[dict[str, str]] = []
    for item in updates:
        prefix = f"[{label}] {item['pkg']}:"
        ver_re = re.compile(rf"->\s+{re.escape(item['upstream_ver'])}(?:\s|\(|$)")
        if any(t.startswith(prefix) and ver_re.search(t) for t in open_issue_titles):
            throttled.append({**item, "reason": "open failure issue already exists for this version"})
        else:
            kept.append(item)
    return kept, throttled


def detect_updates(
    cfg: AutomationConfig,
    *,
    only_pkg: str | None = None,
    throttle_failed: bool = False,
    skip_weekly: bool = False,
) -> dict[str, Any]:
    """Run nvchecker (generated from autobump.toml), custom probes, and ZFS unlock checks."""
    updates: list[dict[str, str]] = []
    throttled: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    zfs_alerts: list[dict[str, str]] = []

    def _cadence_active(pkg_spec: Any) -> bool:
        return bool(only_pkg) or not (skip_weekly and pkg_spec.cadence == "weekly")

    nv_pkgs = [
        pkg
        for pkg, spec in sorted(cfg.packages.items())
        if spec.detect.get("type") == "nvchecker"
        and (not only_pkg or pkg == only_pkg)
        and _cadence_active(spec)
    ]

    if nv_pkgs:
        with tempfile.TemporaryDirectory(prefix="nvchecker-") as tmp:
            tdir = Path(tmp)
            conf_path = generate_nvchecker_toml(
                cfg, tdir / "nvchecker.toml", tdir / "old.json", tdir / "new.json", only_pkgs=set(nv_pkgs)
            )
            (tdir / "old.json").write_text('{"version": 2, "data": {}}\n', encoding="utf-8")
            nv_cmd = ["nvchecker", "-c", str(conf_path)]
            if only_pkg:
                nv_cmd.extend(["-e", only_pkg])
            nv_env = dict(os.environ)
            gh_tok = nv_env.get("NVCHECKER_GITHUB_TOKEN") or nv_env.get("GITHUB_TOKEN") or nv_env.get("GH_TOKEN")
            if gh_tok:
                nv_env["NVCHECKER_GITHUB_TOKEN"] = gh_tok
            res = run(nv_cmd, env=nv_env, check=False)
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
                    arches = detect_ebuild_arches(cfg.repo_root, pkg, configured_arches=spec.build.arches)
                    updates.append(
                        {
                            "pkg": pkg,
                            "slug": spec.slug,
                            "strategy": spec.strategy,
                            "local_ver": local_ver,
                            "upstream_ver": upstream_ver,
                            "policy": spec.policy,
                            "arm64": "arm64" in arches,
                        }
                    )

    for pkg, spec in sorted(cfg.packages.items()):
        if only_pkg and pkg != only_pkg:
            continue
        if not _cadence_active(spec):
            continue
        if spec.detect.get("type") == "wechat-head":
            try:
                wx = detect_wechat_update(cfg, spec)
                if wx:
                    local_ver, probe_tag = wx
                    arches = detect_ebuild_arches(cfg.repo_root, pkg, configured_arches=spec.build.arches)
                    updates.append(
                        {
                            "pkg": pkg,
                            "slug": spec.slug,
                            "strategy": spec.strategy,
                            "local_ver": local_ver,
                            "upstream_ver": probe_tag,
                            "policy": spec.policy,
                            "arm64": "arm64" in arches,
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

    if throttle_failed and updates:
        open_titles = _fetch_open_failure_titles(cfg)
        updates, throttled = filter_throttled_updates(updates, open_titles, cfg.notify.github_label)

    return {"updates": updates, "throttled": throttled, "errors": errors, "zfs_alerts": zfs_alerts}
