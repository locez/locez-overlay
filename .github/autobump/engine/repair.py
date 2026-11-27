"""Pluggable AI Repair Runner with strict file-scope guardrails.

Supports any external agent CLI (e.g. `merry`, Claude Code, Gemini CLI, Codex) via
a simple command template:
  AI_REPAIR_CMD="merry fix --context {context_file} --repo {repo_root}"

Guardrails enforced regardless of which agent runs:
  1. Agent may ONLY modify files under `<cat>/<pkg>/` plus configured `allowed_extra_paths`.
  2. Any out-of-scope edit causes immediate revert + failure.
  3. Caller must pass `pkgcheck` + `build-test` before opening a Draft PR.
  4. AI Repair NEVER auto-merges into base_branch.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .common import BumpError, run
from .context import FailureContext
from .schema import AutomationConfig


def changed_files_in_repo(repo_root: Path) -> list[str]:
    """Return all modified, staged, or untracked relative file paths."""
    res = run(["git", "status", "--porcelain"], cwd=repo_root)
    paths: list[str] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        # Porcelain format: XY <path> or XY <old> -> <new>
        raw = line[3:].strip()
        if " -> " in raw:
            old_p, new_p = raw.split(" -> ", 1)
            paths.extend([old_p.strip('"'), new_p.strip('"')])
        else:
            paths.append(raw.strip('"'))
    return sorted(set(paths))


def validate_allowed_changes(
    changed_paths: list[str],
    pkg: str,
    allowed_extra_paths: tuple[str, ...],
) -> list[str]:
    """Return list of disallowed paths (empty list means all changes are within scope)."""
    allowed_prefixes = (f"{pkg.rstrip('/')}/", *(p.rstrip("/") + "/" for p in allowed_extra_paths))
    disallowed = []
    for p in changed_paths:
        if not any(p.startswith(prefix) for prefix in allowed_prefixes):
            disallowed.append(p)
    return disallowed


def run_ai_repair(
    cfg: AutomationConfig,
    context_file: Path,
    *,
    command_override: str = "",
) -> dict[str, Any]:
    ctx = FailureContext.from_file(context_file)
    cmd_template = (
        command_override
        or os.environ.get("AI_REPAIR_CMD", "").strip()
        or cfg.ai_repair.command.strip()
    )
    if not cmd_template:
        return {
            "status": "disabled",
            "pkg": ctx.pkg,
            "message": "No AI repair command configured (set AI_REPAIR_CMD secret or [ai_repair].command).",
        }

    formatted = cmd_template.format(
        context_file=shlex.quote(str(context_file.resolve())),
        pkg=shlex.quote(ctx.pkg),
        repo_root=shlex.quote(str(cfg.repo_root.resolve())),
    )

    proc = subprocess.run(
        formatted,
        shell=True,
        cwd=str(cfg.repo_root),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "AUTOBUMP_CONTEXT_FILE": str(context_file.resolve()),
            "AUTOBUMP_PKG": ctx.pkg,
            "AUTOBUMP_REPO_ROOT": str(cfg.repo_root.resolve()),
        },
    )

    changed = changed_files_in_repo(cfg.repo_root)
    disallowed = validate_allowed_changes(changed, ctx.pkg, cfg.ai_repair.allowed_extra_paths)
    if disallowed:
        # Immediately revert everything to protect the repository
        run(["git", "checkout", "--", "."], cwd=cfg.repo_root, check=False)
        run(["git", "clean", "-fd"], cwd=cfg.repo_root, check=False)
        raise BumpError(
            f"AI repair violated file-scope guardrail for {ctx.pkg}! "
            f"Disallowed paths modified: {disallowed}. Changes reverted."
        )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        return {
            "status": "failed",
            "pkg": ctx.pkg,
            "changed_files": changed,
            "message": f"AI repair command exited with {proc.returncode}:\n{detail}",
        }

    if not changed:
        return {
            "status": "no_changes",
            "pkg": ctx.pkg,
            "changed_files": [],
            "message": "AI repair command completed without making file changes.",
        }

    return {
        "status": "patched",
        "pkg": ctx.pkg,
        "changed_files": changed,
        "message": (proc.stdout or "").strip()[-2000:] or f"AI repair modified {len(changed)} file(s).",
    }
