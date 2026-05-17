import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from checks import CheckResult

LONDON_TZ = ZoneInfo("Europe/London")

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

_WEEKDAY_PAT = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
_DATE_PAT = re.compile(
    rf"\b({_WEEKDAY_PAT})\s+(\d{{1,2}})\s+([A-Za-z]+)(?:\s+(\d{{4}}))?\s+UPCOMING\b",
    re.IGNORECASE,
)


def _parse_date_text(text):
    """Parse 'Tuesday 19 May' or 'Tuesday 19 May 2026' into a date, or None."""
    text = re.sub(r"\b(UPCOMING|PASSED)\b", "", text, flags=re.IGNORECASE).strip()
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if m:
        month = MONTH_MAP.get(m.group(2).lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                pass

    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)", text)
    if m:
        month = MONTH_MAP.get(m.group(2).lower())
        if month:
            today = datetime.now(LONDON_TZ).date()
            for year in (today.year, today.year + 1):
                try:
                    d = date(year, month, int(m.group(1)))
                    if d >= today - timedelta(days=30):
                        return d
                except ValueError:
                    pass
    return None


def _parse_page(soup, url):
    full_text = soup.get_text(" ", strip=True)

    last_updated = None
    m = re.search(
        r"Last\s+updated[:\s]+(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        full_text,
        re.IGNORECASE,
    )
    if m:
        last_updated = m.group(1)

    update_note = None
    for p in soup.find_all("p"):
        p_text = p.get_text(" ", strip=True)
        strong = p.find("strong")
        strong_text = strong.get_text(strip=True) if strong else ""
        if re.match(r"^Update\s*\(", strong_text, re.IGNORECASE) or re.match(
            r"^Update\s*\(", p_text, re.IGNORECASE
        ):
            update_note = p_text
            break

    strikes = []

    cards = soup.find_all("div", class_="strike-date-card")
    if cards:
        for card in cards:
            status_el = card.find(class_="status")
            date_el = card.find(class_="date-day")
            if not status_el or not date_el:
                continue
            if "UPCOMING" not in status_el.get_text(strip=True).upper():
                continue
            day_label = date_el.get_text(strip=True)
            d = _parse_date_text(day_label)
            if d:
                strikes.append({"date": d, "day_label": day_label, "type": "Strike day"})
    else:
        seen = set()
        for m in _DATE_PAT.finditer(full_text):
            day_label = f"{m.group(1)} {m.group(2)} {m.group(3)}"
            if m.group(4):
                day_label += f" {m.group(4)}"
            d = _parse_date_text(day_label)
            if d and d not in seen:
                seen.add(d)
                strikes.append({"date": d, "day_label": day_label, "type": "Strike day"})

    strikes.sort(key=lambda s: s["date"])

    return {
        "upcoming_strikes": strikes,
        "last_updated": last_updated,
        "update_note": update_note,
        "source_url": url,
    }


def _failure_result():
    plain = "⚠ Tube strike data could not be retrieved — check tfl.gov.uk manually."
    rows_html = """
    <tr>
      <td style="background:#f8fafc;padding:10px 24px 10px 21px;
                 border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;
                 border-left:3px solid #dc2626">
        <p style="margin:0;font-size:11px;font-weight:700;color:#64748b;
                  text-transform:uppercase;letter-spacing:0.08em">🚇 Tube Strikes</p>
      </td>
    </tr>
    <tr>
      <td style="background:white;padding:12px 24px;font-size:13px;color:#64748b">
        ⚠ Tube strike data could not be retrieved — check tfl.gov.uk manually.
      </td>
    </tr>"""
    return CheckResult(
        triggered=True,
        subject_tag="🚇 Tube Strikes",
        rows_html=rows_html,
        plain=plain,
    )


def build_section_rows(data, lookahead_days):
    strikes = data["upcoming_strikes"]
    last_updated = data["last_updated"] or "unknown"
    update_note = data["update_note"]
    source_url = data["source_url"]

    date_rows = ""
    for s in strikes:
        date_rows += f"""
            <tr>
              <td style="padding:8px 24px;border-bottom:1px solid #f1f5f9">
                <span style="font-size:13px;font-weight:600;color:#1e293b">{s['day_label']}</span>
                <span style="font-size:12px;color:#94a3b8;margin-left:8px">{s['type']}</span>
              </td>
            </tr>"""

    note_html = ""
    if update_note:
        note_html = f"""
        <tr><td style="background:#f1f5f9;height:6px"></td></tr>
        <tr>
          <td style="background:white">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:10px 24px 14px;font-size:12px;color:#64748b">
                  ⚠&nbsp; {update_note}
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    n = len(strikes)
    section_header = f"""
    <tr>
      <td style="background:#f8fafc;padding:10px 24px 10px 21px;
                 border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;
                 border-left:3px solid #dc2626">
        <p style="margin:0;font-size:11px;font-weight:700;color:#64748b;
                  text-transform:uppercase;letter-spacing:0.08em">
          🚇 Tube Strikes
          <span style="font-weight:400;text-transform:none;letter-spacing:0;
                       margin-left:8px;font-size:11px;color:#94a3b8">
            next {lookahead_days} day{'s' if lookahead_days != 1 else ''} &nbsp;&middot;&nbsp; {n} date{'s' if n != 1 else ''}
          </span>
        </p>
      </td>
    </tr>"""

    source_html = f"""
    <tr>
      <td style="background:white">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding:6px 24px 12px;font-size:11px;color:#94a3b8">
              Source: {source_url} (last updated: {last_updated})
              &nbsp;&middot;&nbsp; Always check tfl.gov.uk before travelling.
            </td>
          </tr>
        </table>
      </td>
    </tr>"""

    return f"""
    {section_header}
    <tr>
      <td style="background:white">
        <table width="100%" cellpadding="0" cellspacing="0">
          {date_rows}
          <tr><td style="height:4px"></td></tr>
        </table>
      </td>
    </tr>
    {note_html}
    {source_html}"""


def build_plain_section(data, lookahead_days):
    strikes = data["upcoming_strikes"]
    last_updated = data["last_updated"] or "unknown"
    update_note = data["update_note"]
    source_url = data["source_url"]

    lines = [f"🚇 TUBE STRIKES (next {lookahead_days} days)", ""]
    for s in strikes:
        lines.append(f"  {s['day_label']:<26}  {s['type']}")
    if update_note:
        lines.append("")
        lines.append(f"  ⚠ {update_note}")
    lines.append("")
    lines.append(f"  Source: {source_url} (last updated: {last_updated})")
    lines.append("  Always check tfl.gov.uk before travelling.")
    return "\n".join(lines)


def run(config) -> CheckResult:
    ts_config = config.get("tube_strike", {})
    if not ts_config.get("enabled", True):
        logging.info("Tube strike check: disabled in config — skipping")
        return CheckResult(triggered=False, subject_tag="🚇 Tube Strikes", rows_html="", plain="")

    url = ts_config.get("url", "https://tubenotifications.co.uk/tube-strikes")
    lookahead_days = ts_config.get("lookahead_days", 7)

    logging.info(f"Tube strike check: scraping {url}")

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logging.warning(f"Tube strike check: scraping failed — {e}")
        return _failure_result()

    try:
        soup = BeautifulSoup(resp.text, "lxml")
        data = _parse_page(soup, url)
    except Exception as e:
        logging.warning(f"Tube strike check: scraping failed — {e}")
        return _failure_result()

    today = datetime.now(LONDON_TZ).date()
    window_end = today + timedelta(days=lookahead_days)
    data["upcoming_strikes"] = [
        s for s in data["upcoming_strikes"] if today <= s["date"] <= window_end
    ]

    n = len(data["upcoming_strikes"])
    if n == 0:
        logging.info("Tube strike check: no upcoming strikes in window — section omitted")
        return CheckResult(triggered=False, subject_tag="🚇 Tube Strikes", rows_html="", plain="")

    logging.info(
        f"Tube strike check: found {n} upcoming strike date{'s' if n != 1 else ''} in next {lookahead_days} days"
    )

    return CheckResult(
        triggered=True,
        subject_tag="🚇 Tube Strikes",
        rows_html=build_section_rows(data, lookahead_days),
        plain=build_plain_section(data, lookahead_days),
    )
