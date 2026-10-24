"""LP trade helpers (trades, player class, range width, style), copied from
RH Wallet Finder's metrics so this app stands alone.

A position dict has: pool, open_block, open_ts, close_ts (None = still open),
dep, pnl, side ('bid' = only USDG/ETH went in, 'ask' = only the token, 'both'),
tl, tu, mcap. Positions opened together (same pool, same block) are one trade.
"""
import math
import statistics

DAY = 86400
FULL_RANGE_PCT = 1e6  # anything wider reads as "full range"
FLAT = 0.0005  # a trade within ±0.05% of its deposit did nothing (a ladder the price never reached)
PF_CAP = 99.0
MULTIDAY_S = 2 * 3600  # median hold from here up = Multiday player, below = Degen player
KINDS = ("multiday", "degen")
KIND_NAME = {"multiday": "Multiday", "degen": "Degen"}
BOT_HOLD_S = 300
BOT_PER_DAY = 100


def _median(xs, default=None):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else default


def range_pct(p):
    return min(FULL_RANGE_PCT, (math.pow(1.0001, min(p["tu"] - p["tl"], 200_000)) - 1) * 100)


def trades(positions):
    """Positions opened in one go (same pool, same block) -> trades, oldest first.
    Without an open_block a position is its own trade."""
    groups = {}
    for p in positions:
        blk = p.get("open_block")
        groups.setdefault((p.get("pool"), blk) if blk is not None else id(p), []).append(p)
    out = []
    for legs in groups.values():
        done = all(p["close_ts"] for p in legs)
        sides = {p.get("side") for p in legs}
        out.append({"legs": legs, "open_ts": min(p["open_ts"] for p in legs),
                    "close_ts": max(p["close_ts"] for p in legs) if done else None,
                    "dep": sum(p.get("dep") or 0.0 for p in legs), "tl": min(p.get("tl", 0) for p in legs),
                    "tu": max(p.get("tu", 0) for p in legs), "side": sides.pop() if len(sides) == 1 else "both",
                    "mcap": legs[0].get("mcap")})
    return sorted(out, key=lambda t: t["open_ts"])


def player_kind(holds, open_ages, multiday_s=MULTIDAY_S):
    """('multiday' | 'degen', fit 0..100) from closed trades' hold times; open
    trades count once they are already older than the line."""
    hs = list(holds) + [a for a in open_ages if a >= multiday_s]
    if not hs:
        return "degen", 0
    kind = "multiday" if statistics.median(hs) >= multiday_s else "degen"
    fit = sum(1 for h in hs if (h >= multiday_s) == (kind == "multiday")) / len(hs)
    return kind, round(fit * 100)


# ---- style -----------------------------------------------------------------
SIDE_NAME = {"bid": "bid", "ask": "ask", "both": "2-sided"}


def style(trade_list):
    """('Ladder ×5 · bid', 86): how its trades are built, and the share that fit.
    bid = only USDG/ETH went in (buys the token on the way down), ask = only the
    token (sells it on the way up), 2-sided = a range around the price."""
    if not trade_list:
        return None, 0
    counts = {}
    for t in trade_list:
        k = ("ladder" if len(t["legs"]) > 1 else "single", t["side"] or "both")
        counts[k] = counts.get(k, 0) + 1
    (shape, side), n = max(counts.items(), key=lambda kv: kv[1])
    if shape == "ladder":
        legs = _median([len(t["legs"]) for t in trade_list if len(t["legs"]) > 1])
        name = f"Ladder ×{legs:.0f}"
    else:
        name = "Single range"
    return f"{name} · {SIDE_NAME.get(side, side)}", round(n / len(trade_list) * 100)
