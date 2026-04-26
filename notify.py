from __future__ import annotations
import os
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


def send_post(post: AnalyzedPost, analysis: str) -> None:
    token, chat_id = _get_telegram_config()
    text = (
        f"<b>r/{post.subreddit}</b> — <i>{post.title}</i>\n"
        f"{post.permalink}\n"
        f"\n"
        f"{analysis[:500]}"
    )
    _send_telegram(token, chat_id, text)
