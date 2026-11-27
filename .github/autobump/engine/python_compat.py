"""Automatic PYTHON_COMPAT bumper & EOL target cleaner synced with Gentoo main tree."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .common import BumpError, http_get_text
from .schema import AutomationConfig

PYTHON_TARGETS_DESC_URL = (
    "https://raw.githubusercontent.com/gentoo-mirror/gentoo/master/profiles/desc/python_targets.desc"
)


def parse_python_targets_desc(text: str) -> tuple[list[int], set[str]]:
    """Return (sorted_cpython3_minors, active_pypy_targets) from Gentoo python_targets.desc."""
    minors: set[int] = set()
    pypys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split("-", 1)[0].strip()
        m = re.fullmatch(r"python3_([0-9]+)", token)
        if m:
            minors.add(int(m.group(1)))
        elif re.fullmatch(r"pypy3_[0-9]+", token):
            pypys.add(token)
    if not minors:
        raise BumpError("Could not parse any active python3_X targets from python_targets.desc")
    return sorted(minors), pypys


def get_gentoo_python_targets() -> tuple[list[int], set[str]]:
    """Fetch active CPython 3.x and PyPy3 targets from gentoo-mirror/gentoo."""
    return parse_python_targets_desc(http_get_text(PYTHON_TARGETS_DESC_URL))


def parse_python_compat(eb_text: str) -> tuple[list[int], list[str]] | None:
    """Parse PYTHON_COMPAT=( ... ) from ebuild text. Returns (cpython_minors, other_tokens) or None."""
    m = re.search(r"^PYTHON_COMPAT=\(\s*([^)]+?)\s*\)", eb_text, re.M)
    if not m:
        return None
    raw_tokens = m.group(1).split()
    minors: set[int] = set()
    others: list[str] = []
    for tok in raw_tokens:
        # Handle brace range e.g. python3_{12..14}
        bm = re.fullmatch(r"python3_\{([0-9]+)\.\.([0-9]+)\}", tok)
        if bm:
            lo, hi = int(bm.group(1)), int(bm.group(2))
            for v in range(min(lo, hi), max(lo, hi) + 1):
                minors.add(v)
            continue
        # Handle brace list e.g. python3_{12,13,14}
        lm = re.fullmatch(r"python3_\{([0-9,]+)\}", tok)
        if lm:
            for part in lm.group(1).split(","):
                if part.isdigit():
                    minors.add(int(part))
            continue
        sm = re.fullmatch(r"python3_([0-9]+)", tok)
        if sm:
            minors.add(int(sm.group(1)))
            continue
        others.append(tok)
    return sorted(minors), others


def format_python_compat(minors: Sequence[int], others: Sequence[str] = ()) -> str:
    """Format PYTHON_COMPAT=( ... ) using idiomatic Gentoo brace notation."""
    tokens: list[str] = []
    if minors:
        s = sorted(set(minors))
        if len(s) >= 2 and s == list(range(s[0], s[-1] + 1)):
            tokens.append(f"python3_{{{s[0]}..{s[-1]}}}")
        else:
            tokens.extend(f"python3_{v}" for v in s)
    tokens.extend(others)
    return f"PYTHON_COMPAT=( {' '.join(tokens)} )"


def compute_target_compat(
    old_minors: list[int],
    active_minors: list[int],
    old_others: list[str],
    active_pypys: set[str],
) -> tuple[list[int], list[str]]:
    """Drop EOL targets (< min_active), preserve intentional floor, and extend to max_active."""
    min_active, max_active = active_minors[0], active_minors[-1]
    if old_minors:
        floor = max(min(old_minors), min_active)
        if floor > max_active:
            floor = max_active
    else:
        floor = min_active
    new_minors = [v for v in active_minors if floor <= v <= max_active]
    new_others = [t for t in old_others if not t.startswith("pypy3_") or t in active_pypys]
    return new_minors, new_others


def _list_all_ebuilds(repo_root: Path, pkg: str) -> list[Path]:
    pdir = repo_root / pkg
    return sorted(pdir.glob("*.ebuild")) if pdir.is_dir() else []


def scan_python_compat_updates(
    cfg: AutomationConfig,
    *,
    only_pkg: str | None = None,
    active_targets: tuple[list[int], set[str]] | None = None,
) -> dict[str, Any]:
    """Find packages in cfg.packages whose ebuilds have outdated or EOL PYTHON_COMPAT."""
    active_minors, active_pypys = active_targets or get_gentoo_python_targets()
    max_target = f"python3_{active_minors[-1]}"
    updates: list[dict[str, Any]] = []

    for pkg, spec in sorted(cfg.packages.items()):
        if only_pkg and pkg != only_pkg:
            continue
        changed_files: list[str] = []
        old_summary = ""
        new_summary = ""
        for eb in _list_all_ebuilds(cfg.repo_root, pkg):
            text = eb.read_text(encoding="utf-8")
            parsed = parse_python_compat(text)
            if parsed is None:
                continue
            old_minors, old_others = parsed
            new_minors, new_others = compute_target_compat(old_minors, active_minors, old_others, active_pypys)
            old_line = format_python_compat(old_minors, old_others)
            new_line = format_python_compat(new_minors, new_others)
            if old_minors != new_minors or old_others != new_others:
                changed_files.append(eb.name)
                old_summary = old_line
                new_summary = new_line
        if changed_files:
            updates.append(
                {
                    "pkg": pkg,
                    "slug": spec.slug,
                    "policy": spec.policy,
                    "old_compat": old_summary,
                    "new_compat": new_summary,
                    "max_target": max_target,
                    "files": changed_files,
                }
            )

    return {
        "active_cpython": [f"python3_{v}" for v in active_minors],
        "max_target": max_target,
        "updates": updates,
    }


def apply_python_compat_sync(
    cfg: AutomationConfig,
    pkg: str,
    *,
    active_targets: tuple[list[int], set[str]] | None = None,
) -> dict[str, Any]:
    """Update PYTHON_COMPAT=( ... ) across all ebuilds of `pkg`."""
    spec = cfg.get_package(pkg)
    active_minors, active_pypys = active_targets or get_gentoo_python_targets()
    max_target = f"python3_{active_minors[-1]}"
    modified: list[str] = []
    old_line_last = ""
    new_line_last = ""

    for eb in _list_all_ebuilds(cfg.repo_root, pkg):
        text = eb.read_text(encoding="utf-8")
        parsed = parse_python_compat(text)
        if parsed is None:
            continue
        old_minors, old_others = parsed
        new_minors, new_others = compute_target_compat(old_minors, active_minors, old_others, active_pypys)
        if old_minors == new_minors and old_others == new_others:
            continue
        old_line_last = format_python_compat(old_minors, old_others)
        new_line_last = format_python_compat(new_minors, new_others)
        new_text = re.sub(r"^PYTHON_COMPAT=\(\s*[^)]+?\s*\)", new_line_last, text, count=1, flags=re.M)
        if new_text != text:
            eb.write_text(new_text, encoding="utf-8")
            modified.append(eb.name)

    if not modified:
        return {
            "pkg": pkg,
            "slug": spec.slug,
            "policy": spec.policy,
            "max_target": max_target,
            "status": "skipped",
            "message": f"{pkg} PYTHON_COMPAT is already up-to-date",
        }

    return {
        "pkg": pkg,
        "slug": spec.slug,
        "policy": spec.policy,
        "max_target": max_target,
        "old_compat": old_line_last,
        "new_compat": new_line_last,
        "files": modified,
        "status": "updated",
        "message": f"Updated {pkg} ({', '.join(modified)}): {old_line_last} -> {new_line_last}",
    }
