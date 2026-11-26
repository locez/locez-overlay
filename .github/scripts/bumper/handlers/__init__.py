"""Package handler registry and unified bump_package() dispatcher."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..common import BumpError, BumpSkip, get_local_version
from .go_vendor import bump_tsshd
from .kernel import bump_cachyos, bump_xanmod
from .proton import bump_dwproton, bump_proton_ge
from .simple import bump_cider, bump_pypi, bump_simple_github
from .tencent import bump_tencent_qq, bump_wechat

Handler = Callable[[str, str, Path], str | None]

HANDLERS: dict[str, Handler] = {
    "dev-python/pure-protobuf": bump_pypi,
    "dev-python/qasync": bump_pypi,
    "media-libs/libdicom": bump_simple_github,
    "media-libs/openslide": bump_simple_github,
    "media-plugins/kotonoha": bump_simple_github,
    "x11-misc/flatpak-xdg-utils": bump_simple_github,
    "media-sound/cider": bump_cider,
    "app-emulation/proton-ge-bin": bump_proton_ge,
    "app-emulation/dwproton-bin": bump_dwproton,
    "net-im/tencent-qq": bump_tencent_qq,
    "net-im/wechat": bump_wechat,
    "sys-kernel/xanmod-sources": bump_xanmod,
    "sys-kernel/cachyos-sources": bump_cachyos,
}


def bump_package(pkg: str, target_ver: str, *, upload_vendor: bool = False) -> dict[str, Any]:
    local_ver = get_local_version(pkg) or "unknown"
    work = Path(tempfile.mkdtemp(prefix=f"autobump-{pkg.replace('/', '_')}-"))
    try:
        actual_ver: str | None = None
        if pkg == "net-misc/tsshd":
            bump_tsshd(pkg, target_ver, work, upload_vendor=upload_vendor)
        elif pkg in HANDLERS:
            actual_ver = HANDLERS[pkg](pkg, target_ver, work)
        else:
            raise BumpError(f"Unsupported package for autobump: {pkg}")

        final_ver = actual_ver or target_ver
        return {
            "pkg": pkg,
            "slug": pkg.replace("/", "-"),
            "local_ver": local_ver,
            "target_ver": final_ver,
            "status": "bumped",
            "message": f"Bumped {pkg}: {local_ver} -> {final_ver}",
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
