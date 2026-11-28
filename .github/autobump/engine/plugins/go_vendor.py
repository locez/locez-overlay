"""Generic Go vendor/deps tarball bump strategy."""

from __future__ import annotations

from .base import BumpContext
from .vendor import bump_vendor_package


def bump_go_vendor(ctx: BumpContext) -> None:
    """Bump a Go package by building/reusing ${P}-deps.tar.xz in distfiles_repo."""
    bump_vendor_package(ctx, kind="go")
