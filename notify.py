from __future__ import annotations

import os
import re
import smtplib
from email.mime.text import MIMEText

from reddit import RedditPost


def _get_smtp_config() -> tuple[str, str, str]:
    smtp_email = os.environ["SMTP_EMAIL"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    carrier_gateway = os.environ["CARRIER_GATEWAY"]
    raw_phone = os.environ["PHONE_NUMBER"]
    digits = re.sub(r"\D", "", raw_phone)
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    recipient = f"{digits}@{carrier_gateway}"
    return smtp_email, smtp_password, recipient


def _send_sms(smtp_email: str, smtp_password: str, recipient: str, subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_email
    msg["To"] = recipient
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.sendmail(smtp_email, recipient, msg.as_string())


def send_notification(reenabled: list[str]) -> None:
    smtp_email, smtp_password, recipient = _get_smtp_config()
    if reenabled:
        body = f"F5Bot: Re-enabled {len(reenabled)} keyword(s): {', '.join(reenabled)}"
    else:
        body = "F5Bot: All keywords were already enabled. No changes made."
    _send_sms(smtp_email, smtp_password, recipient, "F5Bot Keywords Update", body)
    print(f"Notification sent to {recipient}")


_MAX_ANALYSES = int(os.environ.get("MAX_ANALYSES", "5"))


def send_analyses(results: list[tuple[RedditPost, str]]) -> None:
    if not results:
        return
    smtp_email, smtp_password, recipient = _get_smtp_config()
    top5 = sorted(
        [(p, a) for p, a in results if not a.strip().upper().startswith("SKIP")],
        key=lambda r: r[0].score,
        reverse=True,
    )[:_MAX_ANALYSES]
    skipped = len(results) - len(top5)
    summary = (
        f"F5Bot: {len(results)} post(s) analyzed — "
        f"{len(top5)} flagged, {skipped} skipped."
    )
    _send_sms(smtp_email, smtp_password, recipient, "F5Bot Summary", summary)
    print(f"Summary sent: {summary}")

    for i, (post, analysis) in enumerate(top5, start=1):
        title = post.title[:60] + ("…" if len(post.title) > 60 else "")
        body = (
            f"[{i}/{len(top5)}] r/{post.subreddit}: {title}\n"
            f"{post.permalink}\n"
            f"---\n"
            f"{analysis[:600]}"
        )
        _send_sms(smtp_email, smtp_password, recipient, f"F5Bot Analysis [{i}/{len(top5)}]", body)
        print(f"Analysis {i}/{len(top5)} sent to {recipient}")
