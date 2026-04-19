import os
import re
import smtplib
from email.mime.text import MIMEText


def send_notification(reenabled: list[str]) -> None:
    smtp_email = os.environ["SMTP_EMAIL"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    carrier_gateway = os.environ["CARRIER_GATEWAY"]
    raw_phone = os.environ["PHONE_NUMBER"]

    digits = re.sub(r"\D", "", raw_phone)
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    recipient = f"{digits}@{carrier_gateway}"

    if reenabled:
        body = f"F5Bot: Re-enabled {len(reenabled)} keyword(s): {', '.join(reenabled)}"
    else:
        body = "F5Bot: All keywords were already enabled. No changes made."

    msg = MIMEText(body)
    msg["Subject"] = "F5Bot Keywords Update"
    msg["From"] = smtp_email
    msg["To"] = recipient

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.sendmail(smtp_email, recipient, msg.as_string())

    print(f"Notification sent to {recipient}")
