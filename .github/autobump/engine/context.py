"""Structured FailureContext bundle for notifications and AI repair agents."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .common import list_release_ebuilds


@dataclass
class FailureContext:
    pkg: str
    stage: str  # detect | bump | pkgcheck | build-test | pr-merge | weekly-health
    local_ver: str
    target_ver: str
    summary: str
    run_url: str = ""
    ebuild_path: str = ""
    ebuild_content: str = ""
    git_diff: str = ""
    logs: dict[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path

    @classmethod
    def from_file(cls, path: Path) -> FailureContext:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)


def _select_latest_ebuild(repo_root: Path, pkg: str) -> Path | None:
    releases = list_release_ebuilds(repo_root, pkg)
    if releases:
        return releases[-1][1]
    pn = pkg.split("/", 1)[1]
    live = repo_root / pkg / f"{pn}-9999.ebuild"
    return live if live.is_file() else None


def collect_failure_context(
    *,
    repo_root: Path,
    pkg: str,
    stage: str,
    local_ver: str,
    target_ver: str,
    summary: str,
    run_url: str = "",
    log_files: dict[str, Path] | None = None,
    max_log_chars: int = 8000,
) -> FailureContext:
    ebuild_path = ""
    ebuild_content = ""
    latest = _select_latest_ebuild(repo_root, pkg)
    if latest is not None:
        ebuild_path = str(latest.relative_to(repo_root))
        ebuild_content = latest.read_text(encoding="utf-8", errors="replace")[:12000]

    git_diff = ""
    try:
        res = subprocess.run(
            ["git", "diff", "--", pkg],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
        )
        if res.returncode == 0:
            git_diff = res.stdout[:8000]
    except (OSError, subprocess.SubprocessError):
        pass

    logs: dict[str, str] = {}
    for name, lpath in (log_files or {}).items():
        if lpath.is_file():
            text = lpath.read_text(encoding="utf-8", errors="replace")
            logs[name] = text[-max_log_chars:]

    return FailureContext(
        pkg=pkg,
        stage=stage,
        local_ver=local_ver,
        target_ver=target_ver,
        summary=summary,
        run_url=run_url,
        ebuild_path=ebuild_path,
        ebuild_content=ebuild_content,
        git_diff=git_diff,
        logs=logs,
    )
