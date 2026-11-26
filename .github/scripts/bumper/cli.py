"""CLI interface for locez-overlay autobump."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .common import get_local_version
from .detect import detect_updates
from .handlers import bump_package


def _emit(payload: Any, result_file: str | None) -> None:
    text = json.dumps(payload, indent=2)
    if result_file:
        Path(result_file).write_text(text + "\n", encoding="utf-8")
    print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="locez-overlay automated package bumper")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--detect", action="store_true", help="Detect outdated packages as JSON")
    mode.add_argument("--pkg", help="Bump a single package (category/name)")
    mode.add_argument("--all", action="store_true", help="Detect and bump all outdated packages")
    parser.add_argument("--only", help="Filter --detect to a single package")
    parser.add_argument("--target", help="Target version for --pkg (auto-detected if omitted)")
    parser.add_argument("--upload-vendor", action="store_true", help="Upload Go vendor tarball to GitHub Releases")
    parser.add_argument("--result-file", help="Write JSON result to this file")
    args = parser.parse_args(argv)

    if args.detect:
        _emit(detect_updates(only_pkg=args.only), args.result_file)
        return 0

    if args.pkg:
        target = args.target
        if not target:
            det = detect_updates(only_pkg=args.pkg)
            if det["updates"]:
                target = det["updates"][0]["upstream_ver"]
            else:
                cur = get_local_version(args.pkg) or "unknown"
                _emit(
                    {
                        "pkg": args.pkg,
                        "slug": args.pkg.replace("/", "-"),
                        "local_ver": cur,
                        "target_ver": cur,
                        "status": "skipped",
                        "message": f"{args.pkg} is already up-to-date",
                    },
                    args.result_file,
                )
                return 0
        result = bump_package(args.pkg, target, upload_vendor=args.upload_vendor)
        _emit(result, args.result_file)
        return 1 if result["status"] == "failed" else 0

    det = detect_updates()
    results = []
    any_failed = bool(det["errors"])
    for item in det["updates"]:
        res = bump_package(item["pkg"], item["upstream_ver"], upload_vendor=args.upload_vendor)
        results.append(res)
        if res["status"] == "failed":
            any_failed = True
    _emit({"results": results, "detect_errors": det["errors"]}, args.result_file)
    return 1 if any_failed else 0
