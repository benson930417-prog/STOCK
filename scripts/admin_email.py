"""Admin email sender via Gmail SMTP.

Reads from environment:
    GMAIL_APP_PASSWORD   Required. 16-char Gmail App Password (NOT your
                         account password). Generate at:
                         https://myaccount.google.com/apppasswords
    ADMIN_EMAIL          Required. Recipient address.
    GMAIL_FROM           Optional. Defaults to ADMIN_EMAIL.
                         The Gmail account that owns the App Password.

CLI usage:
    python scripts/admin_email.py \\
        --subject "STOCK daily run — SUCCESS" \\
        --body-file /tmp/summary.txt

If env vars are missing or SMTP fails, prints a warning and exits 0
(non-fatal — daily job must never abort on email failure).
"""
from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from email.mime.text import MIMEText


def send(subject: str, body: str) -> int:
    password = os.environ.get("GMAIL_APP_PASSWORD")
    admin    = os.environ.get("ADMIN_EMAIL")
    sender   = os.environ.get("GMAIL_FROM") or admin

    if not password:
        print("[admin_email] GMAIL_APP_PASSWORD not set — skipping email", file=sys.stderr)
        return 0
    if not admin:
        print("[admin_email] ADMIN_EMAIL not set — skipping email", file=sys.stderr)
        return 0

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = admin

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.login(sender, password)
            s.send_message(msg)
        print(f"[admin_email] sent to {admin}: {subject}")
        return 0
    except Exception as exc:
        print(f"[admin_email] SMTP failed (non-fatal): {exc}", file=sys.stderr)
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--body", type=str, help="Inline email body")
    g.add_argument("--body-file", type=str, help="Path to a UTF-8 text file with the body")
    args = ap.parse_args()

    body = args.body
    if args.body_file:
        with open(args.body_file, "r", encoding="utf-8") as fh:
            body = fh.read()
    return send(args.subject, body or "")


if __name__ == "__main__":
    sys.exit(main())
