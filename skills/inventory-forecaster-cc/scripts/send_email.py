"""
send_email.py
-------------
Reusable SMTP email sender for Pets+People scheduled scripts.
Sends via Office 365 (smtp.office365.com:587) using credentials
from .env or environment variables. No Outlook required.

Usage (CLI):
    python send_email.py --to s.shweky@petspeople.com \
                         --subject "Test" \
                         --body "Hello" \
                         [--html]

    # For large HTML bodies, use --body-file to avoid command-line length limits:
    python send_email.py --subject "Test" --body-file email.html --html

Usage (import):
    from send_email import send_email
    send_email("Subject", html_body, to="s.shweky@petspeople.com")
"""

import os
import sys
import smtplib
import argparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env():
    env_path = Path(r"C:\Users\steven\Desktop\Dropbox (Personal)\Working Docs\Claude Home\.env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val

_load_env()

SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)


def send_email(subject, body, to=None, is_html=True):
    """
    Send an email via Office 365 SMTP.

    Args:
        subject  : Email subject line
        body     : Email body (HTML or plain text)
        to       : Recipient address (defaults to SMTP_FROM)
        is_html  : True for HTML body, False for plain text

    Returns:
        True on success, False on failure (prints error).
    """
    if not SMTP_USER or not SMTP_PASS:
        print("ERROR send_email: SMTP_USER or SMTP_PASSWORD not set in .env", file=sys.stderr)
        return False

    recipient = to or SMTP_FROM

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_FROM
    msg["To"]      = recipient

    content_type = "html" if is_html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, [recipient], msg.as_string())
        print(f"Email sent: {subject}")
        return True
    except Exception as e:
        print(f"ERROR send_email: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Send email via Office 365 SMTP")
    p.add_argument("--to",        default=None,  help="Recipient (default: SMTP_FROM)")
    p.add_argument("--subject",   required=True, help="Subject line")
    p.add_argument("--body",      default=None,  help="Body text or HTML (inline)")
    p.add_argument("--body-file", default=None,  dest="body_file",
                   help="Path to file containing body (avoids command-line length limits)")
    p.add_argument("--html",      action="store_true", help="Treat body as HTML")
    args = p.parse_args()

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body:
        body = args.body
    else:
        print("ERROR: --body or --body-file required", file=sys.stderr)
        sys.exit(1)

    ok = send_email(args.subject, body, to=args.to, is_html=args.html)
    sys.exit(0 if ok else 1)
