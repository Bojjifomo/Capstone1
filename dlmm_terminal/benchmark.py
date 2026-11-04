"""Benchmark wallet: what kind of pools a proven LP wallet enters, and does a live pool look like that.

build()   pulls the wallet's closed DLMM positions of the last N days (Meteora API, 0 RPC),
          groups slices opened together into one entry, and rebuilds each entry's features
          at entry time (pool age, fees 1h / 24h before entry, mcap, price moves) + its result.
profile() turns those entries into the wallet's "habitat": bin steps it uses and the
          p10-p90 band of each feature.
match()   scores a live candidate against that habitat.

The spec (section 2) found no feature that separates winners from losers on unseen tokens,
so the habitat is a similarity check ("pools like the ones this wallet makes money in"),
on top of the v1 gates, not a predictor. Price moves are logged but not matched on.
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone

from . import api, filters

# Features matched against the habitat band. Kept to what the wallet *chooses*
# (pool type, age, size, fee activity); price-move features failed the spec's unseen-token test.
MATCH_DIMS = ("poolAgeHours", "mcapUsd", "fees24hUsd", "fees1hUsd")
INFO_DIMS = ("priceChg1h", "priceChg6h", "holdMin", "pnlPct")


def _side(row: dict) -> str | None:
    if row.get("tokenYMint") == api.SOL_MINT:
        return "y"
    if row.get("tokenXMint") == api.SOL_MINT:
        return "x"
    return None


def group_entries(positions: list[dict], gap_s: int = 1800) -> list[list[dict]]:
    """Slices of one ladder are opened minutes apart: positions created within `gap_s`
    of the first one in the same pool count as one entry."""
    out: list[list[dict]] = []
    for p in sorted(positions, key=lambda p: p["createdAt"]):
        if out and p["createdAt"] - out[-1][0]["createdAt"] <= gap_s:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def _bucket_sum(rows: list[dict], key: str, start: float, end: float) -> float:
    return sum(r.get(key) or 0 for r in rows if start <= r["timestamp"] < end)


def _close_at(candles: list[dict], ts: float, side: str) -> float | None:
    before = [c for c in candles if c["timestamp"] <= ts]
    return filters.to_token_in_sol(before[-1]["close"], side) if before else None


def entry_features(group: list[dict], snap: dict, vol: list[dict], candles: list[dict], side: str,
                   utc_offset_h: int = 7) -> dict:
    t = min(p["createdAt"] for p in group)
    hour = t - t % 3600
    dep = sum(float((p.get("allTimeDeposits") or {}).get("total", {}).get("sol") or 0) for p in group)
    pnl = sum(float(p.get("pnlSol") or 0) for p in group)
    price = _close_at(candles, t, side)
    p1, p6 = _close_at(candles, t - 3600, side), _close_at(candles, t - 6 * 3600, side)
    tok = snap["token_x"] if side == "y" else snap["token_y"]
    now_price = filters.to_token_in_sol(snap.get("current_price") or 0, side)
    mcap = tok.get("market_cap")
    closed = max((p.get("closedAt") or t) for p in group)
    return {
        "pool": snap.get("address"),
        "symbol": tok.get("symbol"),
        "entryTs": datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds"),
        "slices": len(group),
        "binStep": (snap.get("pool_config") or {}).get("bin_step"),
        "poolAgeHours": round((t - (snap.get("created_at") or 0) / 1000) / 3600, 2),
        # Fees of the last full hour / 24 full hours before the entry (no TVL history exists).
        "fees1hUsd": round(_bucket_sum(vol, "fees", hour - 3600, hour), 2),
        "fees24hUsd": round(_bucket_sum(vol, "fees", hour - 24 * 3600, hour), 2),
        # Approx: today's mcap scaled by the token's SOL price then vs now.
        "mcapUsd": round(mcap * price / now_price) if mcap and price and now_price else None,
        "priceChg1h": round((price / p1 - 1) * 100, 2) if price and p1 else None,
        "priceChg6h": round((price / p6 - 1) * 100, 2) if price and p6 else None,
        "entryHourLocal": (datetime.fromtimestamp(t, timezone.utc).hour + utc_offset_h) % 24,
        "capitalSol": round(dep, 4),
        "pnlSol": round(pnl, 4),
        "pnlPct": round(pnl / dep * 100, 2) if dep else None,
        "holdMin": round((closed - t) / 60, 1),
        "win": pnl > 0,
    }


def build(wallet: str, days: float = 20, now: float | None = None, log=print) -> list[dict]:
    now = now or time.time()
    since = now - days * 86400
    pools = [p for p in api.portfolio(wallet, since) if (p.get("lastClosedAt") or 0) >= since and _side(p)]
    entries = []
    for i, row in enumerate(pools, 1):
        addr, side = row["poolAddress"], _side(row)
        try:
            pos = [p for p in api.position_pnl(addr, wallet, "closed") if p["createdAt"] >= since]
            if not pos:
                continue
            groups = group_entries(pos)
            snap = api.pool(addr)
            start = int(min(p["createdAt"] for p in pos) - 25 * 3600)
            end = int(max(p["createdAt"] for p in pos) + 3600)
            vol = api.chunked(api.volume_history, addr, start, end)
            candles = api.chunked(api.ohlcv, addr, start, end)
        except Exception as exc:
            log(f"  skip {row.get('tokenX')}/{row.get('tokenY')}: {exc}")
            continue
        for g in groups:
            entries.append(entry_features(g, snap, vol, candles, side))
        log(f"  [{i}/{len(pools)}] {entries[-1]['symbol']}: {len(groups)} entries")
    return sorted(entries, key=lambda e: e["entryTs"])


def _q(vals: list[float], q: float) -> float:
    vals = sorted(vals)
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * q
    lo = int(k)
    return vals[lo] + (vals[min(lo + 1, len(vals) - 1)] - vals[lo]) * (k - lo)


def profile(entries: list[dict], wallet: str, days: float, lo_q: float = 0.1, hi_q: float = 0.9) -> dict:
    wins = [e for e in entries if e["win"]]
    bands = {}
    for d in MATCH_DIMS + INFO_DIMS:
        vals = [e[d] for e in entries if e.get(d) is not None]
        if vals:
            bands[d] = {"lo": round(_q(vals, lo_q), 2), "hi": round(_q(vals, hi_q), 2),
                        "median": round(statistics.median(vals), 2),
                        "winMedian": round(statistics.median([e[d] for e in wins if e.get(d) is not None]), 2)
                        if any(e.get(d) is not None for e in wins) else None}
    steps: dict[str, dict] = {}
    for e in entries:
        s = steps.setdefault(str(e["binStep"]), {"entries": 0, "wins": 0, "pnlSol": 0.0})
        s["entries"] += 1
        s["wins"] += e["win"]
        s["pnlSol"] = round(s["pnlSol"] + e["pnlSol"], 4)
    return {
        "wallet": wallet,
        "days": days,
        "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries": len(entries),
        "tokens": len({e["pool"] for e in entries}),
        "wins": len(wins),
        "winRatePct": round(len(wins) / len(entries) * 100, 1) if entries else None,
        "pnlSol": round(sum(e["pnlSol"] for e in entries), 4),
        "capitalSolMedian": round(statistics.median([e["capitalSol"] for e in entries]), 3) if entries else None,
        "binSteps": steps,
        "bands": bands,
        "quantiles": [lo_q, hi_q],
    }


def match(c: dict, prof: dict) -> dict:
    """c: a live candidate (filters.evaluate row + fees1hUsd / fees24hUsd).
    Returns which habitat dims the pool sits inside, and a 0..1 score."""
    hits, misses = [], []
    steps = prof.get("binSteps") or {}
    good_steps = {s for s, v in steps.items() if v["pnlSol"] > 0} or set(steps)
    (hits if str(c.get("binStep")) in good_steps else misses).append(
        f"binStep {c.get('binStep')}" + ("" if str(c.get("binStep")) in good_steps else f" ∉ {sorted(good_steps, key=float)}"))
    for d in MATCH_DIMS:
        b, v = prof["bands"].get(d), c.get(d)
        if not b:
            continue
        if v is None:
            misses.append(f"{d} n/a")
        elif b["lo"] <= v <= b["hi"]:
            hits.append(f"{d} {_fmt(d, v)}")
        else:
            misses.append(f"{d} {_fmt(d, v)} ∉ {_fmt(d, b['lo'])}–{_fmt(d, b['hi'])}")
    n = len(hits) + len(misses)
    return {"score": round(len(hits) / n, 2) if n else 0, "hits": hits, "misses": misses, "dims": n}


def _fmt(d: str, v: float) -> str:
    if d == "poolAgeHours":
        return f"{v:.0f}h" if v >= 1 else f"{v * 60:.0f}m"
    if d.endswith("Usd"):
        return f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"
    return f"{v:g}"


def candidate_dims(snapshot: dict) -> dict:
    """Live equivalents of the entry-time fee features."""
    fees = snapshot.get("fees") or {}
    return {"fees1hUsd": fees.get("1h") or 0, "fees24hUsd": fees.get("24h") or 0}
