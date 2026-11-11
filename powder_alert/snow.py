"""Snow data sources.

Every source returns a SnowReading in INCHES so the decision engine can
compare apples to apples:

  * SNOTEL (NRCS)       – measured snow depth at high-elevation stations
  * Resort snow reports – the resort's own 24h study-plot number (optional scraper)
  * Open-Meteo          – modeled snowfall, last N hours + next 24h, downscaled to elevation
  * NWS gridpoints      – official National Weather Service snowfall forecast
"""
from __future__ import annotations

import csv
import io
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .http import get

CM_PER_IN = 2.54
MM_PER_IN = 25.4


@dataclass
class SnowReading:
    source: str                 # e.g. "SNOTEL Loveland Basin (602)"
    kind: str                   # "observed" | "modeled" | "forecast" | "resort"
    new_snow_in: float | None   # inches in the lookback window (or forecast window)
    depth_in: float | None = None
    detail: str = ""
    ok: bool = True
    extra: dict = field(default_factory=dict)


# ─────────────────────────── SNOTEL ───────────────────────────

SNOTEL_CSV = (
    "https://wcc.sc.egov.usda.gov/reportGenerator/view_csv/customSingleStationReport/"
    "hourly/start_of_period/{triplet}|id=%22%22|name/-{hours},0/"
    "SNWD::value,WTEQ::value,TOBS::value"
)
AWDB_REST = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"


def _rolling_median(vals, k=3):
    out = []
    for i in range(len(vals)):
        win = [v for v in vals[max(0, i - k + 1): i + 1] if v is not None]
        out.append(statistics.median(win) if win else None)
    return out


def snotel_new_snow(rows, lookback_hours=24, swe_ratio=12):
    """rows: list of (datetime, depth_in, swe_in) oldest→newest.

    New snow = the biggest rise in (3-hr median-smoothed) depth inside the
    lookback window. Using "biggest rise" instead of "now minus then" keeps
    fresh snow that has already started to settle, and the median filter
    removes the ±1" sensor noise SNOTEL depth sensors are known for.
    """
    rows = [r for r in rows if r[1] is not None]
    if len(rows) < 4:
        return None, None, None
    latest_t = rows[-1][0]
    start_t = latest_t - timedelta(hours=lookback_hours)
    window = [r for r in rows if r[0] >= start_t - timedelta(hours=1)]
    depths = _rolling_median([r[1] for r in window])
    best, running_min = 0.0, None
    for d in depths:
        if d is None:
            continue
        running_min = d if running_min is None else min(running_min, d)
        best = max(best, d - running_min)
    swes = [r[2] for r in window if r[2] is not None]
    swe_gain = max(0.0, swes[-1] - min(swes)) if len(swes) >= 2 else None
    swe_est = round(swe_gain * swe_ratio, 1) if swe_gain is not None else None
    return round(best, 1), depths[-1], swe_est


def parse_snotel_csv(text):
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    reader = csv.reader(io.StringIO("\n".join(lines)))
    header = next(reader)
    idx_d = next(i for i, h in enumerate(header) if "Snow Depth" in h)
    idx_w = next((i for i, h in enumerate(header) if "Snow Water" in h), None)
    rows = []
    for rec in reader:
        try:
            t = datetime.strptime(rec[0], "%Y-%m-%d %H:%M")
        except (ValueError, IndexError):
            continue
        def num(i):
            try:
                return float(rec[i]) if i is not None and rec[i] != "" else None
            except (ValueError, IndexError):
                return None
        rows.append((t, num(idx_d), num(idx_w)))
    return rows


def parse_awdb_json(data):
    series = {}
    for station in data:
        for el in station.get("data", []):
            code = el["stationElement"]["elementCode"]
            for v in el.get("values", []):
                series.setdefault(v["date"], {})[code] = v.get("value")
    rows = []
    for d in sorted(series):
        try:
            t = datetime.strptime(d[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        rows.append((t, series[d].get("SNWD"), series[d].get("WTEQ")))
    return rows


def fetch_snotel(triplet, lookback_hours=24, swe_ratio=12, name=None):
    hours = lookback_hours + 24
    label = f"SNOTEL {name or triplet}"
    rows = []
    try:
        rows = parse_snotel_csv(get(SNOTEL_CSV.format(triplet=triplet, hours=hours)).text)
    except Exception as e:  # fall back to the AWDB REST API
        err = e
        try:
            end = datetime.now(timezone.utc)
            beg = end - timedelta(hours=hours + 8)
            data = get(AWDB_REST, params={
                "stationTriplets": triplet, "elements": "SNWD,WTEQ", "duration": "HOURLY",
                "beginDate": beg.strftime("%Y-%m-%d %H:%M"),
                "endDate": end.strftime("%Y-%m-%d %H:%M"),
            }).json()
            rows = parse_awdb_json(data)
        except Exception as e2:
            return SnowReading(label, "observed", None, ok=False, detail=f"unavailable ({err}; {e2})")
    new, depth, swe_est = snotel_new_snow(rows, lookback_hours, swe_ratio)
    if new is None:
        return SnowReading(label, "observed", None, ok=False, detail="no recent depth data")
    detail = f'{new}" new / {depth:.0f}" base'
    if swe_est is not None:
        detail += f' (SWE suggests ~{swe_est}")'
    return SnowReading(label, "observed", new, depth, detail, extra={"swe_est_in": swe_est})


# ─────────────────────────── Open-Meteo ───────────────────────────

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"


def _to_inches(values, unit):
    unit = (unit or "cm").lower()
    f = 1.0 if unit.startswith("inch") else (1 / MM_PER_IN if unit == "mm" else 1 / CM_PER_IN)
    return [(v or 0.0) * f for v in values]


def parse_open_meteo(data, now, lookback_hours=24, ahead_hours=24):
    h = data["hourly"]
    times = [datetime.fromisoformat(t) for t in h["time"]]
    snow = _to_inches(h["snowfall"], data.get("hourly_units", {}).get("snowfall"))
    past = sum(s for t, s in zip(times, snow) if now - timedelta(hours=lookback_hours) < t <= now)
    nxt = sum(s for t, s in zip(times, snow) if now < t <= now + timedelta(hours=ahead_hours))
    return round(past, 1), round(nxt, 1)


def fetch_open_meteo(lat, lon, elevation_m, tz, now_local, lookback_hours=24):
    params = {
        "latitude": lat, "longitude": lon, "hourly": "snowfall,snow_depth",
        "past_days": 2, "forecast_days": 2, "timezone": tz,
    }
    if elevation_m:
        params["elevation"] = elevation_m
    try:
        data = get(OPEN_METEO, params=params).json()
        past, nxt = parse_open_meteo(data, now_local.replace(tzinfo=None), lookback_hours)
    except Exception as e:
        bad = SnowReading("Open-Meteo model", "modeled", None, ok=False, detail=f"unavailable ({e})")
        return bad, SnowReading("Open-Meteo forecast", "forecast", None, ok=False)
    return (
        SnowReading("Open-Meteo model (past)", "modeled", past, detail=f'{past}" modeled last {lookback_hours}h'),
        SnowReading("Open-Meteo forecast", "forecast", nxt, detail=f'{nxt}" next 24h'),
    )


# ─────────────────────────── NWS ───────────────────────────

NWS_POINTS = "https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?")


def parse_nws_snowfall(grid, now_utc, ahead_hours=24):
    prop = grid["properties"]["snowfallAmount"]
    uom = prop.get("uom", "wmoUnit:mm")
    total = 0.0
    end = now_utc + timedelta(hours=ahead_hours)
    for v in prop.get("values", []):
        start_s, dur_s = v["validTime"].split("/")
        start = datetime.fromisoformat(start_s)
        m = _DUR.match(dur_s)
        hrs = int(m.group(1) or 0) * 24 + int(m.group(2) or 0) or 1
        stop = start + timedelta(hours=hrs)
        overlap = (min(stop, end) - max(start, now_utc)).total_seconds() / 3600
        if overlap > 0 and v.get("value"):
            total += v["value"] * overlap / hrs
    inches = total / MM_PER_IN if "mm" in uom else (total / CM_PER_IN if "cm" in uom else total)
    return round(inches, 1)


def fetch_nws(lat, lon):
    try:
        pts = get(NWS_POINTS.format(lat=lat, lon=lon)).json()
        grid = get(pts["properties"]["forecastGridData"]).json()
        inches = parse_nws_snowfall(grid, datetime.now(timezone.utc))
        return SnowReading("NWS forecast", "forecast", inches, detail=f'{inches}" next 24h')
    except Exception as e:
        return SnowReading("NWS forecast", "forecast", None, ok=False, detail=f"unavailable ({e})")


# ─────────────────────────── Resort report ───────────────────────────

def parse_resort_report(html, regex):
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    m = re.search(regex, text, re.I)
    return float(m.group(1)) if m else None


def fetch_resort_report(name, report_cfg):
    label = f"{name} snow report"
    try:
        val = parse_resort_report(get(report_cfg["url"]).text, report_cfg["regex"])
        if val is None:
            return SnowReading(label, "resort", None, ok=False, detail="24h number not found on page")
        return SnowReading(label, "resort", val, detail=f'{val}" reported 24h')
    except Exception as e:
        return SnowReading(label, "resort", None, ok=False, detail=f"unavailable ({e})")
