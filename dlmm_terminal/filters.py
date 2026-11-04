"""Entry filter: features -> gates -> flags -> decision (spec sections 1, 5).

Pure functions. Network calls live in api.py / cli.py so this is easy to test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .api import SOL_MINT


def sol_side(snapshot: dict) -> str | None:
    """'y' if the pool is TOKEN-SOL, 'x' if SOL-TOKEN, None if SOL isn't in the pair."""
    if snapshot.get("token_y", {}).get("address") == SOL_MINT:
        return "y"
    if snapshot.get("token_x", {}).get("address") == SOL_MINT:
        return "x"
    return None


def token_of(snapshot: dict) -> dict:
    return snapshot["token_x"] if sol_side(snapshot) == "y" else snapshot["token_y"]


def to_token_in_sol(pool_price: float, side: str | None) -> float:
    """Pool price is token_x priced in token_y. Return the memecoin priced in SOL."""
    if not pool_price:
        return 0.0
    return pool_price if side == "y" else 1.0 / pool_price


def fee_apr(fees_usd: float, tvl: float, hours: float) -> float:
    """Fees over N hours / TVL, annualised, in percent."""
    if tvl <= 0 or hours <= 0:
        return 0.0
    return fees_usd / tvl * 8760 / hours * 100


def _window_fee_apr(snapshot: dict, window_h: int, age_h: float) -> float:
    fees = (snapshot.get("fees") or {}).get(f"{window_h}h", 0) or 0
    hours = min(window_h, age_h) if age_h > 0 else window_h  # younger pool: use the hours it has
    return fee_apr(fees, snapshot.get("tvl") or 0, max(hours, 1 / 60))


def _chg(closes: list[float], hours_back: int) -> float | None:
    if len(closes) <= hours_back:
        return None
    then = closes[-1 - hours_back]
    return (closes[-1] / then - 1) * 100 if then else None


def features(snapshot: dict, candles_1h: list[dict], size_sol: float, now: datetime) -> dict:
    side = sol_side(snapshot)
    tok = token_of(snapshot) if side else snapshot.get("token_x", {})
    sol_usd = (snapshot["token_y"] if side == "y" else snapshot["token_x"]).get("price", 0) if side else 0
    tvl = snapshot.get("tvl") or 0
    age_h = (now.timestamp() * 1000 - (snapshot.get("created_at") or 0)) / 3_600_000
    closes = [to_token_in_sol(c["close"], side) for c in candles_1h]
    last24 = candles_1h[-24:]
    highs = [to_token_in_sol(c["high" if side == "y" else "low"], side) for c in last24]
    lows = [to_token_in_sol(c["low" if side == "y" else "high"], side) for c in last24]
    cfg = snapshot.get("pool_config") or {}
    return {
        "pool": snapshot.get("address"),
        "name": snapshot.get("name"),
        "tokenMint": tok.get("address"),
        "symbol": tok.get("symbol"),
        "solSide": side,
        "poolAgeHours": round(age_h, 2),
        "tvlUsd": round(tvl, 2),
        "feeApr1h": round(_window_fee_apr(snapshot, 1, age_h), 1),
        "feeApr24h": round(_window_fee_apr(snapshot, 24, age_h), 1),
        "volTvl24h": round(((snapshot.get("volume") or {}).get("24h", 0) or 0) / tvl, 2) if tvl else None,
        "mcapUsd": tok.get("market_cap"),
        "priceChg1h": _round(_chg(closes, 1)),
        "priceChg6h": _round(_chg(closes, 6)),
        "priceChg24h": _round(_chg(closes, 24)),
        "range24hPct": _round((max(highs) / min(lows) - 1) * 100 if lows and min(lows) > 0 else None),
        "binStep": cfg.get("bin_step"),
        "baseFeePct": cfg.get("base_fee_pct"),
        "solUsd": sol_usd,
        "sizeSol": size_sol,
        "sizeVsTvlPct": round(size_sol * sol_usd / tvl * 100, 2) if tvl and sol_usd else None,
        "entryHourLocal": None,  # filled by evaluate()
        "holders": tok.get("holders"),
        "priceSol": closes[-1] if closes else to_token_in_sol(snapshot.get("current_price") or 0, side),
    }


def _round(v, n=2):
    return None if v is None else round(v, n)


def max_position_sol(cfg: dict) -> float:
    """Spec: max position = loss budget / 0.9 (one bad position fell 93%)."""
    p = cfg["position"]
    return p["bankrollSol"] * p["lossBudgetPct"] / 100 / 0.9


def planned_size_sol(cfg: dict, requested: float | None = None) -> float:
    want = requested if requested is not None else cfg["position"].get("defaultSizeSol", max_position_sol(cfg))
    return round(min(want, max_position_sol(cfg)), 4)


def gates(f: dict, cfg: dict) -> tuple[list[str], list[str]]:
    u, g = cfg["universe"], cfg["gates"]
    checks = {
        "solQuoted": f["solSide"] is not None,
        "binStep": f["binStep"] is not None and u["binStepMin"] <= f["binStep"] <= u["binStepMax"],
        "poolAge": f["poolAgeHours"] * 60 >= g["minPoolAgeMinutes"],
        "feeApr24h": f["feeApr24h"] >= g["minFeeApr24hPct"],
        "sizeVsTvl": f["sizeVsTvlPct"] is not None and f["sizeVsTvlPct"] <= g["maxPositionPctOfTvl"],
    }
    passed = [k for k, ok in checks.items() if ok]
    failed = [_why(k, f, cfg) for k, ok in checks.items() if not ok]
    return passed, failed


def _why(k: str, f: dict, cfg: dict) -> str:
    u, g = cfg["universe"], cfg["gates"]
    return {
        "solQuoted": "solQuoted: SOL not in pair",
        "binStep": f"binStep: {f['binStep']} outside {u['binStepMin']}-{u['binStepMax']}",
        "poolAge": f"poolAge: {f['poolAgeHours'] * 60:.0f}m < {g['minPoolAgeMinutes']}m",
        "feeApr24h": f"feeApr24h: {f['feeApr24h']:.0f}% < {g['minFeeApr24hPct']}%",
        "sizeVsTvl": f"sizeVsTvl: {f['sizeVsTvlPct']}% > {g['maxPositionPctOfTvl']}%",
    }[k]


def flags(f: dict, cfg: dict) -> list[str]:
    fl, out = cfg["flags"], []
    if f["priceChg1h"] is not None and f["priceChg1h"] >= fl["pump1hPct"]:
        out.append(f"pump1h +{f['priceChg1h']:.0f}%")
    if f["priceChg6h"] is not None and f["priceChg6h"] <= fl["drop6hPct"]:
        out.append(f"drop6h {f['priceChg6h']:.0f}%")
    if f["tvlUsd"] < fl["minTvlUsd"]:
        out.append(f"tvl ${f['tvlUsd']:,.0f}")
    start, end = fl["nightEntryLocalHours"]
    if f["entryHourLocal"] is not None and start <= f["entryHourLocal"] < end:
        out.append(f"night entry {f['entryHourLocal']:02d}h")
    return out


def evaluate(snapshot: dict, candles_1h: list[dict], cfg: dict, now: datetime | None = None,
             size_sol: float | None = None) -> dict:
    """One candidate row in the spec's log schema."""
    now = now or datetime.now(timezone.utc)
    size = planned_size_sol(cfg, size_sol)
    f = features(snapshot, candles_1h, size, now)
    f["entryHourLocal"] = (now + timedelta(hours=cfg["flags"].get("utcOffsetHours", 7))).hour
    passed, failed = gates(f, cfg)
    fl = flags(f, cfg)
    bottom = cfg["position"]["rangeBottomPct"]
    return {
        "ts": now.isoformat(timespec="seconds"),
        **f,
        "gatesPassed": passed,
        "gatesFailed": failed,
        "flags": fl,
        "decision": "propose" if not failed else "skip",
        "decisionReason": "all gates pass (assist mode: you confirm)" if not failed else "; ".join(failed),
        "rangeType": "wide" if bottom <= -65 else "standard",
        "plan": {
            "strategy": cfg["position"]["strategy"], "side": cfg["position"]["side"],
            "sizeSol": size, "rangeBottomPct": bottom,
            "rangeBottomPriceSol": f["priceSol"] * (1 + bottom / 100) if f["priceSol"] else None,
        },
    }
