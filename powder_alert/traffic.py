"""Traffic: how bad is the drive right now, and when is the best time to leave?

Provider "tomtom"   – TomTom Routing API (free key, 2,500 calls/day). Gives live
                      drive time, free-flow drive time, and *predicted* drive
                      time for a future departure (departAt), which is what lets
                      the app say "leave at 6:00, not 7:30".
Provider "heuristic" – no key needed. A rough I-70 ski-traffic model by day and
                      hour. Good enough to start; swap to TomTom for real data.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from .http import get

TOMTOM = "https://api.tomtom.com/routing/1/calculateRoute/{a}:{b}/json"


@dataclass
class Drive:
    minutes: float
    free_flow_minutes: float
    source: str
    depart: datetime | None = None
    ok: bool = True
    detail: str = ""

    @property
    def ratio(self):
        return self.minutes / self.free_flow_minutes if self.free_flow_minutes else 99

    @property
    def delay_min(self):
        return max(0.0, self.minutes - self.free_flow_minutes)


def _tomtom(home, dest, key, depart: datetime | None):
    params = {"key": key, "traffic": "true", "computeTravelTimeFor": "all", "routeType": "fastest"}
    params["departAt"] = depart.isoformat(timespec="seconds") if depart else "now"
    a = f"{home['lat']},{home['lon']}"
    b = f"{dest['lat']},{dest['lon']}"
    s = get(TOMTOM.format(a=a, b=b), params=params).json()["routes"][0]["summary"]
    return Drive(
        minutes=s["travelTimeInSeconds"] / 60,
        free_flow_minutes=s.get("noTrafficTravelTimeInSeconds", s["travelTimeInSeconds"]) / 60,
        source="TomTom live" if depart is None else "TomTom predicted",
        depart=depart,
    )


def _haversine_mi(a, b):
    r = 3958.8
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# Fallback traffic models (only used without a TomTom key). Each profile gives
# road-miles per straight-line mile, average free-flow mph, and a multiplier
# function by departure time.
PROFILES = {
    # I-70 west out of Denver: weekend mornings are the infamous jam.
    "i70": {"road_factor": 1.35, "mph": 55},
    # Wasatch: short drives, but the Cottonwood canyon roads (SR-210/SR-190)
    # "red snake" badly on weekends and powder mornings.
    "wasatch": {"road_factor": 1.4, "mph": 40},
}


def heuristic_ratio(when: datetime, powder: bool, holiday: bool = False, profile: str = "i70"):
    """Rough ski-traffic multiplier for a departure time."""
    h = when.hour + when.minute / 60
    weekend = when.weekday() >= 5 or holiday
    if profile == "wasatch":
        if weekend:
            r = 1.2 if 6 <= h < 7 else 1.7 if 7 <= h < 10 else 1.3 if 10 <= h < 11.5 else 1.05
            r += 0.4 if powder and 6.5 <= h < 10.5 else 0
        else:
            r = 1.25 if 7 <= h < 9 else 1.05
            r += 0.3 if powder and 6.5 <= h < 9.5 else 0
        return r
    if weekend:
        r = 1.15 if 5 <= h < 6 else 1.6 if 6 <= h < 9.5 else 1.35 if 9.5 <= h < 11 else 1.1
        r += 0.2 if powder and 5.5 <= h < 10 else 0
    else:
        r = 1.15 if 6.5 <= h < 8.5 else 1.05
        r += 0.1 if powder and 6 <= h < 9 else 0
    return r


def _heuristic(home, dest, when, powder, profile="i70"):
    p = PROFILES.get(profile, PROFILES["i70"])
    miles = _haversine_mi(home, dest) * p["road_factor"]
    free = miles / p["mph"] * 60
    r = heuristic_ratio(when, powder, profile=profile)
    return Drive(free * r, free, "estimate (no live traffic key)", depart=when,
                 detail=f"{profile} pattern model")


def drive_now(home, dest, now, powder=False, provider="tomtom", profile="i70"):
    key = os.environ.get("TOMTOM_API_KEY")
    if provider == "tomtom" and key:
        try:
            return _tomtom(home, dest, key, None)
        except Exception as e:
            d = _heuristic(home, dest, now, powder, profile)
            d.detail = f"TomTom failed ({e}); using estimate"
            return d
    return _heuristic(home, dest, now, powder, profile)


def best_departure(home, dest, now, hours=3, step_min=30, powder=True, provider="tomtom", profile="i70"):
    """Return the Drive with the shortest predicted time over the next `hours`."""
    key = os.environ.get("TOMTOM_API_KEY")
    options = []
    t = now + timedelta(minutes=step_min)
    while t <= now + timedelta(hours=hours):
        try:
            if provider == "tomtom" and key:
                options.append(_tomtom(home, dest, key, t))
            else:
                options.append(_heuristic(home, dest, t, powder, profile))
        except Exception:
            pass
        t += timedelta(minutes=step_min)
    return min(options, key=lambda d: d.minutes) if options else None
