"""Exit rules for an open SOL-only DLMM position (spec section 4).

Pure: give it one position snapshot and the state from the last tick, get back events.
Prices are always the memecoin priced in SOL, so the range sits *below* entry.

What the data said (131 positions replayed on 5-minute paths, vs. the real exits):
  take-profit +1/+2/+5/+10%     -7.16 / -7.57 / -4.37 / -1.06 SOL
  stop-loss -10/-15/-25% PnL    -7.60 / -4.24 / -1.35 SOL
  trailing 5/10/25 pts          -7.27 / -7.66 / -1.82 SOL
  close 10 min below range      +0.22 SOL   <- the only rule that added SOL
  price stop -40% below entry   roughly break-even in hindsight, -6.47 SOL on unseen tokens
So TP / SL / trailing / max-hold default to off. They are here so you can turn them on
deliberately and so v2 can re-test them on logged paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Tick:
    position: str
    pool: str
    symbol: str
    now: float              # unix seconds
    created_at: float | None
    pnl_pct: float          # SOL PnL %, fees included
    pnl_sol: float | None
    price: float            # token priced in SOL
    range_min: float        # token priced in SOL
    range_max: float


@dataclass
class State:
    peak_pnl: float = float("-inf")
    below_since: float | None = None
    alerted: list[str] = field(default_factory=list)
    close_sent: bool = False

    def to_dict(self) -> dict:
        return {"peak_pnl": None if self.peak_pnl == float("-inf") else self.peak_pnl,
                "below_since": self.below_since, "alerted": self.alerted, "close_sent": self.close_sent}

    @classmethod
    def from_dict(cls, d: dict | None) -> "State":
        d = d or {}
        peak = d.get("peak_pnl")
        return cls(float("-inf") if peak is None else peak, d.get("below_since"),
                   list(d.get("alerted", [])), bool(d.get("close_sent")))


def _once(state: State, key: str, active: bool) -> bool:
    """True the first time `active` turns on; re-arms after it turns off."""
    if active and key not in state.alerted:
        state.alerted.append(key)
        return True
    if not active and key in state.alerted:
        state.alerted.remove(key)
    return False


def evaluate(t: Tick, state: State, ex: dict) -> list[dict]:
    """Return events: {'kind': 'alert'|'close', 'rule': str, 'msg': str}."""
    ev: list[dict] = []
    state.peak_pnl = max(state.peak_pnl, t.pnl_pct)
    entry = t.range_max  # SOL-only spot range: top of the range is the entry price
    span = t.range_max - t.range_min
    below = t.price < t.range_min
    if below:
        if state.below_since is None:
            state.below_since = t.now
    else:
        state.below_since = None

    def close(rule: str, msg: str):
        ev.append({"kind": "close", "rule": rule, "msg": msg})

    # --- closes (checked first; stop beats take-profit, as in the replay) ---
    n = ex.get("closeBelowRangeAfterMin")
    if n is not None and state.below_since is not None and t.now - state.below_since >= n * 60:
        close("belowRange", f"price below range bottom for {(t.now - state.below_since) / 60:.0f} min")
    n = ex.get("priceStopBelowEntryPct")
    if n is not None and entry and t.price <= entry * (1 + n / 100):
        close("priceStop", f"price {(t.price / entry - 1) * 100:.1f}% vs entry (stop {n}%)")
    n = ex.get("stopLossPct")
    if n is not None and t.pnl_pct <= n:
        close("stopLoss", f"PnL {t.pnl_pct:.2f}% <= {n}%")
    n = ex.get("trailingPts")
    if n is not None and state.peak_pnl >= ex.get("trailingArmPct", 0) and t.pnl_pct <= state.peak_pnl - n:
        close("trailing", f"PnL {t.pnl_pct:.2f}% is {state.peak_pnl - t.pnl_pct:.2f} pts under peak {state.peak_pnl:.2f}%")
    n = ex.get("takeProfitPct")
    if n is not None and t.pnl_pct >= n:
        close("takeProfit", f"PnL {t.pnl_pct:.2f}% >= {n}%")
    n = ex.get("maxHoldMin")
    if n is not None and t.created_at and t.now - t.created_at >= n * 60:
        close("maxHold", f"held {(t.now - t.created_at) / 60:.0f} min >= {n}")

    # --- alerts (fire once, re-arm when the condition clears) ---
    n = ex.get("alertAtPnlPct")
    if n is not None and _once(state, "pnl", t.pnl_pct <= n):
        ev.append({"kind": "alert", "rule": "pnlAlert", "msg": f"PnL {t.pnl_pct:.2f}% <= {n}%: your call"})
    if ex.get("alertBottomThirdOfRange") and span > 0:
        if _once(state, "bottomThird", t.price < t.range_min + span / 3):
            ev.append({"kind": "alert", "rule": "bottomThird",
                       "msg": f"price in bottom third of range ({(t.price / entry - 1) * 100:.1f}% vs entry)"})
    if _once(state, "outOfRange", below):
        ev.append({"kind": "alert", "rule": "outOfRange", "msg": "price below range bottom: earning no fees"})

    closes = [e for e in ev if e["kind"] == "close"]
    if closes and state.close_sent:
        ev = [e for e in ev if e["kind"] != "close"]
    elif closes:
        ev = [closes[0]] + [e for e in ev if e["kind"] != "close"]  # one close reason is enough
    return ev
