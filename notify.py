from __future__ import annotations
import os
import httpx
from dotenv import load_dotenv
from analyze import AnalyzedPost

load_dotenv()


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
    if reenabled:
        text = f"F5Bot: Re-enabled {len(reenabled)} keyword(s): {', '.join(reenabled)}"
    else:
        text = "F5Bot: All keywords were already enabled. No changes made."
    _send_telegram(token, chat_id, text)
    print(f"Notification sent to Telegram chat {chat_id}")


_MAX_ANALYSES = int(os.environ.get("MAX_ANALYSES", "5"))


def send_analyses(results: list[tuple[AnalyzedPost, str]]) -> None:
    if not results:
        return
    token, chat_id = _get_telegram_config()
    flagged = sorted(
        [(p, a) for p, a in results if not a.strip().upper().startswith("SKIP")],
        key=lambda r: r[0].score,
        reverse=True,
    )[:_MAX_ANALYSES]
    skipped = len(results) - len(flagged)
    summary = (
        f"F5Bot: {len(results)} post(s) analyzed — "
        f"{len(flagged)} flagged, {skipped} skipped."
    )
    _send_telegram(token, chat_id, summary)
    print(f"Summary sent: {summary}")

    for i, (post, analysis) in enumerate(flagged, start=1):
        text = (
            f"<b>[{i}/{len(flagged)}] r/{post.subreddit}</b>\n"
            f"<i>{post.title}</i>\n"
            f"{post.permalink}\n"
            f"\n"
            f"{analysis[:2000]}"
        )
        _send_telegram(token, chat_id, text)
        print(f"Analysis {i}/{len(flagged)} sent to Telegram")
