"""Pluggable Notifier: GitHub Issue (with dedup) + Telegram Bot."""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .common import USER_AGENT, run
from .schema import AutomationConfig

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def extract_log_excerpt(log_paths: list[Path], *, max_lines: int = 45, max_chars: int = 3500) -> str:
    """Return a clean, ANSI-stripped tail from the first existing non-empty log file."""
    for path in log_paths:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            continue
        clean = _ANSI_ESCAPE_RE.sub("", raw)
        lines = clean.splitlines()[-max_lines:]
        excerpt = "\n".join(lines).strip()
        if len(excerpt) > max_chars:
            excerpt = "...(truncated)\n" + excerpt[-(max_chars - 16) :]
        return excerpt
    return ""


def send_telegram(text_html: str) -> bool:
    """Send an HTML message via Telegram Bot API if secrets are configured."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    # Telegram message limit is 4096 chars
    if len(text_html) > 3900:
        text_html = text_html[:3850] + "\n...<i>(truncated)</i>"

    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status == 200


def format_telegram_event(
    *,
    overlay_name: str,
    event: str,
    pkg: str,
    title: str,
    detail: str = "",
    url: str = "",
) -> str:
    icons = {
        "failed": "❌",
        "ai_draft_pr": "🤖",
        "zfs_unlocked": "🎉",
        "health_alert": "🩺",
        "merged": "✅",
    }
    icon = icons.get(event, "ℹ️")
    parts = [
        f"{icon} <b>[{html.escape(overlay_name)}] {html.escape(title)}</b>",
        f"Package: <code>{html.escape(pkg)}</code>",
    ]
    if detail:
        snippet = detail.strip()
        if len(snippet) > 1200:
            snippet = snippet[:1200] + "\n...(truncated)"
        parts.append(f"<pre>{html.escape(snippet)}</pre>")
    if url:
        parts.append(f'<a href="{html.escape(url, quote=True)}">Open on GitHub</a>')
    return "\n".join(parts)


def upsert_github_failure_issue(
    cfg: AutomationConfig,
    *,
    pkg: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    """Create or update a deduped GitHub failure issue; returns {'action', 'number', 'url'}."""
    label = cfg.notify.github_label
    repo = cfg.overlay.repository
    run(
        [
            "gh",
            "label",
            "create",
            label,
            "-R",
            repo,
            "--color",
            "d93f0b",
            "--description",
            "Automated overlay maintenance failure",
        ],
        check=False,
    )

    search_prefix = f"[{label}] {pkg}"
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
            "--search",
            f"{search_prefix} in:title",
            "--json",
            "number,title,url",
        ],
        check=False,
    )
    existing = None
    if res.returncode == 0 and res.stdout.strip():
        items = json.loads(res.stdout)
        if items:
            existing = items[0]

    if existing:
        num = str(existing["number"])
        if existing.get("title") != title:
            run(["gh", "issue", "edit", num, "-R", repo, "--title", title])
            run(["gh", "issue", "comment", num, "-R", repo, "--body", body])
            return {"action": "updated", "number": int(num), "url": existing.get("url", "")}
        return {"action": "deduped", "number": int(num), "url": existing.get("url", "")}

    created = run(
        ["gh", "issue", "create", "-R", repo, "--title", title, "--label", label, "--body", body]
    )
    url = created.stdout.strip().splitlines()[-1] if created.stdout.strip() else ""
    num = int(url.rstrip("/").split("/")[-1]) if url.rstrip("/").split("/")[-1].isdigit() else 0
    return {"action": "created", "number": num, "url": url}


def close_github_failure_issue(cfg: AutomationConfig, pkg: str, comment: str) -> None:
    label = cfg.notify.github_label
    repo = cfg.overlay.repository
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
            "--search",
            f"[{label}] {pkg} in:title",
            "--json",
            "number",
        ],
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return
    for item in json.loads(res.stdout):
        run(["gh", "issue", "close", str(item["number"]), "-R", repo, "--comment", comment], check=False)


def notify_event(
    cfg: AutomationConfig,
    *,
    event: str,
    pkg: str,
    title: str,
    detail: str = "",
    log_excerpt: str = "",
    run_url: str = "",
    pr_url: str = "",
) -> dict[str, Any]:
    """Dispatch event to configured channels (GitHub Issue + Telegram)."""
    out: dict[str, Any] = {"event": event, "pkg": pkg, "github": None, "telegram": False}
    mention = f"@{cfg.overlay.maintainer} " if cfg.overlay.maintainer else ""
    link = pr_url or run_url

    if event in ("failed", "zfs_unlocked", "health_alert") and cfg.notify.github_issue:
        gh_title = f"[{cfg.notify.github_label}] {pkg}: {title}"
        gh_body = f"{mention}{title}\n\n"
        if detail:
            gh_body += f"```\n{detail.strip()[:4000]}\n```\n\n"
        if log_excerpt:
            gh_body += (
                "<details>\n"
                "<summary>Failure log excerpt (tail)</summary>\n\n"
                f"```text\n{log_excerpt.strip()}\n```\n"
                "</details>\n\n"
            )
        if run_url:
            gh_body += f"Workflow run: {run_url}\n"
        if event == "failed":
            gh_body += "\n_Comment `/ai-fix` to trigger an AI repair Draft PR attempt._\n"
        out["github"] = upsert_github_failure_issue(cfg, pkg=pkg, title=gh_title, body=gh_body)
        if out["github"].get("url"):
            link = out["github"]["url"]

    if cfg.notify.telegram and event in cfg.notify.telegram_events:
        # Avoid spamming Telegram if GitHub already deduped an identical open failure
        if not (out["github"] and out["github"].get("action") == "deduped"):
            msg = format_telegram_event(
                overlay_name=cfg.overlay.name,
                event=event,
                pkg=pkg,
                title=title,
                detail=detail,
                url=link,
            )
            try:
                out["telegram"] = send_telegram(msg)
            except (OSError, urllib.error.URLError) as exc:
                out["telegram_error"] = str(exc)

    return out
