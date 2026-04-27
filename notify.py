from __future__ import annotations
import html
import os
import re
import httpx
from datetime import date
from analyze import AnalyzedPost


def _get_telegram_config() -> tuple[str, str]:
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    return token, chat_id


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = httpx.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10.0)
    resp.raise_for_status()


def send_notification(reenabled: list[str]) -> None:
    token, chat_id = _get_telegram_config()
    header = f"<b>F5Bot — {date.today().strftime('%B %d, %Y')}</b>"
    if reenabled:
        body = f"Re-enabled {len(reenabled)} keyword(s): {', '.join(reenabled)}"
    else:
        body = "All keywords already enabled."
    _send_telegram(token, chat_id, f"{header}\n{body}")


def _format_analysis(analysis: str) -> str:
    why_m = re.search(r"WHY:\s*(.+)", analysis)
    angle_m = re.search(r"ANGLE:\s*(.+)", analysis)
    parts = []
    if why_m:
        parts.append(html.escape(why_m.group(1).strip()))
    if angle_m:
        sentences = re.split(r"(?<=\.)\s+", angle_m.group(1).strip())
        parts.extend(f"• {html.escape(s)}" for s in sentences[:2])
    return "\n".join(parts) if parts else html.escape(analysis.strip())


def send_post(post: AnalyzedPost, analysis: str) -> None:
    token, chat_id = _get_telegram_config()
    text = (
        f"<b>r/{html.escape(post.subreddit)}</b> — <i>{html.escape(post.title)}</i>\n"
        f"{post.url}\n"
        f"\n"
        f"{_format_analysis(analysis)}"
    )
    _send_telegram(token, chat_id, text)
