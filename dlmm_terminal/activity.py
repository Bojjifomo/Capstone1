"""Fee activity over the last 24h, from hourly history: catches "dead, then suddenly alive"
pools (a pump-and-dump on a quiet token).

Example that prompted it (DAVID-SOL, Nov 2026): 20+ hours with zero fees, then 94% of the
24h fees inside 3 hours, market cap 378k -> 3.2M -> 850k.

  deadStreak   longest run of consecutive hours with fees < deadHourPct of the 24h total
  top3Share    share of the 24h fees made in the 3 busiest hours
  activeHours  hours with fees >= 1% of the 24h total

A pool counts as revived when deadStreak >= minDeadHours AND top3Share >= minTop3Share.
Only pools old enough to have been dead (>= minDeadHours + 3 hours of history) qualify, so
a new pool's first hours are not mistaken for a revival.

What the benchmark data says (WKX8…Zmpt, 20 days, 83 entries with history): 4 entries
matched (PMUP x2, WASTED x2), all 4 won (+1.42 SOL). None of the spec's 5 big losers matched.
So this is the owner's risk call, not a proven filter; signal.revival.mode can drop it to
"flag" (warn only) or "off".
"""
from __future__ import annotations


def features(hourly: list[dict], age_h: float, dead_hour_pct: float = 0.5) -> dict | None:
    """hourly: Meteora volume/history 1h rows, oldest first, ending at the current hour."""
    n = max(1, min(24, int(age_h)))
    fees = [r.get("fees") or 0 for r in hourly[-n:]]
    total = sum(fees)
    if total <= 0 or not fees:
        return None
    streak = best = 0
    for f in fees:
        streak = streak + 1 if f < dead_hour_pct / 100 * total else 0
        best = max(best, streak)
    top = sorted(fees, reverse=True)
    return {
        "hours": len(fees),
        "activeHours": sum(f >= 0.01 * total for f in fees),
        "top3Share": round(sum(top[:3]) / total, 3),
        "deadStreak": best,
        "last4Share": round(sum(fees[-4:]) / total, 3),
    }


def revived(f: dict | None, cfg: dict) -> bool:
    if not f:
        return False
    return (f["hours"] >= cfg["minDeadHours"] + 3 and f["deadStreak"] >= cfg["minDeadHours"]
            and f["top3Share"] >= cfg["minTop3Share"])


def describe(f: dict) -> str:
    return (f"dead→alive: {f['deadStreak']}h dead, top 3 hours = {f['top3Share'] * 100:.0f}% of 24h fees, "
            f"{f['activeHours']}/{f['hours']}h active")
