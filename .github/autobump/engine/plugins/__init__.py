"""Built-in strategy registry and unified bump_package() dispatcher."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..common import BumpError, BumpSkip, get_local_version, parse_manifest
from ..schema import AutomationConfig
from .base import BumpContext, Strategy
from .cn_apps import bump_dingtalk, bump_feishu, bump_wps_office
from .go_vendor import bump_go_vendor
from .kernel import bump_cachyos, bump_xanmod
from .proton import bump_dwproton, bump_proton_ge
from .rust import bump_rust_crates, bump_rust_vendor
from .simple import bump_cider, bump_github_release, bump_pypi
from .tencent import bump_qqmusic, bump_tencent_qq, bump_wechat
from .zig import bump_zig_vendor

__all__ = ["BumpContext", "STRATEGIES", "Strategy", "bump_package"]

STRATEGIES: dict[str, Strategy] = {
    "pypi": bump_pypi,
    "github-release": bump_github_release,
    "cider": bump_cider,
    "proton-ge": bump_proton_ge,
    "dwproton": bump_dwproton,
    "tencent-qq": bump_tencent_qq,
    "qqmusic": bump_qqmusic,
    "wechat": bump_wechat,
    "feishu": bump_feishu,
    "dingtalk": bump_dingtalk,
    "wps-office": bump_wps_office,
    "go-vendor": bump_go_vendor,
    "rust-vendor": bump_rust_vendor,
    "rust-crates": bump_rust_crates,
    "zig-vendor": bump_zig_vendor,
    "kernel-xanmod": bump_xanmod,
    "kernel-cachyos": bump_cachyos,
}


def bump_package(
    cfg: AutomationConfig,
    pkg: str,
    target_ver: str,
    *,
    upload_vendor: bool = False,
    distdir: Path | None = None,
) -> dict[str, Any]:
    spec = cfg.get_package(pkg)
    local_ver = get_local_version(cfg.repo_root, pkg, spec.strategy) or "unknown"
    work = Path(tempfile.mkdtemp(prefix=f"autobump-{spec.slug}-"))
    try:
        strategy_fn = STRATEGIES.get(spec.strategy)
        if strategy_fn is None:
            raise BumpError(f"Unsupported or empty strategy '{spec.strategy}' for {pkg}")

        ctx = BumpContext(
            cfg=cfg,
            spec=spec,
            target_ver=target_ver,
            work=work,
            upload_vendor=upload_vendor,
        )
        resolved_ver = strategy_fn(ctx) or target_ver
        if distdir is not None:
            distdir.mkdir(parents=True, exist_ok=True)
            manifest_dists = parse_manifest(ctx.pkg_dir / "Manifest")
            for distname in manifest_dists:
                src_file = work / distname
                if src_file.is_file():
                    shutil.move(str(src_file), str(distdir / distname))
        return {
            "pkg": pkg,
            "slug": spec.slug,
            "strategy": spec.strategy,
            "local_ver": local_ver,
            "target_ver": resolved_ver,
            "policy": spec.policy,
            "status": "bumped",
            "message": f"Bumped {pkg}: {local_ver} -> {resolved_ver}",
        }
    except BumpSkip as exc:
        return {
            "pkg": pkg,
            "slug": spec.slug,
            "strategy": spec.strategy,
            "local_ver": local_ver,
            "target_ver": target_ver,
            "policy": spec.policy,
            "status": "skipped",
            "message": str(exc),
        }
    except Exception as exc:
        return {
            "pkg": pkg,
            "slug": spec.slug,
            "strategy": spec.strategy,
            "local_ver": local_ver,
            "target_ver": target_ver,
            "policy": spec.policy,
            "status": "failed",
            "message": str(exc),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
