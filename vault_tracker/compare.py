"""Compare two wallets' DLMM actions, e.g. a vault and the wallet you copy instead of it, and say
when they stop doing the same thing.

Two checks, both with a grace period (`window`) because the two never act in the same second:
- actions: every OPEN / CLOSE on one side should have the same action in the same pool on the
  other side within the window;
- holdings: the pools both hold right now (positions with liquidity) should be the same.
"""

import json
import os
import statistics
import time

from .alerts import fmt_usd
from .tokens import SOL_MINT

ACTIONS = ("OPEN", "CLOSE")
VERB = {"OPEN": "BUKA", "CLOSE": "TUTUP"}
STATS_WINDOW = 86_400       # s of history the score and the matching look at
RECENT_BEDA = 6 * 3600      # an unmatched action keeps the verdict at BEDA this long
SPOT_MIN_USD = 50           # spot swaps of the copied wallet worth mentioning
SEEN_KEPT = 2000


def range_overlap(a, b):
    """Bins two ranges share, as a fraction of the wider one (1.0 = identical)."""
    if not a or not b or None in a or None in b:
        return None
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)
    return inter / max(a[1] - a[0] + 1, b[1] - b[0] + 1)


def match_actions(a_events, b_events, window):
    """Pair each OPEN / CLOSE of A with the nearest unused same action of B in the same pool
    (or, failing that, the same token pair) within `window` seconds.
    Events: dicts with key, type, lb_pair, pairkey, ts. Returns (pairs, a_left, b_left)."""
    used, pairs, a_left = set(), [], []
    for ev in sorted(a_events, key=lambda e: e["ts"]):
        best = None
        for same_pool in (True, False):
            cands = [b for b in b_events if b["key"] not in used and b["type"] == ev["type"]
                     and abs(b["ts"] - ev["ts"]) <= window
                     and (b["lb_pair"] == ev["lb_pair"] if same_pool else b["pairkey"] == ev["pairkey"])]
            if cands:
                best = min(cands, key=lambda b: abs(b["ts"] - ev["ts"]))
                break
        if best is None:
            a_left.append(ev)
        else:
            used.add(best["key"])
            pairs.append((ev, best))
    return pairs, a_left, [b for b in b_events if b["key"] not in used]


def _delay(seconds):
    s = abs(int(seconds))
    txt = f"{s} dtk" if s < 120 else f"{s // 60} mnt"
    return f"{txt} {'sebelum' if seconds < 0 else 'setelah'}"


class Comparator:
    def __init__(self, a, b, window=600, state_dir=".vault_tracker", sol_only=False):
        self.a, self.b = a, b            # VaultTracker: a = reference (vault), b = the wallet you copy
        self.window = window
        self.sol_only = sol_only
        self.path = os.path.join(state_dir, f"compare-{a.address[:8]}-{b.address[:8]}.json")
        try:
            with open(self.path) as f:
                self.state = json.load(f)
        except (OSError, ValueError):
            self.state = {}
        for k, v in (("seen", []), ("pools", {}), ("log", []), ("pool_alerts", {})):
            self.state.setdefault(k, v)
        self.started = bool(self.state["seen"])
        self.view = {"verdict": "MENUNGGU", "reasons": ["belum dicek"], "pools": [], "stats": {}}
        self._names = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)

    # ---- helpers ------------------------------------------------------------------

    def _pair(self, lb_pair):
        if lb_pair not in self._names:
            try:
                pair, tx, ty = self.a.pair_info(lb_pair)
                self._names[lb_pair] = (f"{tx.symbol}-{ty.symbol}", (pair.token_x_mint, pair.token_y_mint))
            except Exception:
                self._names[lb_pair] = (lb_pair[:6], (lb_pair, lb_pair))
        return self._names[lb_pair]

    def _wanted(self, lb_pair):
        return not self.sol_only or SOL_MINT in self._pair(lb_pair)[1]

    def _events(self, tracker, side, since):
        out = []
        for e in tracker.state["events"]:
            if e["type"] not in ACTIONS or (e.get("ts") or 0) < since or not e.get("lb_pair"):
                continue
            if not self._wanted(e["lb_pair"]):
                continue
            p = tracker.state["positions"].get(e.get("position"), {})
            rng = e.get("range") or (p.get("lower"), p.get("upper"))
            out.append({"key": f"{side}:{e['type']}:{e.get('position')}", "side": side, "type": e["type"],
                        "position": e.get("position"),
                        "lb_pair": e["lb_pair"], "pairkey": self._pair(e["lb_pair"])[1], "ts": e["ts"],
                        "range": tuple(rng) if rng and None not in rng else None,
                        "name": self._pair(e["lb_pair"])[0]})
        return out

    def _held(self, tracker, lb_pair, t0, t1, skip=None):
        """Did the tracker hold a position in the pool at some moment in [t0, t1]?"""
        for pk, p in tracker.state["positions"].items():
            if pk == skip or p.get("lb_pair") != lb_pair or p.get("kind") == "order":
                continue
            if (p.get("opened_at") or 0) <= t1 and (p.get("closed_at") or float("inf")) >= t0:
                return True
        return False

    def _reposition(self, e):
        """An unmatched action that does not change what is held: re-opening a range in a pool the
        other side is in, or closing one position while staying in the pool (moving the range)."""
        me, other = (self.a, self.b) if e["side"] == "a" else (self.b, self.a)
        t, w = e["ts"], self.window
        if e["type"] == "OPEN":
            return self._held(other, e["lb_pair"], t - w, t + w)
        return self._held(me, e["lb_pair"], t, t + w, skip=e["position"])

    def _apart_since(self, lb_pair, holder, other, now):
        """When the holder started being alone in the pool: its current entry, or the moment the
        other side left, whichever is later (so a restart does not reset the clock)."""
        opened = [p.get("opened_at") for p in holder.state["positions"].values()
                  if p.get("lb_pair") == lb_pair and p.get("status") == "open" and p.get("opened_at")]
        left = [p.get("closed_at") for p in other.state["positions"].values()
                if p.get("lb_pair") == lb_pair and p.get("closed_at")]
        times = [t for t in (min(opened) if opened else None, max(left) if left else None) if t]
        return min(now, max(times)) if times else now

    def _holdings(self, tracker):
        return {r["lb_pair"]: r for r in (tracker.snapshot or {}).get("positions", [])
                if r.get("kind") == "position" and r.get("has_liquidity") and self._wanted(r["lb_pair"])}

    def _note(self, kind, title, lines):
        self.state["log"] = (self.state["log"] + [{"ts": int(time.time()), "kind": kind, "title": title,
                                                   "lines": lines}])[-100:]
        return title, lines

    # ---- the comparison -----------------------------------------------------------

    def update(self, now=None):
        """Compare once. Returns [(title, lines)] alerts (only for what changed since last time)."""
        now = now or time.time()
        A, B = self.a.display, self.b.display
        first = not self.started
        seen = set(self.state["seen"])
        notes, new_seen = [], []
        ev_a = self._events(self.a, "a", now - STATS_WINDOW)
        ev_b = self._events(self.b, "b", now - STATS_WINDOW)
        pairs, a_left, b_left = match_actions(ev_a, ev_b, self.window)
        # moving a range (close + reopen in the same pool) is not a different decision
        moved = [e for e in a_left + b_left if now - e["ts"] > self.window and self._reposition(e)]
        a_left = [e for e in a_left if e not in moved]
        b_left = [e for e in b_left if e not in moved]

        def fresh(key):
            if key in seen:
                return False
            seen.add(key)
            new_seen.append(key)
            return not first

        for ea, eb in pairs:
            if fresh(ea["key"] + "|" + eb["key"]):
                lines = [f"{B} {_delay(eb['ts'] - ea['ts'])} {A}"]
                if ea["lb_pair"] != eb["lb_pair"]:
                    lines.append("⚠️ pair sama, tapi pool beda (bin step lain)")
                ov = range_overlap(ea["range"], eb["range"]) if ea["type"] == "OPEN" else None
                if ov is not None:
                    lines.append(f"Range: {A} {ea['range'][0]}..{ea['range'][1]} · {B} {eb['range'][0]}..{eb['range'][1]}"
                                 f" · sama {ov * 100:.0f}%")
                notes.append(self._note("ok", f"✅ SAMA: keduanya {VERB[ea['type']]} {ea['name']}", lines))

        # actions the other side did not repeat within the window
        alerted_pools = {}
        groups = {}
        for e in a_left + b_left:
            if now - e["ts"] <= self.window or not fresh(e["key"]):
                continue
            groups.setdefault((e["side"], e["lb_pair"]), []).append(e)
        for (side, lb), evs in groups.items():
            who, other = (A, B) if side == "a" else (B, A)
            verbs = "+".join(dict.fromkeys(VERB[e["type"]] for e in evs))
            lines = [f"{other} tidak melakukan hal yang sama dalam {self.window // 60} menit."]
            lines += [f"{time.strftime('%d/%m %H:%M', time.localtime(e['ts']))} {who} {VERB[e['type']]}" for e in evs]
            if side == "b":
                lines.append(f"Kalau kamu copy {B}, posisi ini ikut ter-copy padahal {A} tidak.")
            else:
                lines.append(f"Kalau kamu copy {B}, kamu tidak ikut aksi {A} ini.")
            notes.append(self._note("beda", f"⚠️ BEDA: {who} {verbs} {self._pair(lb)[0]}, {other} tidak", lines))
            alerted_pools[lb] = now
            self.state["pool_alerts"][lb] = now

        # what both hold right now
        hold_a, hold_b = self._holdings(self.a), self._holdings(self.b)
        rows = []
        for lb in sorted(set(hold_a) | set(hold_b)):
            ra, rb = hold_a.get(lb), hold_b.get(lb)
            st = self.state["pools"].setdefault(lb, {})
            if ra and rb:
                ov = range_overlap((ra["lower_bin"], ra["upper_bin"]), (rb["lower_bin"], rb["upper_bin"]))
                status = "SAMA"
            else:
                ov, status = None, f"{A} SAJA" if ra else f"{B} SAJA"
            if status == "SAMA":
                if st.get("alerted"):
                    notes.append(self._note("ok", f"✅ SAMA LAGI: {self._pair(lb)[0]}", [f"{A} dan {B} sama-sama pegang pool ini."]))
                st.clear()
            else:
                if st.get("status") != status:
                    holder, other = (self.a, self.b) if ra else (self.b, self.a)
                    st.update(status=status, since=self._apart_since(lb, holder, other, now), alerted=False)
                recent = now - self.state["pool_alerts"].get(lb, 0) < 2 * self.window
                if not st["alerted"] and now - st["since"] > self.window and not first:
                    st["alerted"] = True
                    if not recent:
                        holder = A if ra else B
                        notes.append(self._note("beda", f"⚠️ BEDA: {self._pair(lb)[0]} cuma dipegang {holder}",
                                                [f"Sudah {int((now - st['since']) // 60)} menit, "
                                                 f"{B if ra else A} tidak punya posisi di pool ini."]))
                elif first and now - st["since"] > self.window:
                    st["alerted"] = True
            rows.append({"lb_pair": lb, "pair": self._pair(lb)[0], "status": status,
                         "a": ra and {"range": [ra["lower_bin"], ra["upper_bin"]], "value_usd": ra.get("value_usd"),
                                      "in_range": ra.get("in_range")},
                         "b": rb and {"range": [rb["lower_bin"], rb["upper_bin"]], "value_usd": rb.get("value_usd"),
                                      "in_range": rb.get("in_range")},
                         "overlap": ov, "since": st.get("since")})
        for lb in [lb for lb in self.state["pools"] if lb not in hold_a and lb not in hold_b]:
            if self.state["pools"][lb].get("alerted"):
                notes.append(self._note("ok", f"✅ SAMA LAGI: {self._pair(lb)[0]}", [f"{A} dan {B} sama-sama sudah keluar."]))
            del self.state["pools"][lb]

        # spot trades of the copied wallet (not LP: copy tools usually ignore them, still worth knowing)
        for e in self.b.state["events"]:
            if e["type"] != "SWAP" or (e.get("ts") or 0) < now - STATS_WINDOW:
                continue
            key = f"swap:{e.get('sig')}"
            usd = self._swap_usd(e)
            if usd is not None and usd >= SPOT_MIN_USD and fresh(key):
                sent = ", ".join(f"{v:.4g} {self.b.tokens.get(m).symbol}" for m, v in e["sent"].items())
                recv = ", ".join(f"{v:.4g} {self.b.tokens.get(m).symbol}" for m, v in e["received"].items())
                notes.append(self._note("info", f"ℹ️ {B} swap spot ≈{fmt_usd(usd)}",
                                        [f"{sent} → {recv}", f"Bukan posisi LP, dan {A} tidak ikut."]))

        # score + verdict
        delays = [eb["ts"] - ea["ts"] for ea, eb in pairs]
        a_old = [e for e in a_left if now - e["ts"] > self.window]
        b_old = [e for e in b_left if now - e["ts"] > self.window]
        stats = {"a_actions": len(ev_a), "a_matched": len(pairs), "b_actions": len(ev_b),
                 "b_extra": len(b_old), "a_missed": len(a_old), "moved": len(moved),
                 "median_delay": statistics.median(delays) if delays else None}
        reasons = []
        for r in rows:
            if r["status"] != "SAMA" and now - (r["since"] or now) > self.window:
                reasons.append(f"{r['pair']}: {r['status']} sejak {time.strftime('%d/%m %H:%M', time.localtime(r['since']))}")
        for e in a_old + b_old:
            if now - e["ts"] < RECENT_BEDA:
                who, other = (A, B) if e["side"] == "a" else (B, A)
                reasons.append(f"{time.strftime('%d/%m %H:%M', time.localtime(e['ts']))} {who} {VERB[e['type']]} "
                               f"{e['name']}, {other} tidak")
        waiting = []
        for e in a_left + b_left:
            if now - e["ts"] <= self.window:
                who, other = (A, B) if e["side"] == "a" else (B, A)
                waiting.append(f"{who} {VERB[e['type']]} {e['name']} {int(now - e['ts'])} dtk lalu, menunggu {other} "
                               f"(maks {self.window // 60} menit)")
        waiting += [f"{r['pair']}: {r['status']} (menunggu)" for r in rows
                    if r["status"] != "SAMA" and now - (r["since"] or now) <= self.window]
        verdict = "BEDA" if reasons else ("MENUNGGU" if waiting else "SAMA")
        if verdict == "MENUNGGU":
            reasons = waiting
        self.view = {"verdict": verdict, "reasons": reasons, "pools": rows, "stats": stats,
                     "a": {"name": A, "address": self.a.address}, "b": {"name": B, "address": self.b.address},
                     "window_min": self.window // 60, "sol_only": self.sol_only,
                     "log": self.state["log"][-20:][::-1], "checked_at": int(now)}

        if first:
            notes = [self._note("info", f"👀 Pembanding aktif: {A} vs {B}", self.summary_lines())]
        self.view["log"] = self.state["log"][-20:][::-1]
        self.started = True
        self.state["seen"] = (self.state["seen"] + new_seen)[-SEEN_KEPT:]
        self.save()
        return notes

    def _swap_usd(self, e):
        prices = self.b.prices
        for side in ("sent", "received"):
            for m, v in (e.get(side) or {}).items():
                usd = prices.usd(m, v)
                if usd:
                    return usd
        return None

    def summary_lines(self):
        v, s = self.view, self.view["stats"]
        A, B = self.a.display, self.b.display
        lines = [f"Status sekarang: {v['verdict']}"]
        lines += [f"• {r}" for r in v["reasons"][:6]]
        if s:
            md = s["median_delay"]
            lines.append(f"24 jam terakhir: {A} {s['a_actions']} aksi buka/tutup, {B} ikut {s['a_matched']}"
                         + (f" (median {_delay(md)} {A})" if md is not None else ""))
            lines.append(f"Aksi {B} yang tidak dilakukan {A}: {s['b_extra']} · aksi {A} yang tidak diikuti {B}: {s['a_missed']}"
                         f" · geser range (diabaikan): {s.get('moved', 0)}")
        return lines
