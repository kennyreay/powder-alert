"""Notification channels: console, ntfy (phone push), email, Pushover."""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from .engine import GO_NOW, HEADS_UP, POWDER
from .http import post


def _fmt_min(m):
    m = round(m)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def compose(results, now_local, dashboard_url=""):
    """Build (title, text_body, html_body, priority) for the resorts worth alerting on."""
    hits = [r for r in results if r.verdict in (GO_NOW, POWDER, HEADS_UP)]
    if not hits:
        return None
    top = hits[0]
    if top.verdict == GO_NOW:
        title = f'❄️ GO NOW: {top.name} {top.new_snow_in:g}" fresh, roads clear'
        prio = 5
    elif top.verdict == POWDER:
        title = f'❄️ Powder day: {top.name} {top.new_snow_in:g}" — traffic heavy'
        if top.better_departure:
            title += f", leave {top.better_departure.depart:%-I:%M %p}"
        prio = 4
    else:
        title = f'🌨️ Heads-up: {top.name} {top.forecast_in:g}" forecast by tomorrow'
        prio = 3

    lines, last_region = [], None
    multi = len({r.region for r in hits}) > 1
    for r in sorted(hits, key=lambda r: r.region) if multi else hits:
        if multi and r.region != last_region:
            lines.append(f"— {r.region} —")
            last_region = r.region
        if r.verdict == HEADS_UP:
            lines.append(f'• {r.name}: {r.forecast_in:g}" forecast next 24h')
            continue
        s = f'• {r.name}: {r.new_snow_in:g}" new ({r.new_snow_basis}) · drive {_fmt_min(r.drive.minutes)}'
        s += f" (+{r.drive.delay_min:.0f} min, {r.drive.source})"
        if r.verdict == GO_NOW:
            s = "✅ " + s[2:]
        elif r.better_departure:
            b = r.better_departure
            s += f"\n    ↳ leave {b.depart:%-I:%M %p} → {_fmt_min(b.minutes)}"
        if any("closure" in x for x in r.reasons):
            s += "\n    ⚠️ drive is unusually long — possible road closure / avalanche control"
        lines.append(s)
    lines.append(f"\nChecked {now_local:%a %b %-d, %-I:%M %p}")
    if dashboard_url:
        lines.append(f"Dashboard: {dashboard_url}")
    text = "\n".join(lines)

    rows = "".join(
        f"<tr><td style='padding:6px 10px'><b>{escape(r.name)}</b><br><small>{escape(r.region)}</small></td>"
        f"<td style='padding:6px 10px'>{escape(r.verdict.replace('_', ' '))}</td>"
        f"<td style='padding:6px 10px'>{'' if r.new_snow_in is None else f'{r.new_snow_in:g}&quot;'}</td>"
        f"<td style='padding:6px 10px'>{escape(_fmt_min(r.drive.minutes)) if r.drive else '—'}</td>"
        f"<td style='padding:6px 10px'>{escape(f'{r.better_departure.depart:%-I:%M %p}') if r.better_departure else ''}</td></tr>"
        for r in results)
    html = (f"<div style='font-family:system-ui,sans-serif'><h2>{escape(title)}</h2>"
            f"<pre style='font-family:inherit;white-space:pre-wrap'>{escape(text)}</pre>"
            f"<table border='1' style='border-collapse:collapse'><tr><th>Resort</th><th>Verdict</th>"
            f"<th>New snow</th><th>Drive now</th><th>Better departure</th></tr>{rows}</table></div>")
    return title, text, html, prio


def send_console(title, text, **_):
    print("\n" + "═" * 60 + f"\n{title}\n" + "─" * 60 + f"\n{text}\n" + "═" * 60)
    return True


def send_ntfy(title, text, prio, click=""):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    payload = {"topic": topic, "title": title, "message": text, "priority": prio,
               "tags": ["snowflake", "skier"]}
    if click:
        payload["click"] = click
    headers = {}
    if os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
    post(server, json=payload, headers=headers)
    return True


def send_email(title, text, html, host, port):
    user, pw, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"))
    if not (user and pw and to):
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = title, user, to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, pw)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    return True


def send_pushover(title, text, prio, click=""):
    tok, usr = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if not (tok and usr):
        return False
    post("https://api.pushover.net/1/messages.json",
         data={"token": tok, "user": usr, "title": title, "message": text,
               "priority": 1 if prio >= 5 else 0, "url": click})
    return True


def dispatch(cfg_notify, title, text, html, prio):
    sent = []
    url = cfg_notify.get("dashboard_url", "")
    channels = [
        ("console", lambda: send_console(title, text)),
        ("ntfy", lambda: send_ntfy(title, text, prio, url)),
        ("email", lambda: send_email(title, text, html, cfg_notify.get("smtp_host", "smtp.gmail.com"),
                                     int(cfg_notify.get("smtp_port", 587)))),
        ("pushover", lambda: send_pushover(title, text, prio, url)),
    ]
    for name, fn in channels:
        if not cfg_notify.get(name):
            continue
        try:
            if fn():
                sent.append(name)
            elif name != "console":
                print(f"[notify] {name}: enabled but credentials not set — skipped")
        except Exception as e:
            print(f"[notify] {name} failed: {e}")
    return sent
