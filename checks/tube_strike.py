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

    cal_rows = soup.find_all("div", class_="ts-cal-row")
    if cal_rows:
        for row in cal_rows:
            label_el = row.find(class_="ts-cal-label")
            blurb_el = row.find(class_="ts-cal-pattern")
            badge_el = row.find(class_="ts-badge-upcoming")
            if not label_el or not badge_el:
                continue
            day_label = label_el.get_text(" ", strip=True)
            day_label = re.sub(r"\s*UPCOMING\s*", "", day_label, flags=re.IGNORECASE).strip()
            d = _parse_date_text(day_label)
            if d:
                blurb = blurb_el.get_text(" ", strip=True) if blurb_el else ""
                strikes.append({"date": d, "day_label": day_label, "blurb": blurb})
    else:
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
                    strikes.append({"date": d, "day_label": day_label, "blurb": ""})
        else:
            seen = set()
            for m in _DATE_PAT.finditer(full_text):
                day_label = f"{m.group(1)} {m.group(2)} {m.group(3)}"
                if m.group(4):
                    day_label += f" {m.group(4)}"
                d = _parse_date_text(day_label)
                if d and d not in seen:
                    seen.add(d)
                    strikes.append({"date": d, "day_label": day_label, "blurb": ""})

    strikes.sort(key=lambda s: s["date"])

    line_impact = None
    impact_el = soup.find("div", class_="ts-lines-affected-bad")
    if impact_el:
        line_impact = impact_el.get_text(" ", strip=True)

    running_normally = None
    lines_box = soup.find("div", class_="ts-lines-box")
    if lines_box:
        box_text = lines_box.get_text(" ", strip=True)
        m = re.search(r"Running normally[:\s]+(.+)", box_text, re.IGNORECASE)
        if m:
            running_normally = m.group(1).strip().rstrip(".")

    return {
        "upcoming_strikes": strikes,
        "last_updated": last_updated,
        "update_note": update_note,
        "line_impact": line_impact,
        "running_normally": running_normally,
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
    line_impact = data.get("line_impact")
    running_normally = data.get("running_normally")
    source_url = data["source_url"]

    date_rows = ""
    for s in strikes:
        blurb_html = ""
        if s.get("blurb"):
            blurb_html = f'<div style="font-size:12px;color:#64748b;margin-top:2px">{s["blurb"]}</div>'
        date_rows += f"""
            <tr>
              <td style="padding:10px 24px;border-bottom:1px solid #f1f5f9">
                <span style="font-size:13px;font-weight:600;color:#1e293b">{s['day_label']}</span>
                {blurb_html}
              </td>
            </tr>"""

    lines_html = ""
    if line_impact or running_normally:
        impact_row = ""
        if line_impact:
            impact_row = f"""
              <tr>
                <td style="padding:10px 24px 6px;font-size:12px;color:#475569">
                  <strong style="color:#dc2626">Affected:</strong> {line_impact}
                </td>
              </tr>"""
        normal_row = ""
        if running_normally:
            normal_row = f"""
              <tr>
                <td style="padding:4px 24px 12px;font-size:12px;color:#475569">
                  <strong style="color:#16a34a">Running normally:</strong> {running_normally}
                </td>
              </tr>"""
        lines_html = f"""
        <tr><td style="background:#f1f5f9;height:6px"></td></tr>
        <tr>
          <td style="background:white">
            <table width="100%" cellpadding="0" cellspacing="0">
              {impact_row}
              {normal_row}
            </table>
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
    {lines_html}
    {note_html}
    {source_html}"""


def build_plain_section(data, lookahead_days):
    strikes = data["upcoming_strikes"]
    last_updated = data["last_updated"] or "unknown"
    update_note = data["update_note"]
    line_impact = data.get("line_impact")
    running_normally = data.get("running_normally")
    source_url = data["source_url"]

    lines = [f"🚇 TUBE STRIKES (next {lookahead_days} days)", ""]
    for s in strikes:
        lines.append(f"  {s['day_label']}")
        if s.get("blurb"):
            lines.append(f"    {s['blurb']}")
    if line_impact:
        lines.append("")
        lines.append(f"  Affected: {line_impact}")
    if running_normally:
        lines.append(f"  Running normally: {running_normally}")
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
