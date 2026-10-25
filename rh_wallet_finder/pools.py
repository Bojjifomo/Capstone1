"""Which pools pay LPs the most fee for the capital they put in.

From positions already in the database (no RPC): every priced position closed
in the window counts with its deposit × how long it was held. Per pool:

  fee_apr    fees earned ÷ capital-days × 365: the fee rate LPs actually got
  net_apr    the same with PnL (fees + price moves / IL): what was kept
  fee_per_1k fee per $1,000 deposited per day, the same number in dollars

A pool needs a few positions from a few wallets (min_positions, min_wallets),
so one lucky position doesn't top the list; top_share says how much of the
pool's fees one wallet earned.
"""
import statistics
import time

import config
import metrics as M
from queries import pool_names

SORTS = ("fee_apr", "net_apr", "fees", "pnl", "wallets")
MIN_DEPOSIT = 10.0
MIN_HOLD_S = 60


def best_pools(con, days=7, min_positions=10, min_wallets=3, sort="fee_apr", limit=30, now=None):
    now = now or int(time.time())
    since = now - int(days * 86400)
    scores = {r[0]: r[1] for r in con.execute("SELECT addr, score FROM wallets WHERE score IS NOT NULL")}
    by_pool = {}
    for r in con.execute("""SELECT owner, pool, tl, tu, open_ts, close_ts, dep_usd, fee_usd, pnl_usd FROM positions
                            WHERE close_ts >= ? AND valued = 1 AND minted = 1 AND COALESCE(launch, 0) = 0
                            AND dep_usd >= ? AND open_ts IS NOT NULL""", (since, MIN_DEPOSIT)):
        by_pool.setdefault(r["pool"], []).append(dict(r))

    meta = {r["id"]: dict(r) for r in con.execute("SELECT id, c0, c1, fee FROM pools")}
    mcap = {r[0]: r[1] for r in con.execute("SELECT addr, mcap FROM tokens")}
    out = []
    for pool, ps in by_pool.items():
        owners = {}
        for p in ps:
            o = owners.setdefault(p["owner"], {"fees": 0.0, "pnl": 0.0, "n": 0})
            o["fees"] += p["fee_usd"] or 0.0
            o["pnl"] += p["pnl_usd"] or 0.0
            o["n"] += 1
        if len(ps) < min_positions or len(owners) < min_wallets:
            continue
        cap_days = sum(p["dep_usd"] * max(p["close_ts"] - p["open_ts"], MIN_HOLD_S) / 86400 for p in ps)
        fees = sum(p["fee_usd"] or 0.0 for p in ps)
        pnl = sum(p["pnl_usd"] or 0.0 for p in ps)
        decided = [p for p in ps if abs(p["pnl_usd"] or 0.0) > p["dep_usd"] * 0.0005]
        pl = meta.get(pool) or {}
        token = next((c for c in (pl.get("c0"), pl.get("c1")) if c and c not in config.STABLES | config.ETH_LIKE),
                     None)
        top = sorted(owners.items(), key=lambda kv: -kv[1]["pnl"])[:3]
        out.append({
            "pool": pool, "fee_tier": pl["fee"] / 1e4 if pl.get("fee") is not None else None,
            "positions": len(ps), "wallets": len(owners),
            "deposited": sum(p["dep_usd"] for p in ps), "fees": fees, "pnl": pnl,
            "fee_apr": fees / cap_days * 365 * 100, "net_apr": pnl / cap_days * 365 * 100,
            "fee_per_1k": fees / cap_days * 1000,
            "win": sum(p["pnl_usd"] > 0 for p in decided) / len(decided) * 100 if decided else None,
            "earning": sum((p["fee_usd"] or 0) > 0 for p in ps) / len(ps) * 100,
            "hold_h": statistics.median(max(p["close_ts"] - p["open_ts"], 0) for p in ps) / 3600,
            "range_pct": statistics.median(M.range_pct(p) for p in ps),
            "top_share": max(o["fees"] for o in owners.values()) / fees * 100 if fees > 0 else None,
            "mcap": mcap.get(token) if token else None,
            "top_wallets": [{"addr": a, "pnl": o["pnl"], "fees": o["fees"], "positions": o["n"],
                             "score": scores.get(a)} for a, o in top],
        })
    names = pool_names(con, {p["pool"] for p in out})
    for p in out:
        p["pair"] = names.get(p["pool"])
    key = sort if sort in SORTS else "fee_apr"
    out.sort(key=lambda p: -(p[key] or 0))
    return out[:limit]
