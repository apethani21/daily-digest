#!/usr/bin/env python3
"""Daily Digest — runs all checks and sends one email if anything is worth reporting."""

import argparse
import json
import logging
import os
import smtplib
import sys
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from checks import air_quality, holidays

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_FILE = SCRIPT_DIR / "logs" / "digest.log"
CONFIG_FILE = SCRIPT_DIR / "config.yaml"
ENV_FILE = SCRIPT_DIR / ".env"
LONDON_TZ = ZoneInfo("Europe/London")

CHECKS = [air_quality, holidays]


def setup_logging(verbose=False):
    LOG_FILE.parent.mkdir(exist_ok=True)
    fmt = "[%(asctime)s] [%(levelname)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt)
    file_handler = TimedRotatingFileHandler(LOG_FILE, when="D", backupCount=30)
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def load_env():
    if not ENV_FILE.exists():
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def build_subject(active_results, now):
    date_str = now.strftime("%-d %b %Y")
    if not active_results:
        return f"Daily Digest — {date_str}"
    tags = " & ".join(r.subject_tag for r in active_results)
    return f"Daily Digest: {tags} — {date_str}"


def assemble_plain(active_results, now):
    tz_label = "BST" if now.dst() and now.dst().total_seconds() > 0 else "GMT"
    lines = [
        f"DAILY DIGEST — {now.strftime('%A, %-d %B %Y')}",
        f"Generated at {now.strftime('%H:%M')} {tz_label}",
        "",
    ]
    for r in active_results:
        lines.append("=" * 50)
        lines.append(r.plain)
        lines.append("")
    return "\n".join(lines)


def assemble_html(active_results, now):
    tz_label = "BST" if now.dst() and now.dst().total_seconds() > 0 else "GMT"
    date_str = now.strftime("%A, %-d %B %Y")
    time_str = now.strftime("%H:%M")

    sections_html = ""
    for i, r in enumerate(active_results):
        if i > 0:
            sections_html += '\n<tr><td style="background:#f1f5f9;height:8px"></td></tr>\n'
        sections_html += r.rows_html

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 0">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%">

        <!-- Header -->
        <tr><td style="background:#1e293b;padding:20px 24px;border-radius:8px 8px 0 0">
          <p style="margin:0;font-size:18px;font-weight:700;color:#f8fafc">Daily Digest</p>
          <p style="margin:4px 0 0;font-size:13px;color:#94a3b8">{date_str} &middot; {time_str} {tz_label}</p>
        </td></tr>

        <!-- Sections -->
        {sections_html}

        <!-- Footer -->
        <tr><td style="background:#f8fafc;padding:12px 24px;border-radius:0 0 8px 8px;border-top:1px solid #e2e8f0">
          <p style="margin:0;font-size:11px;color:#94a3b8">Daily Digest &middot; {time_str} {tz_label}</p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_email(subject, plain_body, html_body, chart_png=None):
    email_from = os.environ["EMAIL_FROM"]
    email_to = os.environ["EMAIL_TO"]
    region = os.environ.get("SES_REGION", "eu-west-1")

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain_body, "plain"))
    alt.attach(MIMEText(html_body, "html"))

    if chart_png:
        root = MIMEMultipart("related")
        root.attach(alt)
        img = MIMEImage(chart_png, "png")
        img.add_header("Content-ID", "<forecast_chart>")
        img.add_header("Content-Disposition", "inline", filename="forecast.png")
        root.attach(img)
    else:
        root = alt

    root["Subject"] = subject
    root["From"] = email_from
    root["To"] = email_to

    cred_file = Path.home() / "keys" / "aws" / "ses-credentials.json"
    creds = json.loads(cred_file.read_text())
    username, password = creds["smtp-username"], creds["smtp-password"]

    host = f"email-smtp.{region}.amazonaws.com"
    with smtplib.SMTP(host, 587) as server:
        server.ehlo()
        server.starttls()
        server.login(username, password)
        server.sendmail(email_from, [email_to], root.as_bytes())
    logging.info(f"Email sent via SES SMTP ({host})")


def main():
    parser = argparse.ArgumentParser(description="Daily Digest")
    parser.add_argument("--force-email", action="store_true", help="Send even if nothing triggered")
    parser.add_argument("--dry-run", action="store_true", help="Print without sending")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    parser.add_argument(
        "--check",
        choices=["air_quality", "holidays"],
        help="Run only one check",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    load_env()

    if os.environ.get("DRY_RUN", "false").lower() == "true":
        args.dry_run = True

    try:
        config = load_config()
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)

    if not args.dry_run:
        if not os.environ.get("EMAIL_FROM") or not os.environ.get("EMAIL_TO"):
            logging.error("EMAIL_FROM and EMAIL_TO must be set in .env")
            sys.exit(1)
        if not (Path.home() / "keys" / "aws" / "ses-credentials.json").exists():
            logging.error("~/keys/aws/ses-credentials.json not found")
            sys.exit(1)

    now = datetime.now(LONDON_TZ)
    logging.info("Starting daily digest")

    check_map = {"air_quality": air_quality, "holidays": holidays}
    to_run = [check_map[args.check]] if args.check else list(check_map.values())

    results = []
    for module in to_run:
        name = module.__name__.split(".")[-1]
        try:
            result = module.run(config)
            results.append(result)
            logging.info(f"{name}: {'TRIGGERED' if result.triggered else 'clear'}")
        except Exception as e:
            logging.error(f"{name} failed: {e}", exc_info=True)

    any_triggered = any(r.triggered for r in results)
    if not any_triggered and not args.force_email:
        logging.info("Nothing to report — no email sent")
        return

    active_results = results if args.force_email else [r for r in results if r.triggered]

    subject = build_subject(active_results, now)
    plain_body = assemble_plain(active_results, now)
    html_body = assemble_html(active_results, now)
    chart_png = next((r.chart_png for r in active_results if r.chart_png), None)

    if args.dry_run:
        print(f"\nSubject: {subject}\n")
        print(plain_body)
        return

    try:
        send_email(subject, plain_body, html_body, chart_png)
        logging.info(f"Digest sent: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
