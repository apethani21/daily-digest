import io
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests

from checks import CheckResult

LONDON_TZ = ZoneInfo("Europe/London")

AQI_SCALE = [
    (40,  "GOOD",         "#16a34a"),
    (60,  "MODERATE",     "#d97706"),
    (80,  "POOR",         "#ea580c"),
    (100, "VERY POOR",    "#dc2626"),
]
EXTREMELY_POOR_COLOR = "#7c3aed"

POLLEN_LABELS = ["none", "very low", "low", "medium", "high", "very high"]
POLLEN_COLORS = ["#6b7280", "#6b7280", "#6b7280", "#d97706", "#ea580c", "#dc2626"]
POLLEN_THRESHOLDS = {
    "alder_pollen":   [0, 9, 49, 99, 199],
    "birch_pollen":   [0, 9, 49, 99, 199],
    "grass_pollen":   [0, 4,  9, 49,  99],
    "mugwort_pollen": [0, 9, 49, 99, 199],
}
POLLEN_NAMES = {
    "alder_pollen":   "Alder",
    "birch_pollen":   "Birch",
    "grass_pollen":   "Grass",
    "mugwort_pollen": "Mugwort",
}
POLLEN_SERIES_COLORS = {
    "alder_pollen":   "#84cc16",
    "birch_pollen":   "#f59e0b",
    "grass_pollen":   "#22c55e",
    "mugwort_pollen": "#c084fc",
}

UV_SCALE = [
    (2,  "Low",       "#6b7280"),
    (5,  "Moderate",  "#d97706"),
    (7,  "High",      "#ea580c"),
    (10, "Very High", "#dc2626"),
]
UV_EXTREME_COLOR = "#7c3aed"

DUST_SCALE = [
    (10, "Normal",            "#6b7280"),
    (20, "Slightly Elevated", "#d97706"),
    (50, "Elevated",          "#ea580c"),
]
DUST_HIGH_COLOR = "#dc2626"

FORECAST_HOURS = [9, 12, 15, 18, 21]
LOC_ICONS  = ["🏠", "💼"]
LOC_COLORS = ["#3b82f6", "#8b5cf6"]


def aqi_label_color(aqi):
    for threshold, label, color in AQI_SCALE:
        if aqi <= threshold:
            return label, color
    return "EXTREMELY POOR", EXTREMELY_POOR_COLOR


def uv_label_color(uv):
    for threshold, label, color in UV_SCALE:
        if uv <= threshold:
            return label, color
    return "Extreme", UV_EXTREME_COLOR


def dust_label_color(dust):
    for threshold, label, color in DUST_SCALE:
        if dust <= threshold:
            return label, color
    return "High", DUST_HIGH_COLOR


def pollen_level(pollen_type, value):
    if value is None:
        return 0
    for i, t in enumerate(POLLEN_THRESHOLDS.get(pollen_type, POLLEN_THRESHOLDS["birch_pollen"])):
        if value <= t:
            return i
    return 5


def fetch_with_retry(url, params, retries=1):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt < retries:
                logging.warning(f"Request failed (attempt {attempt + 1}), retrying: {e}")
            else:
                raise


def fetch_location_data(lat, lon, include_pollen=False):
    today = datetime.now(LONDON_TZ).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(LONDON_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    hourly = "european_aqi,pm2_5"
    if include_pollen:
        hourly += ",alder_pollen,birch_pollen,grass_pollen,mugwort_pollen"
    return fetch_with_retry(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        {
            "latitude": lat,
            "longitude": lon,
            "current": "european_aqi,pm2_5,uv_index,dust",
            "hourly": hourly,
            "start_date": today,
            "end_date": tomorrow,
            "timezone": "Europe/London",
        },
    )


def extract_forecast(data, hours):
    times = data["hourly"]["time"]
    aqi_vals = data["hourly"]["european_aqi"]
    pm25_vals = data["hourly"]["pm2_5"]
    today = datetime.now(LONDON_TZ).strftime("%Y-%m-%d")
    result = {}
    for h in hours:
        key = f"{today}T{h:02d}:00"
        if key in times:
            idx = times.index(key)
            result[h] = {"aqi": aqi_vals[idx] or 0, "pm25": pm25_vals[idx] or 0}
    return result


def extract_hourly_series(data):
    now = datetime.now(LONDON_TZ)
    cutoff = now + timedelta(hours=24)
    times = data["hourly"]["time"]
    aqi_vals = data["hourly"]["european_aqi"]
    series = []
    for t_str, aqi in zip(times, aqi_vals):
        dt = datetime.fromisoformat(t_str).replace(tzinfo=LONDON_TZ)
        if now <= dt <= cutoff:
            series.append((dt, aqi or 0))
    return series


def extract_pollen(data):
    now = datetime.now(LONDON_TZ)
    current_hour = now.strftime("%Y-%m-%dT%H:00")
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    idx = times.index(current_hour) if current_hour in times else 0
    return {pt: (hourly.get(pt) or [None])[idx] for pt in POLLEN_NAMES}


def extract_pollen_series(data):
    now = datetime.now(LONDON_TZ)
    cutoff = now + timedelta(hours=24)
    times = data["hourly"]["time"]
    result = {}
    for pt in POLLEN_NAMES:
        vals = data["hourly"].get(pt) or []
        series = []
        for t_str, v in zip(times, vals):
            dt = datetime.fromisoformat(t_str).replace(tzinfo=LONDON_TZ)
            if now <= dt <= cutoff:
                series.append((dt, pollen_level(pt, v or 0)))
        if series and any(lv > 0 for _, lv in series):
            result[pt] = series
    return result


def any_threshold_exceeded(locations, pollen, uv_index, dust, config):
    t = config["thresholds"]
    for loc in locations:
        if loc["aqi"] > t["aqi_max"] or loc["pm25"] > t["pm25_max"]:
            return True
    if any(pollen_level(pt, v) >= t["pollen_min"] for pt, v in pollen.items()):
        return True
    if uv_index is not None and uv_index >= t.get("uv_index_max", 6):
        return True
    if dust is not None and dust >= t.get("dust_max", 20):
        return True
    return False


def generate_forecast_chart(locations, pollen_series=None):
    fig, ax = plt.subplots(figsize=(7, 2.8))
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f8fafc")

    bands = [
        (0,   40,  "#dcfce7"),
        (40,  60,  "#fef9c3"),
        (60,  80,  "#ffedd5"),
        (80,  100, "#fee2e2"),
        (100, 150, "#ede9fe"),
    ]
    for y0, y1, color in bands:
        ax.axhspan(y0, y1, color=color, alpha=0.6, linewidth=0)

    all_aqi = []
    for loc, color in zip(locations, LOC_COLORS):
        series = loc.get("hourly_series", [])
        if not series:
            continue
        xs = [dt for dt, _ in series]
        ys = [aqi for _, aqi in series]
        all_aqi.extend(ys)
        ax.plot(xs, ys, color=color, linewidth=2, label=loc["name"], zorder=3)
        ax.fill_between(xs, ys, alpha=0.08, color=color, zorder=2)

    now = datetime.now(LONDON_TZ)
    ax.axvline(now, color="#94a3b8", linewidth=1, linestyle="--", zorder=4)
    ax.axhline(60, color="#d97706", linewidth=0.8, linestyle=":", alpha=0.7, zorder=4)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=LONDON_TZ))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=3, tz=LONDON_TZ))
    plt.setp(ax.get_xticklabels(), fontsize=9, color="#334155")

    top = max(max(all_aqi, default=0) * 1.15, 65)
    ax.set_ylim(0, top)
    ax.yaxis.set_major_locator(plt.MultipleLocator(20))
    plt.setp(ax.get_yticklabels(), fontsize=9, color="#334155")
    ax.set_ylabel("AQI", fontsize=9, color="#64748b")

    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#94a3b8")
    ax.tick_params(colors="#94a3b8", which="both")
    ax.grid(True, color="#cbd5e1", linewidth=0.5, zorder=1)

    ax2 = None
    if pollen_series:
        ax2 = ax.twinx()
        for pt, series in pollen_series.items():
            xs = [dt for dt, _ in series]
            ys = [lv for _, lv in series]
            ax2.plot(xs, ys, color=POLLEN_SERIES_COLORS[pt], linewidth=1.5,
                     linestyle="--", label=POLLEN_NAMES[pt], zorder=3, alpha=0.85)
        ax2.set_ylim(0, 5)
        ax2.set_yticks(range(6))
        ax2.set_yticklabels(POLLEN_LABELS, fontsize=8, color="#64748b")
        ax2.set_ylabel("Pollen", fontsize=9, color="#64748b")
        ax2.spines[["top"]].set_visible(False)
        ax2.spines[["right"]].set_color("#94a3b8")
        ax2.tick_params(colors="#94a3b8", which="both")

    handles, labels = ax.get_legend_handles_labels()
    if ax2:
        h2, l2 = ax2.get_legend_handles_labels()
        handles += h2
        labels += l2
    ax.legend(handles, labels, loc="upper right", fontsize=9,
              framealpha=0.9, edgecolor="#cbd5e1", labelcolor="#475569")

    title = "24-hour forecast — AQI" + (" & pollen" if pollen_series else "")
    ax.set_title(title, fontsize=10, color="#475569", loc="left", pad=8, fontweight="normal")

    fig.autofmt_xdate(rotation=0, ha="center")
    plt.tight_layout(pad=0.8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _aqi_badge(aqi):
    label, color = aqi_label_color(aqi)
    return (
        f'<span style="background:{color};color:white;padding:2px 8px;'
        f'border-radius:4px;font-size:12px;font-weight:600;letter-spacing:0.05em">'
        f'{label}</span>'
    )


def build_section_rows(locations, pollen, uv_index, dust, config, chart_png_available=False):
    t = config["thresholds"]
    aqi_hit = any(loc["aqi"] > t["aqi_max"] or loc["pm25"] > t["pm25_max"] for loc in locations)
    uv_hit = uv_index is not None and uv_index >= t.get("uv_index_max", 6)
    dust_hit = dust is not None and dust >= t.get("dust_max", 20)

    section_header = """
    <tr>
      <td style="background:#f8fafc;padding:10px 24px 10px 21px;
                 border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;
                 border-left:3px solid #16a34a">
        <p style="margin:0;font-size:11px;font-weight:700;color:#64748b;
                  text-transform:uppercase;letter-spacing:0.08em">Air Quality &amp; Pollen</p>
      </td>
    </tr>"""

    aq_section = ""
    if aqi_hit:
        cards_html = ""
        for icon, loc in zip(LOC_ICONS, locations):
            cards_html += f"""
            <tr>
              <td style="padding:12px 24px;border-bottom:1px solid #f1f5f9">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="font-size:15px;font-weight:600;color:#1e293b">
                      {icon}&nbsp;&nbsp;{loc['name']}
                    </td>
                    <td align="right">{_aqi_badge(loc['aqi'])}</td>
                  </tr>
                  <tr>
                    <td colspan="2" style="padding-top:4px;font-size:13px;color:#64748b">
                      PM2.5 &nbsp;<strong style="color:#334155">{loc['pm25']:.0f}&thinsp;μg/m³</strong>
                      &nbsp;&nbsp;&middot;&nbsp;&nbsp;
                      AQI &nbsp;<strong style="color:#334155">{loc['aqi']:.0f}</strong>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

        forecast_header = "".join(
            f'<th style="padding:8px 12px;font-size:12px;font-weight:600;'
            f'color:#64748b;text-align:center">{h:02d}:00</th>'
            for h in FORECAST_HOURS
        )
        forecast_rows = ""
        for icon, loc in zip(LOC_ICONS, locations):
            cells = "".join(
                (
                    f'<td style="padding:8px 12px;text-align:center">'
                    f'<span style="color:{aqi_label_color(loc["forecast"][h]["aqi"])[1]};'
                    f'font-weight:600;font-size:13px">{loc["forecast"][h]["aqi"]:.0f}</span></td>'
                    if h in loc.get("forecast", {})
                    else '<td style="padding:8px 12px;text-align:center;color:#cbd5e1">—</td>'
                )
                for h in FORECAST_HOURS
            )
            forecast_rows += f"""
            <tr style="border-bottom:1px solid #f1f5f9">
              <td style="padding:8px 12px;font-size:13px;color:#475569;white-space:nowrap">
                {icon}&nbsp;{loc['name']}
              </td>
              {cells}
            </tr>"""

        chart_html = ""
        if chart_png_available:
            chart_html = """
            <tr><td style="background:#f1f5f9;height:6px"></td></tr>
            <tr><td style="background:white;padding:16px 24px 20px">
              <p style="margin:0 0 12px;font-size:12px;font-weight:700;text-transform:uppercase;
                        letter-spacing:0.08em;color:#94a3b8">Forecast</p>
              <img src="cid:forecast_chart"
                   width="520" style="display:block;max-width:100%;border-radius:6px" alt="AQI forecast chart">
            </td></tr>"""

        aq_section = f"""
        <tr><td style="background:white">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:16px 24px 4px">
              <p style="margin:0;font-size:12px;font-weight:700;text-transform:uppercase;
                        letter-spacing:0.08em;color:#94a3b8">Air quality</p>
            </td></tr>
            {cards_html}
          </table>
        </td></tr>
        <tr><td style="background:#f1f5f9;height:6px"></td></tr>
        <tr><td style="background:white">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:16px 24px 4px">
              <p style="margin:0;font-size:12px;font-weight:700;text-transform:uppercase;
                        letter-spacing:0.08em;color:#94a3b8">Today's forecast
                <span style="font-weight:400;text-transform:none;letter-spacing:0"> (AQI)</span>
              </p>
            </td></tr>
            <tr>
              <th style="padding:8px 12px;font-size:12px;font-weight:600;
                         color:#64748b;text-align:left"></th>
              {forecast_header}
            </tr>
            {forecast_rows}
          </table>
        </td></tr>
        {chart_html}"""

    uv_section = ""
    if uv_hit:
        uv_label, uv_color = uv_label_color(uv_index)
        uv_section = f"""
        <tr><td style="background:#f1f5f9;height:6px"></td></tr>
        <tr><td style="background:white">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:16px 24px 4px">
              <p style="margin:0;font-size:12px;font-weight:700;text-transform:uppercase;
                        letter-spacing:0.08em;color:#94a3b8">☀️ UV Index</p>
            </td></tr>
            <tr>
              <td style="padding:8px 24px 16px">
                <span style="font-size:28px;font-weight:700;color:{uv_color}">{uv_index:.0f}</span>
                <span style="font-size:13px;font-weight:600;color:{uv_color};margin-left:8px">{uv_label.upper()}</span>
              </td>
            </tr>
          </table>
        </td></tr>"""

    dust_section = ""
    if dust_hit:
        dust_label, dust_color = dust_label_color(dust)
        dust_section = f"""
        <tr><td style="background:#f1f5f9;height:6px"></td></tr>
        <tr><td style="background:white">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:16px 24px 4px">
              <p style="margin:0;font-size:12px;font-weight:700;text-transform:uppercase;
                        letter-spacing:0.08em;color:#94a3b8">🏜️ Dust</p>
            </td></tr>
            <tr>
              <td style="padding:8px 24px 16px">
                <span style="font-size:28px;font-weight:700;color:{dust_color}">{dust:.0f}</span>
                <span style="font-size:13px;color:#64748b;margin-left:4px">μg/m³</span>
                <span style="font-size:13px;font-weight:600;color:{dust_color};margin-left:8px">{dust_label.upper()}</span>
              </td>
            </tr>
          </table>
        </td></tr>"""

    pollen_rows = ""
    for pt, v in pollen.items():
        lv = pollen_level(pt, v)
        color = POLLEN_COLORS[lv]
        pollen_rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9">
          <td style="padding:8px 24px;font-size:13px;color:#475569">{POLLEN_NAMES[pt]}</td>
          <td style="padding:8px 24px;font-size:13px;font-weight:600;color:{color};text-align:right">
            {POLLEN_LABELS[lv].capitalize()} ({lv}/5)
          </td>
        </tr>"""

    spacer = '<tr><td style="background:#f1f5f9;height:6px"></td></tr>' if (aqi_hit or uv_hit or dust_hit) else ""

    pollen_section = f"""
    {spacer}
    <tr><td style="background:white">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="padding:16px 24px 4px">
          <p style="margin:0;font-size:12px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.08em;color:#94a3b8">🌾 Pollen</p>
        </td></tr>
        {pollen_rows}
        <tr><td style="height:8px"></td></tr>
      </table>
    </td></tr>"""

    return section_header + aq_section + uv_section + dust_section + pollen_section


def build_plain_section(locations, pollen, uv_index, dust, config):
    now = datetime.now(LONDON_TZ)
    tz_label = "BST" if now.dst() and now.dst().total_seconds() > 0 else "GMT"
    t = config["thresholds"]
    lines = [f"Checked at {now.strftime('%H:%M')} {tz_label}", ""]
    aqi_hit = any(loc["aqi"] > t["aqi_max"] or loc["pm25"] > t["pm25_max"] for loc in locations)
    uv_hit = uv_index is not None and uv_index >= t.get("uv_index_max", 6)
    dust_hit = dust is not None and dust >= t.get("dust_max", 20)
    if aqi_hit:
        for icon, loc in zip(LOC_ICONS, locations):
            label, _ = aqi_label_color(loc["aqi"])
            lines += [
                f"{icon} {loc['name']}: {label}",
                f"   PM2.5: {loc['pm25']:.0f} μg/m³",
                f"   AQI:   {loc['aqi']:.0f}",
                "",
            ]
    if uv_hit:
        label, _ = uv_label_color(uv_index)
        lines += [f"☀️ UV Index: {uv_index:.0f} ({label.upper()})", ""]
    if dust_hit:
        label, _ = dust_label_color(dust)
        lines += [f"🏜️ Dust: {dust:.0f} μg/m³ ({label.upper()})", ""]
    lines.append("🌾 Pollen:")
    for pt, v in pollen.items():
        lv = pollen_level(pt, v)
        lines.append(f"   {POLLEN_NAMES[pt]}: {POLLEN_LABELS[lv].capitalize()} ({lv}/5)")
    return "\n".join(lines)


def run(config) -> CheckResult:
    aq_config = config["air_quality"]
    locations = []
    uv_index = None
    dust = None

    for i, loc in enumerate(aq_config["locations"]):
        try:
            data = fetch_location_data(loc["lat"], loc["lon"], include_pollen=(i == 0))
            cur = data["current"]
            entry = {
                "name": loc["name"],
                "aqi": cur.get("european_aqi") or 0,
                "pm25": cur.get("pm2_5") or 0,
                "forecast": extract_forecast(data, FORECAST_HOURS),
                "hourly_series": extract_hourly_series(data),
                "_raw": data if i == 0 else None,
            }
            if i == 0:
                uv_index = cur.get("uv_index")
                dust = cur.get("dust")
            locations.append(entry)
            logging.info(f"AQ {loc['name']}: AQI={entry['aqi']}, PM2.5={entry['pm25']}")
        except Exception as e:
            logging.error(f"Failed to fetch {loc['name']}: {e}")

    if not locations:
        raise RuntimeError("All location fetches failed")

    pollen = {}
    try:
        first_raw = locations[0].get("_raw")
        pollen = extract_pollen(first_raw) if first_raw else {}
    except Exception as e:
        logging.warning(f"Pollen fetch failed: {e}")
        pollen = {pt: None for pt in POLLEN_NAMES}

    logging.info(f"UV Index: {uv_index}, Dust: {dust} μg/m³")

    triggered = any_threshold_exceeded(locations, pollen, uv_index, dust, aq_config)
    logging.info(f"AQ threshold: {'EXCEEDED' if triggered else 'clear'}")

    t = aq_config["thresholds"]
    tags = []
    if any(loc["aqi"] > t["aqi_max"] or loc["pm25"] > t["pm25_max"] for loc in locations):
        tags.append("⚠️ Air Quality")
    if any(pollen_level(pt, v) >= t["pollen_min"] for pt, v in pollen.items()):
        tags.append("🌾 Pollen")
    if uv_index is not None and uv_index >= t.get("uv_index_max", 6):
        tags.append("☀️ UV")
    if dust is not None and dust >= t.get("dust_max", 20):
        tags.append("🏜️ Dust")
    subject_tag = " & ".join(tags) if tags else "Air Quality & Pollen"

    chart_png = None
    if triggered:
        pollen_ser = {}
        try:
            first_raw = locations[0].get("_raw")
            if first_raw:
                pollen_ser = extract_pollen_series(first_raw)
        except Exception as e:
            logging.warning(f"Pollen series extraction failed: {e}")
        try:
            chart_png = generate_forecast_chart(locations, pollen_ser or None)
        except Exception as e:
            logging.warning(f"Chart generation failed (continuing without chart): {e}")

    rows_html = build_section_rows(locations, pollen, uv_index, dust, aq_config, chart_png is not None)
    plain = build_plain_section(locations, pollen, uv_index, dust, aq_config)

    return CheckResult(
        triggered=triggered,
        subject_tag=subject_tag,
        rows_html=rows_html,
        plain=plain,
        chart_png=chart_png,
    )
