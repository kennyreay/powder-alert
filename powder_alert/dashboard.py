"""Writes docs/latest.json and docs/index.html — a static dashboard GitHub Pages can host."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

COLORS = {"GO_NOW": "#1a7f37", "POWDER": "#b35900", "HEADS_UP": "#0b5cad", "NO": "#6b7280"}
LABEL = {"GO_NOW": "GO NOW", "POWDER": "POWDER · HEAVY TRAFFIC", "HEADS_UP": "SNOW COMING", "NO": "NOT YET"}


def _card(r, need):
    v = r["verdict"]
    snow_txt = "—" if r["new_snow_in"] is None else f'{r["new_snow_in"]:g}&quot;'
    bar = 0 if r["new_snow_in"] is None else min(100, r["new_snow_in"] / (need * 2) * 100)
    drive = r.get("drive")
    drive_txt = (f'{drive["minutes"]} min <span class=mut>(+{drive["delay_min"]} · {escape(drive["source"])})</span>'
                 if drive else '<span class=mut>checked only on powder days</span>')
    better = r.get("better_departure")
    better_txt = (f'<div class=row><span>Better departure</span><b>{better["depart"][11:16]} → '
                  f'{better["minutes"]} min</b></div>' if better else "")
    warn = ('<div class=warn>⚠️ Drive is unusually long — possible road closure or avalanche control.</div>'
            if any("closure" in x for x in r["reasons"]) else "")
    fc = "—" if r["forecast_in"] is None else f'{r["forecast_in"]:g}&quot;'
    srcs = "".join(
        f'<li class="{"" if s["ok"] else "bad"}"><b>{escape(s["source"])}</b> — {escape(s["detail"] or "")}</li>'
        for s in r["readings"])
    return f"""
<section class=card>
  <header><h2>{escape(r["name"])}</h2><span class=pill style="background:{COLORS[v]}">{LABEL[v]}</span></header>
  <div class=big>{snow_txt}<small> new · {escape(r["new_snow_basis"])}</small></div>
  <div class=meter><i style="width:{bar:.0f}%"></i><em style="left:50%"></em></div>
  <div class=row><span>Forecast next 24h</span><b>{fc}</b></div>
  <div class=row><span>Drive now</span><b>{drive_txt}</b></div>
  {better_txt}
  {warn}
  <details><summary>Sources</summary><ul>{srcs}</ul></details>
</section>"""


def write(results, cfg, now_local, mode, out_dir="docs", demo=False):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    need = cfg["rules"]["min_new_snow_in"]
    data = {"checked": now_local.isoformat(timespec="minutes"), "mode": mode, "demo": demo,
            "min_new_snow_in": need, "resorts": [r.to_dict() for r in results]}
    (out / "latest.json").write_text(json.dumps(data, indent=2))
    banner = "<div class=demo>DEMO DATA — sample numbers, not live conditions</div>" if demo else ""
    groups = {}
    for r in data["resorts"]:
        groups.setdefault(r.get("region") or "Resorts", []).append(r)
    homes = ", ".join(f'{escape(g.get("home", {}).get("name", "?"))} ({escape(g.get("label", k))})'
                      for k, g in (cfg.get("regions") or {}).items() if g.get("enabled", True)) \
        or escape(cfg.get("home", {}).get("name", "home"))
    cards = "".join(f"<h3 class=region>{escape(name)}</h3><div class=grid>{''.join(_card(r, need) for r in rs)}</div>"
                    for name, rs in groups.items())
    html = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Powder Alert</title>
<style>
:root{{--bg:#f6f8fb;--fg:#0f172a;--mut:#64748b;--card:#fff;--line:#e2e8f0;--snow:#3b82f6}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0b1220;--fg:#e5e7eb;--mut:#94a3b8;--card:#111a2e;--line:#1f2a44;--snow:#60a5fa}}}}
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:1100px;margin:auto;padding:24px 16px}}h1{{margin:0 0 4px;font-size:26px}}.mut{{color:var(--mut)}}
.grid{{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));margin-top:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}}
.card header{{display:flex;justify-content:space-between;align-items:center;gap:8px}}.card h2{{margin:0;font-size:18px}}
.pill{{color:#fff;font-size:11px;font-weight:700;letter-spacing:.04em;padding:4px 8px;border-radius:999px;white-space:nowrap}}
.big{{font-size:36px;font-weight:750;margin:10px 0 4px}}.big small{{font-size:13px;font-weight:500;color:var(--mut)}}
.meter{{position:relative;height:8px;background:var(--line);border-radius:6px;margin-bottom:10px}}
.meter i{{position:absolute;inset:0 auto 0 0;background:var(--snow);border-radius:6px}}
.meter em{{position:absolute;top:-3px;width:2px;height:14px;background:var(--fg);opacity:.5}}
.row{{display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-top:1px solid var(--line);font-size:14px}}
.row b{{font-weight:600;text-align:right}}details{{margin-top:8px;font-size:13px}}ul{{padding-left:18px;margin:6px 0}}
.region{{margin:26px 0 0;font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}}
.warn{{margin-top:8px;padding:6px 8px;border-radius:8px;background:#fee2e2;color:#991b1b;font-size:13px}}
li.bad{{color:var(--mut);text-decoration:line-through}}.demo{{background:#fde68a;color:#78350f;padding:8px 12px;border-radius:8px;margin-bottom:12px;font-weight:600}}
</style></head><body><main>
{banner}
<h1>❄️ Powder Alert</h1>
<div class=mut>Goal: ≥ {need}&quot; new snow in {cfg["rules"]["lookback_hours"]}h <b>and</b> light traffic from {homes}.
Last checked {now_local:%a %b %-d, %-I:%M %p} ({mode}). The tick on each bar marks {need}&quot;.</div>
{cards}
<p class=mut style="margin-top:20px;font-size:13px">Sources: NRCS SNOTEL, Open-Meteo, NOAA/NWS, resort reports, TomTom traffic.
Resort reports win over SNOTEL, which wins over models.</p>
</main></body></html>"""
    (out / "index.html").write_text(html)
    return out / "index.html"
