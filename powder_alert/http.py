"""Tiny HTTP helper: one session, a polite User-Agent (NWS requires one), retries."""
import os
import time

import requests

_UA = os.environ.get("POWDER_ALERT_UA", "powder-alert/1.0 (personal ski-alert script)")
_session = requests.Session()
_session.headers.update({"User-Agent": _UA, "Accept": "application/json, text/csv, text/html;q=0.9, */*;q=0.8"})


def get(url, params=None, timeout=20, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise last


def post(url, **kw):
    kw.setdefault("timeout", 20)
    r = _session.post(url, **kw)
    r.raise_for_status()
    return r
