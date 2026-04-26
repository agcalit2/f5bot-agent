import imaplib
import email
import json
import os
import re
from datetime import date, timedelta
from urllib.parse import urlparse, parse_qs, unquote

SEEN_PATH = "seen_emails.json"
IMAP_HOST = "imap.gmail.com"
F5BOT_URL_RE = re.compile(r'https://f5bot\.com/url\?u=([^\s<&]+)')


def load_seen() -> set[str]:
    try:
        with open(SEEN_PATH) as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_seen(uids: set[str]) -> None:
    with open(SEEN_PATH, "w") as f:
        json.dump(list(uids), f)


def fetch_new_threads() -> list[dict]:
    smtp_email = os.environ["SMTP_EMAIL"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", 3))

    since_date = (date.today() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")

    with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
        imap.login(smtp_email, smtp_password)
        imap.select("INBOX")

        _, data = imap.search(None, f'FROM "admin@f5bot.com" SINCE {since_date}')
        all_uids = data[0].decode().split() if data[0] else []

        seen = load_seen()
        new_uids = [uid for uid in all_uids if uid not in seen]

        results = []
        for uid in new_uids:
            _, msg_data = imap.fetch(uid, "(BODY[])")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = msg.get("Subject", "")
            msg_date = msg.get("Date", "")

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="replace")

            reddit_links = [unquote(m) for m in F5BOT_URL_RE.findall(body)]

            results.append({
                "uid": uid,
                "subject": subject,
                "date": msg_date,
                "reddit_links": reddit_links,
            })

    return results, seen | set(new_uids)
