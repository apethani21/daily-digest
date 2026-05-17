from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from checks import CheckResult

LONDON_TZ = ZoneInfo("Europe/London")

WMO_EMOJI = {
    0:  "☀️",
    1:  "🌤",
    2:  "⛅",
    3:  "☁️",
    45: "🌫",
    48: "🌫",
    51: "🌦",
    53: "🌦",
    55: "🌦",
    61: "🌧",
    63: "🌧",
    65: "🌧",
    71: "🌨",
    73: "🌨",
    75: "🌨",
    77: "🌨",
    80: "🌦",
    81: "🌦",
    82: "🌦",
    85: "🌨",
    86: "🌨",
    95: "⛈",
    96: "⛈",
    99: "⛈",
}

WMO_LABEL = {
    0:  "Clear",
    1:  "Mainly clear",
    2:  "Partly cloudy",
    3:  "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm",
    99: "Thunderstorm",
}


def _wmo(code):
    code = code or 0
    return WMO_EMOJI.get(code, "🌡"), WMO_LABEL.get(code, "Unknown")


def fetch_forecast(lat, lon, days):
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max,windspeed_10m_max",
            "timezone": "Europe/London",
            "forecast_days": days,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def build_section_rows(days_data, location_name):
    now = datetime.now(LONDON_TZ)

    cols = ""
    for i, day in enumerate(days_data):
        dt = datetime.fromisoformat(day["date"]).replace(tzinfo=LONDON_TZ)
        day_name = "Today" if i == 0 else dt.strftime("%a")
        emoji, _ = _wmo(day["weathercode"])
        t_max = f"{day['t_max']:.0f}°"
        t_min = f"{day['t_min']:.0f}°"
        precip = day["precip_prob"]
        wind = day["wind_max"]

        precip_html = ""
        if precip is not None:
            precip_color = "#dc2626" if precip >= 70 else "#0369a1" if precip >= 30 else "#94a3b8"
            precip_html = f'<div style="font-size:11px;color:{precip_color};margin-top:3px">💧 {precip:.0f}%</div>'

        wind_html = ""
        if wind is not None:
            wind_html = f'<div style="font-size:11px;color:#94a3b8;margin-top:1px">💨 {wind:.0f}</div>'

        bg = "#f8fafc" if i == 0 else "white"
        border_left = "border-left:1px solid #f1f5f9;" if i > 0 else ""

        cols += f"""
          <td style="width:20%;text-align:center;padding:14px 4px;
                     background:{bg};vertical-align:top;{border_left}">
            <div style="font-size:11px;font-weight:700;color:#64748b;
                        text-transform:uppercase;letter-spacing:0.05em">{day_name}</div>
            <div style="font-size:26px;margin:6px 0 4px">{emoji}</div>
            <div style="font-size:16px;font-weight:700;color:#1e293b">{t_max}</div>
            <div style="font-size:12px;color:#94a3b8;margin-top:1px">{t_min}</div>
            {precip_html}
            {wind_html}
          </td>"""

    tz_label = "BST" if now.dst() and now.dst().total_seconds() > 0 else "GMT"

    section_header = f"""
    <tr>
      <td style="background:#f8fafc;padding:10px 24px 10px 21px;
                 border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;
                 border-left:3px solid #0891b2">
        <p style="margin:0;font-size:11px;font-weight:700;color:#64748b;
                  text-transform:uppercase;letter-spacing:0.08em">
          ☁️ {location_name} Forecast
          <span style="font-weight:400;text-transform:none;letter-spacing:0;
                       margin-left:8px;font-size:11px;color:#94a3b8">
            {now.strftime('%-d %b')} &middot; {now.strftime('%H:%M')} {tz_label}
          </span>
        </p>
      </td>
    </tr>"""

    return f"""
    {section_header}
    <tr>
      <td style="background:white;padding:0">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>{cols}</tr>
        </table>
      </td>
    </tr>"""


def build_plain_section(days_data, location_name):
    lines = [f"☁️ {location_name.upper()} FORECAST", ""]
    for i, day in enumerate(days_data):
        dt = datetime.fromisoformat(day["date"]).replace(tzinfo=LONDON_TZ)
        day_name = "Today    " if i == 0 else dt.strftime("%a      ")
        emoji, label = _wmo(day["weathercode"])
        t_max = f"{day['t_max']:.0f}°"
        t_min = f"{day['t_min']:.0f}°"
        precip = f"💧{day['precip_prob']:.0f}%" if day["precip_prob"] is not None else ""
        wind = f"💨{day['wind_max']:.0f} km/h" if day["wind_max"] is not None else ""
        lines.append(f"  {day_name}  {emoji} {label:<18}  {t_max}/{t_min}  {precip}  {wind}")
    return "\n".join(lines)


def run(config) -> CheckResult:
    w_config = config.get("weather", {})
    loc = w_config.get("location", {"name": "London", "lat": 51.5074, "lon": -0.1278})
    days = w_config.get("forecast_days", 5)

    data = fetch_forecast(loc["lat"], loc["lon"], days)
    daily = data["daily"]

    days_data = []
    for i in range(len(daily["time"])):
        days_data.append({
            "date": daily["time"][i],
            "weathercode": daily["weathercode"][i],
            "t_max": daily["temperature_2m_max"][i] or 0,
            "t_min": daily["temperature_2m_min"][i] or 0,
            "precip_prob": daily["precipitation_probability_max"][i],
            "wind_max": daily["windspeed_10m_max"][i],
        })

    rows_html = build_section_rows(days_data, loc["name"])
    plain = build_plain_section(days_data, loc["name"])

    return CheckResult(
        triggered=False,
        always_include=True,
        subject_tag="",
        rows_html=rows_html,
        plain=plain,
    )
