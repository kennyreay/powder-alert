"""Offline tests: parsers, snow math, decision rules, de-duplication. Run: python -m pytest -q"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from powder_alert import engine, snow, traffic
from powder_alert.__main__ import new_alerts, demo_results
from powder_alert.notify import compose

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())


def _snotel_csv(depths, swes=None):
    t0 = datetime(2026, 11, 10, 0, 0)
    lines = ["#Loveland Basin (602)", "#provisional data",
             "Date,Snow Depth (in) Start of Hour Values,Snow Water Equivalent (in) Start of Hour Values,"
             "Air Temperature Observed (degF) Start of Hour Values"]
    for i, d in enumerate(depths):
        w = "" if swes is None else swes[i]
        lines.append(f"{(t0 + timedelta(hours=i)):%Y-%m-%d %H:%M},{'' if d is None else d},{w},20.1")
    return "\n".join(lines)


def test_snotel_detects_storm_even_after_settling():
    # 30" base, snows 10" overnight, then settles 2"; plus sensor noise and a gap
    depths = [30, 31, 30, 30] * 6 + [30, 32, 34, 36, 38, 40, 40, 39, None, 39, 38, 38] + [38] * 12
    rows = snow.parse_snotel_csv(_snotel_csv(depths))
    new, depth, _ = snow.snotel_new_snow(rows, 24)
    assert 9 <= new <= 10.5, new
    assert depth == 38


def test_snotel_noise_is_not_snow():
    depths = [30, 31, 30, 29, 30, 31, 30, 30] * 6
    new, _, _ = snow.snotel_new_snow(snow.parse_snotel_csv(_snotel_csv(depths)), 24)
    assert new <= 1


def test_snotel_swe_cross_check():
    depths = [20] * 24 + [26] * 24
    swes = [5.0] * 24 + [5.5] * 24
    _, _, swe_est = snow.snotel_new_snow(snow.parse_snotel_csv(_snotel_csv(depths, swes)), 24, 12)
    assert swe_est == 6.0


def test_awdb_json_parser():
    data = [{"stationTriplet": "602:CO:SNTL", "data": [
        {"stationElement": {"elementCode": "SNWD"}, "values": [
            {"date": "2026-11-10 00:00", "value": 30}, {"date": "2026-11-10 01:00", "value": 31}]},
        {"stationElement": {"elementCode": "WTEQ"}, "values": [
            {"date": "2026-11-10 00:00", "value": 8.1}]}]}]
    rows = snow.parse_awdb_json(data)
    assert rows[0][1:] == (30, 8.1) and rows[1][1] == 31


def test_open_meteo_units_and_windows():
    now = datetime(2026, 11, 11, 6, 0)
    times = [(now - timedelta(hours=30) + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(60)]
    snowfall = [0.0] * 60
    for i in range(20, 30):      # 10 h × 2.54 cm = 10 in falling in the past 24 h
        snowfall[i] = 2.54
    for i in range(31, 36):      # 5 h × 1.27 cm = 2.5 in in the next 24 h
        snowfall[i] = 1.27
    data = {"hourly": {"time": times, "snowfall": snowfall}, "hourly_units": {"snowfall": "cm"}}
    past, nxt = snow.parse_open_meteo(data, now, 24, 24)
    assert past == 10.0 and nxt == 2.5


def test_nws_snowfall_partial_overlap():
    now = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
    grid = {"properties": {"snowfallAmount": {"uom": "wmoUnit:mm", "values": [
        {"validTime": "2026-11-11T06:00:00+00:00/PT12H", "value": 101.6},   # half overlaps → 2"
        {"validTime": "2026-11-11T18:00:00+00:00/PT6H", "value": 76.2},     # 3"
        {"validTime": "2026-11-13T00:00:00+00:00/P1D", "value": 500}]}}}     # outside window
    assert snow.parse_nws_snowfall(grid, now, 24) == 5.0


def test_resort_report_regex():
    html = "<div>Snow Report</div><span>24 Hour</span><b>8\"</b><span>48 Hour</span><b>12\"</b>"
    rx = r'24\s*(?:hr|hour)[^0-9]{0,40}(\d+(?:\.\d+)?)\s*(?:"|in)'
    assert snow.parse_resort_report(html, rx) == 8.0


def _drive(ratio):
    return lambda home, dest, when, powder=False, provider=None, profile=None: traffic.Drive(90 * ratio, 90, "test", when)


def _later(minutes):
    return lambda *a, **k: traffic.Drive(minutes, 90, "test predicted", datetime(2026, 11, 11, 9, 30))


RESORT = CFG["resorts"][0]
NOW = datetime(2026, 11, 14, 6, 0)   # a Saturday


def _readings(observed=None, model=None, resort=None, fc=None):
    r = []
    if resort is not None:
        r.append(snow.SnowReading("report", "resort", resort))
    if observed is not None:
        r.append(snow.SnowReading("snotel", "observed", observed))
    if model is not None:
        r.append(snow.SnowReading("model", "modeled", model))
    if fc is not None:
        r.append(snow.SnowReading("fc", "forecast", fc))
    return r


def test_go_now_when_deep_and_clear():
    res = engine.evaluate(RESORT, _readings(observed=8), CFG, NOW, "morning", _drive(1.1), _later(80))
    assert res.verdict == engine.GO_NOW


def test_exactly_six_inches_counts():
    res = engine.evaluate(RESORT, _readings(observed=6.0), CFG, NOW, "morning", _drive(1.0), _later(80))
    assert res.verdict == engine.GO_NOW


def test_powder_but_traffic_suggests_departure():
    res = engine.evaluate(RESORT, _readings(observed=10), CFG, NOW, "morning", _drive(1.7), _later(95))
    assert res.verdict == engine.POWDER and res.better_departure is not None


def test_not_enough_snow():
    res = engine.evaluate(RESORT, _readings(observed=4, model=9), CFG, NOW, "morning", _drive(1.0), _later(80))
    assert res.verdict == engine.NO   # measured beats model


def test_resort_report_beats_snotel():
    res = engine.evaluate(RESORT, _readings(resort=7, observed=3), CFG, NOW, "morning", _drive(1.0), _later(80))
    assert res.verdict == engine.GO_NOW and res.new_snow_basis == "resort report"


def test_model_used_only_when_no_measurements():
    res = engine.evaluate(RESORT, _readings(model=7), CFG, NOW, "morning", _drive(1.0), _later(80))
    assert res.verdict == engine.GO_NOW and "model" in res.new_snow_basis


def test_evening_heads_up():
    res = engine.evaluate(RESORT, _readings(observed=0, fc=8), CFG, NOW.replace(hour=19), "evening")
    assert res.verdict == engine.HEADS_UP


def test_dedupe_only_upgrades():
    state = {}
    r = engine.evaluate(RESORT, _readings(observed=8), CFG, NOW, "morning", _drive(1.7), _later(80))
    assert len(new_alerts([r], state, "d")) == 1
    state["d"] = {RESORT["name"]: r.verdict}
    assert new_alerts([r], state, "d") == []          # same verdict → silent
    r2 = engine.evaluate(RESORT, _readings(observed=8), CFG, NOW, "morning", _drive(1.0), _later(80))
    assert len(new_alerts([r2], state, "d")) == 1     # traffic cleared → GO NOW alert


def test_heuristic_weekend_morning_is_heavy():
    sat7 = datetime(2026, 11, 14, 7, 0)
    tue7 = datetime(2026, 11, 10, 5, 30)
    assert traffic.heuristic_ratio(sat7, True) > CFG["rules"]["max_traffic_ratio"]
    assert traffic.heuristic_ratio(tue7, True) <= CFG["rules"]["max_traffic_ratio"]


def test_demo_message_composes():
    res = demo_results(CFG, datetime(2026, 11, 14, 6, 15))
    title, text, html, prio = compose(res, datetime(2026, 11, 14, 6, 15))
    assert "GO NOW" in title and prio == 5 and "Copper" in text


UTAH = next(r for r in CFG["resorts"] if r["name"] == "Alta")


def test_all_wasatch_resorts_present_with_utah_snotel():
    names = {r["name"] for r in CFG["resorts"] if r["region"] == "utah"}
    assert {"Alta", "Snowbird", "Brighton", "Solitude", "Park City Mountain", "Deer Valley",
            "Snowbasin", "Powder Mountain", "Nordic Valley", "Sundance", "Woodward Park City"} <= names
    for r in CFG["resorts"]:
        assert r["region"] in CFG["regions"]
        assert all(t.endswith(":UT:SNTL") == (r["region"] == "utah") for t in r["snotel"])


def test_utah_uses_salt_lake_home_and_wasatch_profile():
    seen = {}
    def drive(home, dest, when, powder=False, provider=None, profile=None):
        seen.update(home=home["name"], profile=profile)
        return traffic.Drive(40, 38, "test", when)
    res = engine.evaluate(UTAH, _readings(observed=12), CFG, NOW, "morning", drive, _later(40))
    assert seen == {"home": "Salt Lake City", "profile": "wasatch"}
    assert res.verdict == engine.GO_NOW and res.region.startswith("Utah")


def test_canyon_closure_flagged():
    res = engine.evaluate(UTAH, _readings(observed=12), CFG, NOW, "morning", _drive(3.0), _later(80))
    assert res.verdict == engine.POWDER and any("closure" in x for x in res.reasons)


def test_disabled_region_is_skipped(monkeypatch):
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["regions"]["utah"]["enabled"] = False
    monkeypatch.setattr(engine, "gather_snow", lambda *a: [])
    names = {r.name for r in engine.run(cfg, NOW, "evening")}
    assert "Alta" not in names and "Loveland" in names


def test_wasatch_heuristic_weekend_powder_is_heavy():
    sat8 = datetime(2026, 11, 14, 8, 0)
    assert traffic.heuristic_ratio(sat8, True, profile="wasatch") > 2
    assert traffic.heuristic_ratio(datetime(2026, 11, 10, 6, 0), True, profile="wasatch") <= 1.25
