"""Money: what the office earns and costs, for the Money panel under the map.

money.json (next to server.py, written by hand on the Mac, never from the phone):

    {
      "refresh_minutes": 15,
      "fixed_monthly_usd": 7.89,
      "sources": [
        {"name": "Robinhood LP", "url": "http://127.0.0.1:7799/api/lp", "link": "http://127.0.0.1:8892/"},
        {"name": "DLMM (Meteora)", "cmd": ["python3", "-m", "dlmm_terminal", "pnl", "--json"], "cwd": "~/Projects/Capstone1"},
        {"name": "Widgetax fees", "file": "~/Projects/widgetax/earnings.json"}
      ]
    }

Each source answers JSON. Numbers are read from `today`, `week` and `month` (also accepted: `7d`/`30d`,
`earned_today`, or a nested object named in `path`, e.g. "earnings.usd"). A bare number counts as today.
An optional `note` string is shown under the amount. A source that fails keeps its last values and shows the
error. Commands run without a shell, from the folder in `cwd`, with a timeout; the browser can only ask for a
refresh, never add a source.

Earned = the sum of the sources. Fixed costs are the monthly figure spread over the window. Net = earned minus
fixed. "Claude at API prices" is what the same work would have cost at list prices (from the transcripts),
shown beside it so the size of the work is visible even on a subscription.
"""
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import store

WINDOWS = ("today", "week", "month")
ALIASES = {"today": ("today", "1d", "earned_today", "day"), "week": ("week", "7d", "earned_week", "7"),
           "month": ("month", "30d", "earned_month", "30")}
MAX_SOURCES = 12
CMD_TIMEOUT = 25


def _number(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pick(payload, path=""):
    """today/week/month (floats or None) and a note, from whatever JSON a source gives."""
    if path:
        for part in str(path).split("."):
            payload = payload.get(part) if isinstance(payload, dict) else None
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        return {"today": float(payload), "week": None, "month": None}, ""
    if not isinstance(payload, dict):
        return {k: None for k in WINDOWS}, ""
    values = {}
    for window, names in ALIASES.items():
        values[window] = next((_number(payload[n]) for n in names if n in payload and _number(payload[n]) is not None), None)
    note = str(payload.get("note") or payload.get("summary") or "")[:300]
    return values, note


def read_source(src):
    """Run one source and return (payload, error)."""
    try:
        if src.get("url"):
            req = urllib.request.Request(src["url"], headers={"User-Agent": "agent-office-money", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=12) as res:
                return json.loads(res.read().decode("utf-8", "replace")), ""
        if src.get("cmd"):
            argv = [str(a) for a in src["cmd"]]
            cwd = os.path.expanduser(src.get("cwd") or "") or None
            p = subprocess.run(argv, capture_output=True, text=True, timeout=CMD_TIMEOUT, cwd=cwd)
            if p.returncode:
                return None, (p.stderr or p.stdout or "exit %d" % p.returncode).strip()[-200:]
            out = p.stdout.strip()
            start = out.find("{")
            return json.loads(out[start:] if start > 0 else out), ""
        if src.get("file"):
            with open(os.path.expanduser(src["file"]), encoding="utf-8") as f:
                return json.load(f), ""
    except urllib.error.HTTPError as e:
        return None, "answered %d" % e.code
    except subprocess.TimeoutExpired:
        return None, "took longer than %d s" % CMD_TIMEOUT
    except (OSError, ValueError, urllib.error.URLError) as e:
        return None, str(getattr(e, "reason", e))[:200]
    return None, "no url, cmd or file"


class Money:
    def __init__(self, office=None):
        self.office = office
        self.lock = threading.Lock()
        self.results = {}       # name -> {today, week, month, note, error, updated}
        self.checked = 0
        self.refreshing = False

    def config(self):
        cfg = store.load("money.json", {})
        if not isinstance(cfg, dict):
            cfg = {}
        sources = [s for s in (cfg.get("sources") or []) if isinstance(s, dict) and s.get("name")][:MAX_SOURCES]
        try:
            fixed = max(0.0, float(cfg.get("fixed_monthly_usd") or 0))
        except (TypeError, ValueError):
            fixed = 0.0
        try:
            minutes = max(1, min(240, int(cfg.get("refresh_minutes") or 15)))
        except (TypeError, ValueError):
            minutes = 15
        return {"sources": sources, "fixed": fixed, "minutes": minutes, "configured": bool(cfg)}

    def refresh(self, wait=False):
        """Read every source. In the background unless `wait`."""
        with self.lock:
            if self.refreshing:
                return False
            self.refreshing = True
        if wait:
            self._run()
        else:
            threading.Thread(target=self._run, daemon=True, name="money").start()
        return True

    def _run(self):
        try:
            cfg = self.config()
            for src in cfg["sources"]:
                payload, error = read_source(src)
                values, note = pick(payload, src.get("path", "")) if payload is not None else ({k: None for k in WINDOWS}, "")
                with self.lock:
                    old = self.results.get(src["name"], {})
                    if error:
                        row = dict(old, error=error)
                        row.setdefault("today", None); row.setdefault("week", None); row.setdefault("month", None)
                    else:
                        row = dict(values, note=note or old.get("note", ""), error="", updated=time.time())
                    row["link"] = src.get("link", "")
                    self.results[src["name"]] = row
            with self.lock:
                self.checked = time.time()
        finally:
            with self.lock:
                self.refreshing = False

    def api_prices(self):
        """What the work cost at API list prices, from the transcripts: today / 7 days / 30 days."""
        totals = {"today": 0.0, "week": 0.0, "month": 0.0}
        scanner = getattr(self.office, "scanner", None)
        if not scanner:
            return totals
        with scanner.lock:
            for t in scanner.transcripts.values():
                for k, v in t.pay().items():
                    totals[k] += v
        grok = getattr(self.office, "grok", None)
        if grok:
            try:
                totals["today"] += grok.spent_today()
            except Exception:
                pass
        return {k: round(v, 2) for k, v in totals.items()}

    def snapshot(self):
        cfg = self.config()
        with self.lock:
            due = time.time() - self.checked > cfg["minutes"] * 60
            results = {k: dict(v) for k, v in self.results.items()}
            checked, refreshing = self.checked, self.refreshing
        if due and cfg["sources"] and not refreshing:
            self.refresh()
        rows = []
        earned = {k: 0.0 for k in WINDOWS}
        for src in cfg["sources"]:
            r = results.get(src["name"], {"today": None, "week": None, "month": None, "note": "", "error": "", "updated": 0})
            r = dict(r, name=src["name"], link=src.get("link", ""))
            rows.append(r)
            for k in WINDOWS:
                if r.get(k) is not None:
                    earned[k] += r[k]
        days = {"today": 1, "week": 7, "month": 30}
        fixed = {k: round(cfg["fixed"] * days[k] / 30, 2) for k in WINDOWS}
        net = {k: round(earned[k] - fixed[k], 2) for k in WINDOWS}
        return {"configured": cfg["configured"], "sources": rows, "earned": {k: round(v, 2) for k, v in earned.items()},
                "fixed": fixed, "fixed_monthly": cfg["fixed"], "net": net, "api_prices": self.api_prices(),
                "checked": checked, "refreshing": refreshing, "every_minutes": cfg["minutes"],
                "errors": [r["name"] + ": " + r["error"] for r in rows if r.get("error")],
                "day_note": "Days run midnight to midnight on the Mac's clock. Today's API-price figure includes Grok."}


def windowed_pay(usage, days):
    """Sum of a transcript's usage rows over the last `days` calendar days (helper for tests)."""
    first = (datetime.now().date() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    return round(sum(r[4] for d, models in usage.items() if d >= first for r in models.values()), 4)
