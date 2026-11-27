"""Entry check for one pool: gates (must pass), flags (warnings), and the verdict.

  SKIP     a gate that will not change: no LP fee (0% / hook-priced), or a fee tier so high
           it is a honeypot rather than a market
  WAIT     a gate that can change: pool too young, TVL too thin or unknown, mcap below the
           floor, fees too slow
  CAREFUL  gates pass, some flag shows
  OPEN     gates pass, no flag

Market context (volume pace, buyers vs sellers) is shown as info and never moves the level.
These are starting values for Elysium, not a backtested spec: tune them in ⚙.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

HARD = ("lpFee",)


def best_fee_pct_day(row: dict) -> float | None:
    v = [row["feePctDay"].get(w) for w in ("1h", "24h")]
    v = [x for x in v if x is not None]
    return max(v) if v else None


def fee_pace_24h(row: dict) -> float | None:
    """Fees of the last 24h / TVL per day, as DLMM's feeApr24h: a pool younger than a day is paced
    over the hours it has, not diluted over 24."""
    fees, tvl = (row.get("fees") or {}).get("24h"), row.get("tvlUsd")
    if fees is None or not tvl:
        return None
    age = row.get("ageHours")
    hours = min(24.0, max(age, 1 / 60)) if age is not None else 24.0
    return fees / tvl * 100 * 24 / hours


def vol_mcap_24h(row: dict) -> float | None:
    """24h volume / market cap. DLMM owner review (Nov 2026): 0.3-3x made +2.5%, outside +0.9%."""
    v, mc = (row.get("vol") or {}).get("24h"), row.get("mcapUsd")
    return round(v / mc, 2) if v is not None and mc else None


def vol_mcap_fit(x, band=(0.3, 3)) -> float:
    """PING sort: inside the band ranks by its value, outside ranks last."""
    return x if x is not None and band[0] <= x <= band[1] else 0


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
    pace = fee_pace_24h(row)
    row["feePace24h"] = round(pace, 2) if pace is not None else None
    row["feeApr24h"] = round(pace * 365, 0) if pace is not None else None
    if g.get("minFeePctDay") and (pace is None or pace < g["minFeePctDay"]):
        gates.append(f"feePace: {0 if pace is None else pace:.1f}%/hari (24h) < {g['minFeePctDay']}%/hari")
    chg = row.get("chg") or {}
    c1 = chg.get("1h")
    # No entry after a pump (DLMM owner review, 67 entries: price 1h <= +5% at entry made +2.5%,
    # above it +0.4%). WAIT, not SKIP: it clears once the price cools. Unknown change passes.
    pumped = g.get("maxPriceChg1hPct") is not None and c1 is not None and c1 > g["maxPriceChg1hPct"]
    if pumped:
        gates.append(f"pump1h: {c1:+.0f}% > +{g['maxPriceChg1hPct']:g}% (tunggu dip)")

    if f.get("pump1hPct") is not None and (c1 or 0) >= f["pump1hPct"] and not pumped:
        flags.append(f"pump1h {c1:+.0f}%")
    if f.get("drop6hPct") is not None and chg.get("6h") is not None and chg["6h"] <= f["drop6hPct"]:
        flags.append(f"drop6h {chg['6h']:+.0f}%")
    if f.get("minTvlUsd") and tvl is not None and tvl < f["minTvlUsd"]:
        flags.append(f"tvl ${tvl:,.0f}")
    if f.get("youngPoolHours") is not None and row.get("ageHours") is not None and row["ageHours"] < f["youngPoolHours"]:
        flags.append(f"pool muda {row['ageHours']:.1f}j")
    vm = vol_mcap_24h(row)
    row["volMcap24h"] = vm
    band = f.get("volMcapBand")
    if band and vm is not None and not band[0] <= vm <= band[1]:
        flags.append(f"vol/mcap 24h {vm:.2f}x (di luar {band[0]:g}-{band[1]:g}x)")
    tx = (row.get("txns") or {}).get("h1") or {}
    n = (tx.get("buys") or 0) + (tx.get("sells") or 0)
    if f.get("sellsShare1hPct") and n >= 20 and tx["sells"] / n * 100 >= f["sellsShare1hPct"]:
        flags.append(f"sellers {tx['sells'] / n * 100:.0f}% of 1h trades")
    cl = row.get("clone")
    if f.get("cloneSymbol", True) and cl:
        flags.append(f"kloningan: {cl['others']} token lain pakai simbol {row.get('symbol')}"
                     + (f", yang paling tua {cl['original'][:8]}…" if cl.get("original") else ""))
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
        good.append({"ok": True, "text": f"Gates pass: fee {fee}% · {pace:.1f}%/hari (APR {pace * 365:,.0f}%) · TVL ${tvl:,.0f}"})
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
