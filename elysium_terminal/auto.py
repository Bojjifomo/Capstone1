"""Auto rules per position: take profit, stop loss, and what to do when the price leaves the range.

One rule belongs to one portfolio row (a group from groups.py), so two positions in the
same pool can have different rules. This module decides; it never talks to the chain.
The web process calls `run_once` with the portfolio it just read and an `execute` callback.

What a rule can do:
  exit       close the position and sell the token back to the quote (TP, SL, or range exit)
  reentry    close, sell the token back to the quote, re-open the same shape (same widths,
             quote-only) next to the current price

Guard rails, all in data/auto.json and editable from the page:
  holdSec        how long the price must stay outside before a range action fires
  cooldownSec    minimum gap between two actions on the same position
  maxPerDay      range actions per position per UTC day
  maxNotionalUsd positions worth more than this are left alone
  paused         stops everything

A step that fails before anything was sent is retried after the cooldown, three times.
A cycle that stopped half way (close went through, swap or mint did not) is never retried:
the rule goes to "attention" and waits for you.
"""
from __future__ import annotations

import datetime
import json
import math
import threading
from pathlib import Path

FILE = "auto.json"
LOG = "auto_log.jsonl"
ACTIONS = ("hold", "reentry", "close")
_ALIAS = {"rebalance": "reentry"}      # rules saved before re-entry replaced the two-sided rebalance
MAX_FAILS = 3

DEFAULT_GLOBAL = {
    "paused": False,
    "holdSec": 120,
    "cooldownSec": 600,
    "maxPerDay": 6,
    "maxNotionalUsd": 2000.0,
    "slippageBps": 100,
}
_LIMITS = {
    "holdSec": (0, 86400, int),
    "cooldownSec": (30, 86400, int),
    "maxPerDay": (0, 96, int),
    "maxNotionalUsd": (1.0, 1_000_000.0, float),
    "slippageBps": (1, 1000, int),
}


def _num(v, lo, hi, kind, default):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return kind(min(hi, max(lo, x)))


def clean_global(changes: dict, current: dict | None = None) -> dict:
    g = dict(DEFAULT_GLOBAL)
    g.update({k: v for k, v in (current or {}).items() if k in DEFAULT_GLOBAL})
    for k, (lo, hi, kind) in _LIMITS.items():
        if k in (changes or {}):
            g[k] = _num(changes[k], lo, hi, kind, g[k])
    if "paused" in (changes or {}):
        g["paused"] = bool(changes["paused"])
    return g


def _pct(v, lo, hi):
    if v in (None, "", 0, "0", "off", False):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and lo <= x <= hi else None


def clean_rule(body: dict) -> dict:
    """The user-set part of a rule. Anything out of bounds turns that part off."""
    sl = _pct(body.get("sl"), -99.0, 99.0)
    side = lambda v: _ALIAS.get(v, v) if _ALIAS.get(v, v) in ACTIONS else "hold"
    return {
        "tp": _pct(body.get("tp"), 0.1, 1000.0),
        "sl": -abs(sl) if sl else None,
        "above": side(body.get("above")), "below": side(body.get("below")),
    }


def is_empty(rule: dict) -> bool:
    return not rule.get("tp") and not rule.get("sl") and rule.get("above", "hold") == "hold" and rule.get("below", "hold") == "hold"


def _day(now: float) -> str:
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%d")


def next_to_price(row: dict) -> bool:
    """True when a single range already starts at the first grid line past the price.

    A re-entry would close it and mint the same ticks again. Needs the pool's tick spacing,
    which the portfolio only has for v4; without it this says False and the executor checks.
    """
    sp, tick = int(row.get("tickSpacing") or 0), row.get("tick")
    if sp <= 0 or tick is None or row.get("posCount", 1) != 1:
        return False
    if row.get("flip"):                                   # quote is token0: the range sits above the tick
        return row.get("tickLower") == -(-(tick + 1) // sp) * sp
    return row.get("tickUpper") == (tick // sp) * sp


def decide(rule: dict, row: dict, g: dict, now: float) -> dict:
    """What to do for one armed rule and the row it belongs to. Returns action, reason, patch."""
    patch: dict = {"pnlPct": row.get("pnlPct"), "checkedAt": now, "state": row.get("state")}

    def out(action, reason, **extra):
        return {"action": action, "reason": reason, "patch": patch, **extra}

    if rule.get("status") != "armed":
        return out("none", "rule tidak aktif")
    if g.get("paused"):
        return out("none", "auto dijeda")
    pnl = row.get("pnlPct")
    if rule.get("tp") and pnl is not None and pnl >= rule["tp"]:
        return out("exit", f"take profit: PnL {pnl:+.2f}% ≥ +{rule['tp']:g}%", trigger="tp")
    if rule.get("sl") and pnl is not None and pnl <= rule["sl"]:
        return out("exit", f"stop loss: PnL {pnl:+.2f}% ≤ {rule['sl']:g}%", trigger="sl")
    side = row.get("state")
    if side not in ("above", "below"):
        patch["outSince"] = None
        return out("none", "di dalam range")
    want = _ALIAS.get(rule.get(side, "hold"), rule.get(side, "hold"))
    if want == "hold":
        patch["outSince"] = None
        return out("none", f"di luar range ({'atas' if side == 'above' else 'bawah'}), rule-nya diam")
    if want == "reentry" and side == "above" and next_to_price(row):
        patch["outSince"] = None
        return out("none", "range sudah tepat di bawah harga, re-entry tidak mengubah apa-apa")
    since = rule.get("outSince") if rule.get("outSide") == side else None
    if since is None:
        since = now
    patch["outSince"], patch["outSide"] = since, side
    waited = now - since
    if waited < g["holdSec"]:
        return out("none", f"di luar range, nunggu {int(g['holdSec'] - waited)} dtk lagi")
    last = rule.get("lastFire") or 0
    if now - last < g["cooldownSec"]:
        return out("none", f"cooldown {int(g['cooldownSec'] - (now - last))} dtk")
    count = rule.get("dayCount", 0) if rule.get("day") == _day(now) else 0
    if count >= g["maxPerDay"]:
        return out("none", f"sudah {count}× hari ini (batas {g['maxPerDay']})")
    value = row.get("valueUsd")
    if value is None:
        return out("none", "nilai posisi tidak kebaca")
    if value > g["maxNotionalUsd"]:
        return out("none", f"nilai ${value:,.0f} di atas batas auto ${g['maxNotionalUsd']:,.0f}")
    where = "naik lewat atas range" if side == "above" else "turun lewat bawah range"
    if want == "close":
        return out("exit", f"harga {where}: close + swap", trigger="range")
    return out("reentry", f"harga {where}: re-entry, range sama di harga sekarang", trigger="range")


class Store:
    """data/auto.json + data/auto_log.jsonl."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self._lock = threading.RLock()

    def _read(self) -> dict:
        try:
            d = json.loads((self.dir / FILE).read_text())
        except (OSError, ValueError):
            d = None
        if not isinstance(d, dict):
            d = {"rules": {}, "legacy": self._legacy()}
        d.setdefault("rules", {})
        d["global"] = clean_global({}, d.get("global"))
        return d

    def _legacy(self) -> dict:
        """Auto exits armed before positions were split were keyed by pool. Keep them to re-attach."""
        try:
            old = json.loads((self.dir / "auto_exit.json").read_text())
        except (OSError, ValueError):
            return {}
        return {p: {"pct": a.get("pct"), "symbol": a.get("symbol")} for p, a in old.items()
                if isinstance(a, dict) and a.get("status") == "armed" and a.get("pct")}

    def _write(self, d: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=1))
        tmp.replace(path)

    def view(self) -> dict:
        with self._lock:
            d = self._read()
            return {"global": d["global"], "rules": d["rules"]}

    def set_global(self, changes: dict) -> dict:
        with self._lock:
            d = self._read()
            d["global"] = clean_global(changes, d["global"])
            self._write(d)
            return d["global"]

    def set_rule(self, gid: str, body: dict, row: dict | None, now: float) -> dict | None:
        """Arm, change or (when everything is off) remove the rule of one position."""
        with self._lock:
            d = self._read()
            rule = clean_rule(body)
            if is_empty(rule):
                d["rules"].pop(gid, None)
                self._write(d)
                return None
            prev = d["rules"].get(gid) or {}
            keep = {k: prev[k] for k in ("lastFire", "day", "dayCount", "count") if k in prev}
            d["rules"][gid] = {**keep, **rule, "pool": (row or {}).get("pool") or prev.get("pool"),
                               "symbol": (row or {}).get("symbol") or prev.get("symbol"),
                               "armedAt": now, "status": "armed", "msg": "", "fails": 0}
            self._write(d)
            return d["rules"][gid]

    def patch(self, gid: str, patch: dict) -> dict | None:
        with self._lock:
            d = self._read()
            if gid not in d["rules"]:
                return None
            for k, v in patch.items():
                if v is None:
                    d["rules"][gid].pop(k, None)
                else:
                    d["rules"][gid][k] = v
            self._write(d)
            return d["rules"][gid]

    def move(self, old: str, new: str, patch: dict) -> None:
        """A re-entry burns the NFTs and mints new ones. The rule follows the new position."""
        with self._lock:
            d = self._read()
            rule = d["rules"].pop(old, None)
            if rule is None:
                return
            rule.update(patch)
            for k in ("outSince", "outSide"):
                rule.pop(k, None)
            d["rules"][new] = rule
            self._write(d)

    def adopt_legacy(self, rows: list[dict], now: float) -> int:
        """Old per-pool take profits become one rule per position in that pool."""
        with self._lock:
            d = self._read()
            legacy = d.get("legacy") or {}
            if not legacy:
                return 0
            n = 0
            for r in rows:
                a = legacy.get(r["pool"])
                if a and r["id"] not in d["rules"]:
                    d["rules"][r["id"]] = {**clean_rule({"tp": a["pct"]}), "pool": r["pool"], "symbol": r.get("symbol"),
                                           "armedAt": now, "status": "armed", "msg": "", "fails": 0}
                    n += 1
            d["legacy"] = {}
            self._write(d)
            return n

    def recover(self, now: float) -> int:
        """Called once at start. A rule still "running" means the process died mid-action."""
        with self._lock:
            d = self._read()
            n = 0
            for rule in d["rules"].values():
                if rule.get("status") == "running":
                    rule.update(status="attention", msg="proses mati di tengah aksi. Cek wallet + explorer dulu, baru lanjutkan.")
                    n += 1
            if n:
                self._write(d)
            return n

    def log(self, event: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self.dir / LOG, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")

    def log_tail(self, n: int = 20) -> list[dict]:
        try:
            lines = (self.dir / LOG).read_text().splitlines()
        except OSError:
            return []
        out = []
        for ln in lines[-n:]:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out


def run_once(store: Store, rows: list[dict], partial: bool, now: float, execute, say=None) -> list[dict]:
    """Check every armed rule against the portfolio that was just read.

    execute(action, rule, row, decision) -> dict from the executor:
      ok        the action went through (or simulated fine)
      dryRun    nothing was sent
      sent      at least one transaction was sent before a failure
      newId     the group id of the position a re-entry opened
    """
    say = say or (lambda text: None)
    view = store.view()
    g = view["global"]
    by_id = {r["id"]: r for r in rows}
    done = []
    for gid, rule in list(view["rules"].items()):
        if rule.get("status") != "armed":
            continue
        row = by_id.get(gid)
        if row is None:
            if not partial:
                store.patch(gid, {"status": "done", "msg": "posisi sudah nggak ada", "checkedAt": now})
            continue
        d = decide(rule, row, g, now)
        d["patch"]["msg"] = "" if d["action"] != "none" else d["reason"]
        rule = store.patch(gid, d["patch"]) or rule
        if d["action"] == "none":
            continue
        sym = row.get("symbol") or gid
        store.patch(gid, {"status": "running", "msg": d["reason"]})
        store.log({"ts": now, "id": gid, "symbol": sym, "kind": "fire", "action": d["action"], "reason": d["reason"]})
        say(f"🎯 AUTO {sym} ({gid}): {d['reason']}")
        try:
            res = execute(d["action"], rule, row, d) or {}
        except Exception as exc:                      # the executor bridge itself broke
            res = {"ok": False, "error": str(exc)[:240]}
        if res.get("ok") and res.get("noop"):             # nothing to do, nothing sent, nothing counted
            store.patch(gid, {"status": "armed", "msg": "range sudah tepat di samping harga, tidak ada yang diubah", "outSince": None})
            continue
        ok, dry = bool(res.get("ok")), bool(res.get("dryRun"))
        ranged = d.get("trigger") == "range"
        count = (rule.get("dayCount", 0) if rule.get("day") == _day(now) else 0) + (1 if ranged else 0)
        fired = {"lastFire": now, "day": _day(now), "dayCount": count}
        if not ok:
            err = f"{res.get('stage') or res.get('step') or ''} {res.get('error') or 'gagal'}".strip()[:240]
            fails = int(rule.get("fails") or 0) + 1
            if res.get("sent"):
                patch = {"status": "attention", "msg": f"berhenti di tengah: {err}. Token mungkin ada di wallet. Tidak diulang otomatis."}
            elif fails >= MAX_FAILS:
                patch = {"status": "failed", "msg": f"gagal {fails}×: {err}"}
            else:
                patch = {"status": "armed", "msg": f"gagal ({fails}/{MAX_FAILS}), dicoba lagi setelah cooldown: {err}"}
            store.patch(gid, {**patch, "fails": fails, "lastFire": now})
            store.log({"ts": now, "id": gid, "symbol": sym, "kind": "fail", "action": d["action"], "error": err,
                       "sent": bool(res.get("sent"))})
            say(f"❌ AUTO {sym}: {patch['msg']}")
            done.append({"id": gid, "ok": False, "action": d["action"], "msg": patch["msg"]})
            continue
        if d["action"] == "exit":
            msg = "simulasi close lolos (practice, tidak ada yang dikirim)" if dry else "closed + swapped"
            store.patch(gid, {**fired, "status": "done", "msg": msg, "fails": 0})
        elif dry:
            msg = "simulasi re-entry lolos (practice, tidak ada yang dikirim)"
            store.patch(gid, {**fired, "status": "armed", "msg": msg, "fails": 0, "outSince": None})
        else:
            n = int(rule.get("count") or 0) + 1
            msg = f"re-entry #{n} selesai"
            patch = {**fired, "status": "armed", "msg": msg, "fails": 0, "count": n}
            if res.get("newId") and res["newId"] != gid:
                store.move(gid, res["newId"], patch)
            elif res.get("newId"):
                store.patch(gid, {**patch, "outSince": None})
            else:
                store.patch(gid, {**patch, "status": "attention",
                                  "msg": "re-entry terkirim tapi posisi baru tidak kebaca. Cek portfolio."})
        store.log({"ts": now, "id": gid, "symbol": sym, "kind": "simulated" if dry else "done", "action": d["action"],
                   "newId": res.get("newId"), "hash": res.get("hash")})
        say(f"✅ AUTO {sym}: {msg}")
        done.append({"id": gid, "ok": True, "action": d["action"], "msg": msg, "dryRun": dry})
    return done
