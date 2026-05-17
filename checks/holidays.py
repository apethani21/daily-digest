import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import requests

from checks import CheckResult

LONDON_TZ = ZoneInfo("Europe/London")

try:
    from exchange_calendars.errors import InvalidCalendarName
except ImportError:
    InvalidCalendarName = ValueError


def fetch_uk_bank_holidays(url, division):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()[division]["events"]


def get_bank_holiday_events(config, window_start, window_end):
    url = config["bank_holidays"]["url"]
    division = config["bank_holidays"]["division"]
    raw = fetch_uk_bank_holidays(url, division)
    events = {}
    for event in raw:
        event_date = date.fromisoformat(event["date"])
        if window_start <= event_date <= window_end:
            events[event_date] = event["title"]
    return events


def get_exchange_holiday_events(config, window_start, window_end):
    weekdays = []
    current = window_start
    while current <= window_end:
        if current.weekday() < 5:
            weekdays.append(current)
        current += timedelta(days=1)

    events = {}
    for exchange in config["exchanges"]:
        calendar_name = exchange["calendar"]
        label = exchange["label"]
        try:
            cal = xcals.get_calendar(calendar_name)
        except InvalidCalendarName:
            logging.warning(f"Invalid calendar name '{calendar_name}' — skipping")
            continue
        except Exception as e:
            logging.warning(f"Failed to load calendar '{calendar_name}': {e} — skipping")
            continue

        try:
            holiday_names = cal.regular_holidays.holidays(
                start=pd.Timestamp(window_start),
                end=pd.Timestamp(window_end),
                return_name=True,
            )
        except Exception:
            holiday_names = pd.Series(dtype=str)

        for day in weekdays:
            ts = pd.Timestamp(day)
            try:
                is_session = cal.is_session(ts)
            except Exception:
                continue
            if not is_session:
                try:
                    name = holiday_names.get(ts)
                    if name is None or (isinstance(name, float) and pd.isna(name)):
                        name = "Exchange Holiday"
                    else:
                        name = str(name)
                except Exception:
                    name = "Exchange Holiday"
                events.setdefault(day, []).append((label, calendar_name, name))

    logging.info(f"Exchange holiday dates found: {len(events)}")
    return events


def _date_block_html(d, bank_holidays, exchange_events):
    day_label = d.strftime("%A")
    date_label = d.strftime("%-d %B %Y")
    rows_html = ""

    if d in bank_holidays:
        rows_html += f"""
            <tr>
              <td style="padding:10px 24px;border-bottom:1px solid #f1f5f9">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="font-size:13px;color:#475569;vertical-align:middle">
                      <span style="display:inline-block;background:#7c3aed;color:white;
                                   font-size:11px;font-weight:700;letter-spacing:0.06em;
                                   padding:2px 8px;border-radius:3px;margin-right:8px;
                                   text-transform:uppercase;vertical-align:middle">
                        UK Bank Holiday
                      </span>
                      <span style="color:#1e293b;font-weight:500">{bank_holidays[d]}</span>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

    if d in exchange_events:
        for label, calendar_name, holiday_name in exchange_events[d]:
            if d in bank_holidays and calendar_name == "XLON":
                continue
            rows_html += f"""
            <tr>
              <td style="padding:10px 24px;border-bottom:1px solid #f1f5f9">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="font-size:13px;vertical-align:middle">
                      <span style="display:inline-block;background:#e0f2fe;color:#0369a1;
                                   font-size:11px;font-weight:700;letter-spacing:0.06em;
                                   padding:2px 8px;border-radius:3px;margin-right:8px;
                                   text-transform:uppercase;vertical-align:middle">
                        Closed
                      </span>
                      <span style="color:#1e293b;font-weight:500">{label}</span>
                      <span style="color:#94a3b8;margin:0 6px">&middot;</span>
                      <span style="color:#64748b">{holiday_name}</span>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

    return f"""
        <tr>
          <td style="background:#f8fafc;padding:10px 24px 6px;
                     border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0">
            <span style="font-size:13px;font-weight:700;color:#1e293b">{day_label}</span>
            <span style="font-size:13px;color:#64748b;margin-left:6px">{date_label}</span>
          </td>
        </tr>
        {rows_html}"""


def build_section_rows(exchange_events, bank_holidays, window_start, window_end):
    all_dates = sorted(set(exchange_events.keys()) | set(bank_holidays.keys()))

    bank_count = len(bank_holidays)
    exchange_count = sum(
        1
        for d, entries in exchange_events.items()
        for (label, cal_name, _) in entries
        if not (d in bank_holidays and cal_name == "XLON")
    )
    total = bank_count + exchange_count
    summary = f"{total} event{'s' if total != 1 else ''} across {len(all_dates)} day{'s' if len(all_dates) != 1 else ''}"

    date_blocks = "".join(_date_block_html(d, bank_holidays, exchange_events) for d in all_dates)

    legend_html = """
    <tr>
      <td style="padding:12px 24px 4px">
        <span style="display:inline-flex;align-items:center;margin-right:16px">
          <span style="display:inline-block;background:#7c3aed;color:white;
                       font-size:10px;font-weight:700;letter-spacing:0.06em;
                       padding:2px 7px;border-radius:3px;text-transform:uppercase">
            UK Bank Holiday
          </span>
        </span>
        <span style="display:inline-flex;align-items:center">
          <span style="display:inline-block;background:#e0f2fe;color:#0369a1;
                       font-size:10px;font-weight:700;letter-spacing:0.06em;
                       padding:2px 7px;border-radius:3px;text-transform:uppercase">
            Closed
          </span>
          <span style="font-size:11px;color:#94a3b8;margin-left:6px">Exchange closure</span>
        </span>
      </td>
    </tr>"""

    section_header = f"""
    <tr>
      <td style="background:#f8fafc;padding:10px 24px;
                 border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0">
        <p style="margin:0;font-size:11px;font-weight:700;color:#64748b;
                  text-transform:uppercase;letter-spacing:0.08em">
          Trading &amp; Holidays
          <span style="font-weight:400;text-transform:none;letter-spacing:0;
                       margin-left:8px;font-size:11px;color:#94a3b8">
            {window_start.strftime("%a %-d %b")} &ndash; {window_end.strftime("%a %-d %b %Y")}
            &nbsp;&middot;&nbsp; {summary}
          </span>
        </p>
      </td>
    </tr>"""

    return f"""
    {section_header}
    <tr>
      <td style="background:white">
        <table width="100%" cellpadding="0" cellspacing="0">
          {legend_html}
          <tr><td style="height:4px"></td></tr>
        </table>
      </td>
    </tr>
    <tr><td style="background:#f1f5f9;height:4px"></td></tr>
    <tr>
      <td style="background:white">
        <table width="100%" cellpadding="0" cellspacing="0">
          {date_blocks}
          <tr><td style="height:8px"></td></tr>
        </table>
      </td>
    </tr>"""


def build_plain_section(exchange_events, bank_holidays, window_start, window_end):
    all_dates = sorted(set(exchange_events.keys()) | set(bank_holidays.keys()))
    lines = [f"Window: {window_start.strftime('%a %-d %b')} – {window_end.strftime('%a %-d %b %Y')}", ""]
    for d in all_dates:
        lines.append(d.strftime("%A %-d %B %Y"))
        if d in bank_holidays:
            lines.append(f"  - UK Bank Holiday: {bank_holidays[d]}")
        if d in exchange_events:
            for label, calendar_name, holiday_name in exchange_events[d]:
                if d in bank_holidays and calendar_name == "XLON":
                    continue
                lines.append(f"  - {label} closed: {holiday_name}")
        lines.append("")
    return "\n".join(lines)


def run(config) -> CheckResult:
    hol_config = config["holidays"]
    today = datetime.now(LONDON_TZ).date()
    lookahead = hol_config.get("lookahead_days", 7)
    window_start = today + timedelta(days=1)
    window_end = today + timedelta(days=lookahead)

    logging.info(f"Holidays window: {window_start} to {window_end}")

    bank_holidays = get_bank_holiday_events(hol_config, window_start, window_end)
    logging.info(f"UK bank holidays found: {len(bank_holidays)}")

    exchange_events = get_exchange_holiday_events(hol_config, window_start, window_end)

    triggered = bool(bank_holidays) or bool(exchange_events)
    logging.info(f"Holidays: {'TRIGGERED' if triggered else 'clear'}")

    rows_html = build_section_rows(exchange_events, bank_holidays, window_start, window_end)
    plain = build_plain_section(exchange_events, bank_holidays, window_start, window_end)

    return CheckResult(
        triggered=triggered,
        subject_tag="Trading & Holidays",
        rows_html=rows_html,
        plain=plain,
    )
