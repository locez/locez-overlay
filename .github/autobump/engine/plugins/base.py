"""Core Strategy Protocol and BumpContext shared by all bump plugins."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..schema import AutomationConfig, PackageSpec


@dataclass(frozen=True)
class BumpContext:
    cfg: AutomationConfig
    spec: PackageSpec
    target_ver: str
    work: Path
    upload_vendor: bool = False

    @property
    def repo_root(self) -> Path:
        return self.cfg.repo_root

    @property
    def pkg_dir(self) -> Path:
        return self.cfg.repo_root / self.spec.pkg


class Strategy(Protocol):
    def __call__(self, ctx: BumpContext) -> str | None:
        """Apply bump for ctx; optionally return resolved final version string."""
        ...
