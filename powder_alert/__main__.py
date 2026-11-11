"""Powder Alert — run with:  python -m powder_alert [--mode auto|morning|evening] [options]

  --demo         use built-in sample data (no internet, no keys) to see what alerts look like
  --test-notify  send a test message through every enabled channel
  --dry-run      fetch real data and print the verdict, but don't notify or save state
  --force        ignore the morning/evening time windows
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import dashboard, engine, notify, traffic
from .snow import SnowReading

STATE = Path("state.json")


def load_cfg(path):
    return yaml.safe_load(Path(path).read_text())


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(state, today):
    keep = {(today - timedelta(days=i)).isoformat() for i in range(7)}
    STATE.write_text(json.dumps({k: v for k, v in state.items() if k[:10] in keep}, indent=2))


def new_alerts(results, state, key):
    """Only alert when a resort reaches a *higher* verdict than already sent today."""
    sent = state.setdefault(key, {})
    fresh = []
    for r in results:
        if r.verdict == engine.NO:
            continue
        if engine.RANK[r.verdict] > engine.RANK.get(sent.get(r.name, engine.NO), 0):
            fresh.append(r)
    return fresh


def demo_results(cfg, now):
    """Sample data that exercises every verdict — for previewing alerts and the dashboard."""
    fake = {
        "Loveland": (9.0, 4.0, 1.12),
        "Arapahoe Basin": (8.0, 3.0, 1.18),
        "Copper Mountain": (11.0, 5.0, 1.55),
        "Vail": (7.0, 6.0, 1.60),
        "Winter Park": (5.0, 7.5, None),
        "Breckenridge": (3.0, 2.0, None),
        "Alta": (14.0, 6.0, 1.15),
        "Snowbird": (13.0, 6.0, 3.10),          # canyon closed for avalanche control
        "Brighton": (10.0, 5.0, 1.20),
        "Solitude": (9.0, 5.0, 1.75),
        "Park City Mountain": (6.0, 3.0, 1.10),
        "Snowbasin": (4.0, 2.0, None),
    }
    out = []
    for resort in cfg["resorts"]:
        if resort["name"] not in fake:
            continue
        new, fc, ratio = fake[resort["name"]]
        readings = [
            SnowReading(f"SNOTEL {t}", "observed", new, 30 + new, f'{new}" new (demo)') for t in resort["snotel"]
        ] + [SnowReading("Open-Meteo forecast", "forecast", fc, detail=f'{fc}" next 24h (demo)'),
             SnowReading("NWS forecast", "forecast", fc, detail=f'{fc}" next 24h (demo)')]

        def drive_fn(home, dest, when, powder=False, provider=None, profile="i70", _r=ratio):
            d = traffic._heuristic(home, dest, when, powder, profile)
            return traffic.Drive(d.free_flow_minutes * (_r or 1.1), d.free_flow_minutes, "demo traffic", when)

        def depart_fn(home, dest, when, hours, step, powder=True, provider=None, profile="i70"):
            d = traffic._heuristic(home, dest, when, powder, profile)
            return traffic.Drive(d.free_flow_minutes * 1.1, d.free_flow_minutes, "demo predicted",
                                 when.replace(hour=10, minute=30))
        out.append(engine.evaluate(resort, readings, cfg, now, "morning", drive_fn, depart_fn))
    out.sort(key=lambda r: (engine.RANK[r.verdict], r.new_snow_in or 0), reverse=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="powder_alert")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mode", choices=["auto", "morning", "evening"], default="auto")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--test-notify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    cfg = load_cfg(a.config)
    tz = ZoneInfo(cfg["timezone"])
    now = datetime.now(tz)

    if a.test_notify:
        sent = notify.dispatch(cfg["notify"], "❄️ Powder Alert test",
                               "If you can read this, notifications work. Pray for snow.",
                               "<p>If you can read this, notifications work. Pray for snow.</p>", 3)
        print(f"Sent via: {', '.join(sent) or 'nothing — check your environment variables'}")
        return 0

    if a.demo:
        results = demo_results(cfg, now.replace(hour=6, minute=15))
        dashboard.write(results, cfg, now.replace(hour=6, minute=15), "morning", demo=True)
        msg = notify.compose(results, now.replace(hour=6, minute=15), cfg["notify"].get("dashboard_url", ""))
        notify.send_console(msg[0], msg[1])
        print("Demo dashboard written to docs/index.html")
        return 0

    mode = a.mode
    if mode == "auto":
        if engine.in_window(now, cfg["rules"]["morning_window"]):
            mode = "morning"
        elif engine.in_window(now, cfg["rules"]["evening_forecast_time"]):
            mode = "evening"
        elif a.force:
            mode = "morning"
        else:
            print(f"{now:%H:%M} is outside the morning/evening windows — nothing to do (use --force).")
            return 0

    print(f"Powder Alert · {mode} check · {now:%a %Y-%m-%d %H:%M %Z}")
    results = engine.run(cfg, now, mode)
    for r in results:
        print(f"  {r.verdict:9} {r.name:16} new={r.new_snow_in!s:>5}\" fc={r.forecast_in!s:>5}\"  "
              + "; ".join(r.reasons))
    dashboard.write(results, cfg, now, mode)

    if a.dry_run:
        msg = notify.compose(results, now, cfg["notify"].get("dashboard_url", ""))
        print("\n(dry run) would send:\n" + (f"{msg[0]}\n{msg[1]}" if msg else "nothing"))
        return 0

    state = load_state()
    key = now.date().isoformat() if mode == "morning" else f"{now.date().isoformat()}-evening"
    fresh = new_alerts(results, state, key)
    if fresh:
        title, text, html, prio = notify.compose(fresh, now, cfg["notify"].get("dashboard_url", ""))
        sent = notify.dispatch(cfg["notify"], title, text, html, prio)
        print(f"Alert sent via: {', '.join(sent) or 'none'}")
        for r in fresh:
            state.setdefault(key, {})[r.name] = r.verdict
        save_state({k: v for k, v in state.items()}, now.date())
    else:
        print("No new alerts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
