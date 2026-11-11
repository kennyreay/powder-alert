"""Decision engine: combine snow + traffic into a verdict per resort.

Verdicts (strongest first)
  GO_NOW     – ≥ min new snow AND traffic is low right now
  POWDER     – ≥ min new snow but traffic is heavy (includes best time to leave)
  HEADS_UP   – evening only: ≥ min snow FORECAST for the next 24h
  NO         – nothing to see
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from . import snow, traffic

GO_NOW, POWDER, HEADS_UP, NO = "GO_NOW", "POWDER", "HEADS_UP", "NO"
RANK = {GO_NOW: 3, POWDER: 2, HEADS_UP: 1, NO: 0}


@dataclass
class ResortResult:
    name: str
    region: str = ""
    readings: list = field(default_factory=list)
    new_snow_in: float | None = None
    new_snow_basis: str = ""
    forecast_in: float | None = None
    drive: traffic.Drive | None = None
    better_departure: traffic.Drive | None = None
    verdict: str = NO
    reasons: list = field(default_factory=list)

    def to_dict(self):
        d = lambda x: None if x is None else {
            "minutes": round(x.minutes), "free_flow_minutes": round(x.free_flow_minutes),
            "ratio": round(x.ratio, 2), "delay_min": round(x.delay_min), "source": x.source,
            "depart": x.depart.isoformat() if x.depart else None, "detail": x.detail}
        return {
            "name": self.name, "region": self.region, "verdict": self.verdict, "new_snow_in": self.new_snow_in,
            "new_snow_basis": self.new_snow_basis, "forecast_in": self.forecast_in,
            "drive": d(self.drive), "better_departure": d(self.better_departure),
            "reasons": self.reasons,
            "readings": [{"source": r.source, "kind": r.kind, "new_snow_in": r.new_snow_in,
                          "depth_in": r.depth_in, "detail": r.detail, "ok": r.ok}
                         for r in self.readings],
        }


def best_snow_estimate(readings):
    """Resort report > SNOTEL measured > model. Returns (inches, basis)."""
    by = lambda k: [r.new_snow_in for r in readings if r.ok and r.kind == k and r.new_snow_in is not None]
    if by("resort"):
        return max(by("resort")), "resort report"
    if by("observed"):
        return round(statistics.mean(by("observed")), 1), "SNOTEL measured"
    if by("modeled"):
        return max(by("modeled")), "weather model (no measurements available)"
    return None, "no data"


def region_of(resort, cfg):
    """Return (region_cfg, region_key). Falls back to a top-level `home` for old configs."""
    regions = cfg.get("regions") or {}
    key = resort.get("region")
    if key in regions:
        return regions[key], key
    return {"label": "", "home": cfg.get("home"), "traffic_profile": "i70", "enabled": True}, key or ""


CLOSURE_RATIO = 2.5   # drive ≥ 2.5× free-flow usually means a closed canyon/pass or a detour


def gather_snow(resort, cfg, now_local):
    rules, src = cfg["rules"], cfg["sources"]
    lb = rules["lookback_hours"]
    readings = []
    if src.get("resort_reports") and resort.get("report"):
        readings.append(snow.fetch_resort_report(resort["name"], resort["report"]))
    if src.get("snotel"):
        for trip in resort.get("snotel", []):
            readings.append(snow.fetch_snotel(trip, lb, rules.get("snotel_swe_ratio", 12)))
    if src.get("open_meteo"):
        readings.extend(snow.fetch_open_meteo(resort["lat"], resort["lon"], resort.get("elevation_m"),
                                              cfg["timezone"], now_local, lb))
    if src.get("nws"):
        readings.append(snow.fetch_nws(resort["lat"], resort["lon"]))
    return readings


def evaluate(resort, readings, cfg, now_local, mode, drive_fn=None, depart_fn=None):
    """Pure decision logic (no network unless traffic fns hit it) — easy to test."""
    rules = cfg["rules"]
    provider = cfg["sources"].get("traffic", "heuristic")
    drive_fn = drive_fn or traffic.drive_now
    depart_fn = depart_fn or traffic.best_departure
    region, _ = region_of(resort, cfg)
    home, profile = region["home"], region.get("traffic_profile", "i70")
    res = ResortResult(resort["name"], region.get("label", ""), readings)
    res.new_snow_in, res.new_snow_basis = best_snow_estimate(readings)
    fc = [r.new_snow_in for r in readings if r.ok and r.kind == "forecast" and r.new_snow_in is not None]
    res.forecast_in = round(statistics.mean(fc), 1) if fc else None

    need = rules["min_new_snow_in"]
    powder = res.new_snow_in is not None and res.new_snow_in >= need

    if mode == "morning":
        if not powder:
            res.reasons.append(f'{res.new_snow_in if res.new_snow_in is not None else "?"}" new '
                               f'(< {need}") per {res.new_snow_basis}')
            return res
        res.drive = drive_fn(home, resort, now_local, powder=True, provider=provider, profile=profile)
        low = (res.drive.ratio <= rules["max_traffic_ratio"]
               and res.drive.delay_min <= rules["max_traffic_delay_min"])
        res.reasons.append(f'{res.new_snow_in}" new per {res.new_snow_basis}')
        if low:
            res.verdict = GO_NOW
            res.reasons.append(f"traffic light: {res.drive.minutes:.0f} min (+{res.drive.delay_min:.0f})")
        else:
            res.verdict = POWDER
            res.reasons.append(f"traffic heavy: {res.drive.minutes:.0f} min (+{res.drive.delay_min:.0f})")
            if res.drive.ratio >= CLOSURE_RATIO:
                res.reasons.append("possible road closure / avalanche control — check UDOT/CDOT")
            better = depart_fn(home, resort, now_local, rules["departure_search_hours"],
                               rules["departure_step_min"], powder=True, provider=provider, profile=profile)
            if better and better.minutes < res.drive.minutes - 10:
                res.better_departure = better
    elif mode == "evening":
        if rules.get("forecast_heads_up") and res.forecast_in is not None and res.forecast_in >= need:
            res.verdict = HEADS_UP
            res.reasons.append(f'{res.forecast_in}" forecast next 24h')
    return res


def in_window(now: datetime, window: str):
    a, b = window.split("-")
    hm = lambda s: tuple(int(x) for x in s.split(":"))
    return hm(a) <= (now.hour, now.minute) <= hm(b)


def run(cfg, now_local, mode):
    results = []
    for resort in cfg["resorts"]:
        if not region_of(resort, cfg)[0].get("enabled", True):
            continue
        readings = gather_snow(resort, cfg, now_local)
        results.append(evaluate(resort, readings, cfg, now_local, mode))
    results.sort(key=lambda r: (RANK[r.verdict], r.new_snow_in or 0, -(r.drive.minutes if r.drive else 0)),
                 reverse=True)
    return results
