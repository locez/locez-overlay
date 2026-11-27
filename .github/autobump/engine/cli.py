"""Idiomatic argparse CLI for the generic Gentoo overlay automation engine."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .builder import render_container_test_script, resolve_target_atom
from .common import get_local_version
from .context import collect_failure_context
from .deps import build_deps_for_package, resolve_packages_deps
from .detect import detect_updates
from .notify import close_github_failure_issue, notify_event
from .plugins import bump_package
from .python_compat import apply_python_compat_sync, scan_python_compat_updates
from .repair import run_ai_repair
from .schema import AutomationConfig, load_config


def _emit(payload: Any, result_file: str | None = None) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if result_file:
        Path(result_file).write_text(text + "\n", encoding="utf-8")
    print(text)


def _cmd_config_info(cfg: AutomationConfig, _args: argparse.Namespace) -> int:
    _emit(
        {
            "overlay": asdict(cfg.overlay),
            "build": asdict(cfg.build),
            "notify": asdict(cfg.notify),
            "ai_repair": asdict(cfg.ai_repair),
            "package_count": len(cfg.packages),
        }
    )
    return 0


def _cmd_detect(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    _emit(detect_updates(cfg, only_pkg=args.only), args.result_file)
    return 0


def _cmd_bump(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    if args.pkg:
        target = args.target
        if not target:
            det = detect_updates(cfg, only_pkg=args.pkg)
            if det["updates"]:
                target = det["updates"][0]["upstream_ver"]
            else:
                spec = cfg.get_package(args.pkg)
                cur = get_local_version(cfg.repo_root, args.pkg, spec.strategy) or "unknown"
                _emit(
                    {
                        "pkg": args.pkg,
                        "slug": spec.slug,
                        "strategy": spec.strategy,
                        "local_ver": cur,
                        "target_ver": cur,
                        "policy": spec.policy,
                        "status": "skipped",
                        "message": f"{args.pkg} is already up-to-date",
                    },
                    args.result_file,
                )
                return 0
        res = bump_package(cfg, args.pkg, target, upload_vendor=args.upload_vendor)
        _emit(res, args.result_file)
        return 1 if res["status"] == "failed" else 0

    det = detect_updates(cfg)
    results = []
    any_failed = bool(det["errors"])
    for item in det["updates"]:
        r = bump_package(cfg, item["pkg"], item["upstream_ver"], upload_vendor=args.upload_vendor)
        results.append(r)
        if r["status"] == "failed":
            any_failed = True
    _emit({"results": results, "detect_errors": det["errors"], "zfs_alerts": det["zfs_alerts"]}, args.result_file)
    return 1 if any_failed else 0


def _cmd_render_build_script(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    spec = cfg.get_package(args.pkg)
    atom = resolve_target_atom(cfg, args.pkg, live=args.live)
    script = render_container_test_script(
        cfg,
        spec,
        atom=atom,
        overlay_mount=args.overlay_mount,
        log_dir=args.log_dir,
        python_target=args.python_target,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(script, encoding="utf-8")
    out_path.chmod(0o755)
    _emit({"pkg": args.pkg, "atom": atom, "mode": spec.build.mode, "script": str(out_path)})
    return 0


def _cmd_python_compat_detect(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    _emit(scan_python_compat_updates(cfg, only_pkg=args.only), args.result_file)
    return 0


def _cmd_python_compat_sync(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    res = apply_python_compat_sync(cfg, args.pkg)
    _emit(res, args.result_file)
    return 0


def _git_changed_packages(repo_root: Path, base: str, *, only_configured: set[str] | None = None) -> list[str]:
    res = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=True,
    )
    found: set[str] = set()
    for line in res.stdout.splitlines():
        parts = line.strip().split("/")
        if len(parts) < 3:
            continue
        candidate = f"{parts[0]}/{parts[1]}"
        if only_configured is not None:
            if candidate in only_configured:
                found.add(candidate)
        elif (repo_root / parts[0] / parts[1]).is_dir():
            found.add(candidate)
    return sorted(found)


def _cmd_changed_packages(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    pkgs = _git_changed_packages(cfg.repo_root, args.base, only_configured=set(cfg.packages))
    _emit([{"pkg": p, "slug": cfg.packages[p].slug} for p in pkgs])
    return 0


def _cmd_live_packages(cfg: AutomationConfig, _args: argparse.Namespace) -> int:
    _emit([{"pkg": p, "slug": s.slug} for p, s in sorted(cfg.packages.items()) if s.weekly_live_test])
    return 0


def _cmd_collect_context(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    log_map = {k: Path(v) for item in args.log if "=" in item for k, v in [item.split("=", 1)]}
    ctx = collect_failure_context(
        repo_root=cfg.repo_root,
        pkg=args.pkg,
        stage=args.stage,
        local_ver=args.local_ver,
        target_ver=args.target_ver,
        summary=args.summary,
        run_url=args.run_url,
        log_files=log_map,
    )
    ctx.write(Path(args.out))
    print(f"Wrote FailureContext to {args.out}")
    return 0


def _cmd_notify(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    detail = args.detail
    if args.detail_file and Path(args.detail_file).is_file():
        detail = Path(args.detail_file).read_text(encoding="utf-8", errors="replace")
    res = notify_event(
        cfg,
        event=args.event,
        pkg=args.pkg,
        title=args.title,
        detail=detail,
        run_url=args.run_url,
        pr_url=args.pr_url,
    )
    _emit(res)
    return 0


def _cmd_close_issue(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    close_github_failure_issue(cfg, args.pkg, args.comment)
    return 0


def _cmd_ai_repair(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    res = run_ai_repair(cfg, Path(args.context), command_override=args.command)
    _emit(res, args.result_file)
    return 0 if res["status"] in ("patched", "disabled", "no_changes") else 1


def _cmd_build_deps(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    res = build_deps_for_package(
        cfg,
        pkg=args.pkg,
        ver=args.ver,
        kind=args.type,
        src_url=args.src_url,
        out_dir=Path(args.out_dir),
        upload=args.upload,
        go_build_target=args.go_build_target,
        cargo_manifest_dir=args.cargo_manifest_dir,
    )
    _emit(res, args.result_file)
    return 0


def _cmd_resolve_deps(cfg: AutomationConfig, args: argparse.Namespace) -> int:
    pkgs = list(args.pkgs or []) or _git_changed_packages(cfg.repo_root, args.base)
    res = resolve_packages_deps(cfg, pkgs, upload=not args.no_upload)
    _emit(res, args.result_file)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generic Gentoo overlay automation engine")
    parser.add_argument("--repo", help="Path to overlay root (auto-detected if omitted)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("config-info", help="Emit overlay/build/ai_repair config metadata as JSON")
    p.set_defaults(func=_cmd_config_info)

    p = sub.add_parser("detect", help="Detect outdated packages & ZFS unlock alerts")
    p.add_argument("--only", help="Filter to a single package")
    p.add_argument("--result-file")
    p.set_defaults(func=_cmd_detect)

    p = sub.add_parser("bump", help="Bump one package or --all outdated packages")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--pkg", help="Single package category/name")
    grp.add_argument("--all", action="store_true", help="Bump all outdated packages")
    p.add_argument("--target", help="Target version for --pkg")
    p.add_argument("--upload-vendor", action="store_true")
    p.add_argument("--result-file")
    p.set_defaults(func=_cmd_bump)

    p = sub.add_parser("render-build-script", help="Render Gentoo stage3 build & smoke-test script")
    p.add_argument("--pkg", required=True)
    p.add_argument("--live", action="store_true", help="Test 9999 live ebuild")
    p.add_argument("--overlay-mount", default="/overlay")
    p.add_argument("--log-dir", default="/build-logs")
    p.add_argument("--python-target", default="", help="Extra PYTHON_TARGETS impl to test (e.g. python3_15)")
    p.add_argument("--out", required=True, help="Output path for generated bash script")
    p.set_defaults(func=_cmd_render_build_script)

    p = sub.add_parser("python-compat-detect", help="Detect packages needing PYTHON_COMPAT bump or EOL cleanup")
    p.add_argument("--only", help="Filter to a single package")
    p.add_argument("--result-file")
    p.set_defaults(func=_cmd_python_compat_detect)

    p = sub.add_parser("python-compat-sync", help="Update PYTHON_COMPAT in all ebuilds of a package")
    p.add_argument("--pkg", required=True)
    p.add_argument("--result-file")
    p.set_defaults(func=_cmd_python_compat_sync)

    p = sub.add_parser("changed-packages", help="List packages modified relative to base ref")
    p.add_argument("--base", default="origin/master")
    p.set_defaults(func=_cmd_changed_packages)

    p = sub.add_parser("live-packages", help="List packages enabled for weekly live build tests")
    p.set_defaults(func=_cmd_live_packages)

    p = sub.add_parser("collect-context", help="Collect structured FailureContext JSON")
    p.add_argument("--pkg", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--local-ver", default="unknown")
    p.add_argument("--target-ver", default="unknown")
    p.add_argument("--summary", required=True)
    p.add_argument("--run-url", default="")
    p.add_argument("--log", action="append", default=[], help="name=path log files to attach")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_collect_context)

    p = sub.add_parser("notify", help="Send event notification (GitHub Issue + Telegram)")
    p.add_argument("--event", required=True, choices=["failed", "ai_draft_pr", "zfs_unlocked", "health_alert", "merged"])
    p.add_argument("--pkg", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--detail", default="")
    p.add_argument("--detail-file", default="")
    p.add_argument("--run-url", default="")
    p.add_argument("--pr-url", default="")
    p.set_defaults(func=_cmd_notify)

    p = sub.add_parser("close-issue", help="Close open failure issue for a package")
    p.add_argument("--pkg", required=True)
    p.add_argument("--comment", required=True)
    p.set_defaults(func=_cmd_close_issue)

    p = sub.add_parser("ai-repair", help="Run pluggable AI repair runner with file-scope guardrails")
    p.add_argument("--context", required=True, help="Path to FailureContext JSON")
    p.add_argument("--command", default="", help="Optional command template override")
    p.add_argument("--result-file")
    p.set_defaults(func=_cmd_ai_repair)

    p = sub.add_parser("build-deps", help="Build (and optionally upload) Go/Rust ${P}-deps.tar.xz to distfiles_repo")
    p.add_argument("--pkg", required=True, help="Package atom (category/name)")
    p.add_argument("--ver", required=True, help="Package version (e.g. 0.1.10)")
    p.add_argument("--type", required=True, choices=["go", "rust"], help="Ecosystem dependency type")
    p.add_argument("--src-url", required=True, help="Upstream source tarball URL")
    p.add_argument("--out-dir", default="/tmp/distfiles-deps", help="Output directory for ${P}-deps.tar.xz")
    p.add_argument("--go-build-target", default=".", help="Go package target for sanity build check")
    p.add_argument("--cargo-manifest-dir", default="", help="Subdirectory containing Cargo.toml if not repo root")
    p.add_argument("--upload", action="store_true", help="Upload tarball to overlay.distfiles_repo GitHub Release")
    p.add_argument("--result-file")
    p.set_defaults(func=_cmd_build_deps)

    p = sub.add_parser("resolve-deps", help="Resolve __DEPS_TARBALL_URI__ placeholders & upload missing Go/Rust deps for PR packages")
    p.add_argument("--base", default="origin/master", help="Git base ref to detect changed packages when --pkgs is omitted")
    p.add_argument("--pkgs", nargs="*", help="Explicit list of category/name packages to resolve")
    p.add_argument("--no-upload", action="store_true", help="Build locally without uploading to distfiles_repo")
    p.add_argument("--result-file")
    p.set_defaults(func=_cmd_resolve_deps)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(Path(args.repo) if args.repo else None)
    return int(args.func(cfg, args))
