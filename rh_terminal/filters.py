"""Entry check for one pool: gates (must pass), flags (warnings), and the verdict.

  SKIP     a gate that will not change: no LP fee (0% / hook-priced), or a fee tier so high
           it is a honeypot rather than a market
  WAIT     a gate that can change: pool too young, TVL too thin or unknown, mcap below the
           floor, fees too slow
  CAREFUL  gates pass, some flag shows
  OPEN     gates pass, no flag

Market context (volume pace, buyers vs sellers) is shown as info and never moves the level.
These are starting values for Robinhood Chain, not a backtested spec: tune them in ⚙.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

HARD = ("lpFee",)


def best_fee_pct_day(row: dict) -> float | None:
    v = [row["feePctDay"].get(w) for w in ("1h", "24h")]
    v = [x for x in v if x is not None]
    return max(v) if v else None


def evaluate(row: dict, cfg: dict, now: float, size_usd: float | None = None) -> dict:
    u, g, f = cfg["universe"], cfg["gates"], cfg["flags"]
    gates, flags = [], []
    fee = row["lpFeePct"]
    if fee is None or fee < u["minLpFeePct"]:
        gates.append(f"lpFee: {fee if fee is not None else '?'}% < {u['minLpFeePct']}%")
    elif fee > u["maxLpFeePct"]:
        gates.append(f"lpFee: {fee}% > {u['maxLpFeePct']}%")
    age_min = row["ageHours"] * 60 if row.get("ageHours") is not None else None
    if g.get("minPoolAgeMinutes") and age_min is not None and age_min < g["minPoolAgeMinutes"]:
        gates.append(f"poolAge: {age_min:.0f}m < {g['minPoolAgeMinutes']:.0f}m")
    tvl = row.get("tvlUsd")
    if g.get("minTvlUsd"):
        if tvl is None:
            gates.append("tvl: unknown")
        elif tvl < g["minTvlUsd"]:
            gates.append(f"tvl: ${tvl:,.0f} < ${g['minTvlUsd']:,.0f}")
    mc = row.get("mcapUsd")
    if g.get("minMcapUsd") and (mc is None or mc < g["minMcapUsd"]):
        gates.append(f"mcap: {'?' if mc is None else f'${mc:,.0f}'} < ${g['minMcapUsd']:,.0f}")
    best = best_fee_pct_day(row)
    if g.get("minFeePctDay") and (best is None or best < g["minFeePctDay"]):
        gates.append(f"feePace: {0 if best is None else best:.1f}%/day < {g['minFeePctDay']}%/day")

    chg = row.get("chg") or {}
    if f.get("pump1hPct") is not None and (chg.get("1h") or 0) >= f["pump1hPct"]:
        flags.append(f"pump1h {chg['1h']:+.0f}%")
    if f.get("drop6hPct") is not None and chg.get("6h") is not None and chg["6h"] <= f["drop6hPct"]:
        flags.append(f"drop6h {chg['6h']:+.0f}%")
    if f.get("minTvlUsd") and tvl is not None and tvl < f["minTvlUsd"]:
        flags.append(f"tvl ${tvl:,.0f}")
    tx = (row.get("txns") or {}).get("h1") or {}
    n = (tx.get("buys") or 0) + (tx.get("sells") or 0)
    if f.get("sellsShare1hPct") and n >= 20 and tx["sells"] / n * 100 >= f["sellsShare1hPct"]:
        flags.append(f"sellers {tx['sells'] / n * 100:.0f}% of 1h trades")
    if f.get("hookPool") and row.get("hooks") and int(row["hooks"], 16):
        flags.append("hook pool")
    night = f.get("nightEntryLocalHours")
    if night:
        h = (datetime.fromtimestamp(now, timezone.utc) + timedelta(hours=f.get("utcOffsetHours", 7))).hour
        if night[0] <= h < night[1]:
            flags.append(f"night entry {h:02d}:00")
    if size_usd and tvl:
        row["sizeVsTvlPct"] = round(size_usd / tvl * 100, 2)

    hard = [x for x in gates if x.startswith(HARD)]
    level = "SKIP" if hard else "WAIT" if gates else "CAREFUL" if flags else "OPEN"
    bad = [{"ok": False, "text": x} for x in gates] + [{"ok": False, "text": x, "flag": True} for x in flags]
    good = []
    if not gates:
        good.append({"ok": True, "text": f"Gates pass: fee {fee}% · {best:.1f}%/day pace · TVL ${tvl:,.0f}"})
    info = []
    v1, v24 = (row.get("vol") or {}).get("1h"), (row.get("vol") or {}).get("24h")
    if v1 and v24 and (row.get("ageHours") or 0) >= 6:
        pace = v1 * 24 / v24
        if pace >= 1.5:
            info.append({"ok": True, "text": f"Volume picking up: last hour runs {pace:.1f}x the 24h pace"})
        elif pace <= 0.4:
            info.append({"ok": False, "text": f"Quiet: last hour runs {pace:.1f}x the 24h pace"})
    t24 = (row.get("txns") or {}).get("h24") or {}
    n24 = (t24.get("buys") or 0) + (t24.get("sells") or 0)
    if n24 >= 50:
        share = t24["buys"] / n24 * 100
        if share >= 55:
            info.append({"ok": True, "text": f"Buyers lead: {share:.0f}% of 24h trades are buys"})
        elif share <= 45:
            info.append({"ok": False, "text": f"Sellers lead: {100 - share:.0f}% of 24h trades are sells"})
    score = len(good) + sum(1 for x in info if x["ok"]) - len(bad) - sum(1 for x in info if not x["ok"])
    return {"level": level, "score": score, "reasons": bad + good, "info": info,
            "gatesFailed": gates, "flags": flags}


def max_position_usd(cfg: dict) -> float:
    """Loss budget per position, sized so a 90% drawdown loses no more than it (as in DLMM)."""
    p = cfg["position"]
    return p["bankrollUsd"] * p["lossBudgetPct"] / 100 / 0.9


def planned_size_usd(cfg: dict) -> float:
    return min(cfg["position"]["defaultSizeUsd"], max_position_usd(cfg))
