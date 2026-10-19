"""Wallet metrics, player class, scores, styles and similarity. Pure functions
over plain dicts so they can be tested without a chain or a database.

A position dict has: pool, open_block, open_ts, close_ts (None = still open),
dep, wd, fee, now, pnl, cost, eth_pair, single, side ('bid' = only USDG/ETH
went in, 'ask' = only the token, 'both'), tl, tu, mcap (token market cap when
it opened), sell_fee.

Positions opened together (same pool, same block: one transaction) are one
trade: a 5-range ladder is one decision, so win rate, size, hold time and pace
are counted per trade. PnL, fees and the equity curve stay per position.
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


def prescreen(positions, now, multiday_s=MULTIDAY_S):
    """Class and bot flag from open/close times alone (no prices needed), so
    scoring can price people before bots. -> {kind, hold, per_day, bot}"""
    tr = trades(positions)
    holds = [t["close_ts"] - t["open_ts"] for t in tr if t["close_ts"]]
    first = min((t["open_ts"] for t in tr), default=now)
    per_day = len(tr) / max(1.0, (now - first) / DAY)
    hold = _median(holds)
    kind, _ = player_kind(holds, [now - t["open_ts"] for t in tr if not t["close_ts"]], multiday_s)
    return {"kind": kind, "hold": hold, "per_day": per_day,
            "bot": (hold is not None and hold < BOT_HOLD_S) or per_day > BOT_PER_DAY}


def wallet_metrics(positions, now, window_days, data_from, real=False, multiday_s=MULTIDAY_S):
    """Everything the table shows for one wallet, or None without closed positions."""
    w0 = now - window_days * DAY
    closed = sorted((p for p in positions if p["close_ts"] and p["close_ts"] >= w0),
                    key=lambda p: p["close_ts"])
    open_ = [p for p in positions if not p["close_ts"]]
    if not closed:
        return None
    pnl = (lambda p: p["pnl"] - p["cost"]) if real else (lambda p: p["pnl"])
    scope = closed + open_
    tr = trades(scope)
    t_closed = [t for t in tr if t["close_ts"]]
    t_open = [t for t in tr if not t["close_ts"]]

    net = sum(pnl(p) for p in closed)
    flt = sum(pnl(p) for p in open_)
    fees = sum(p["fee"] for p in scope)
    size = _median([t["dep"] for t in tr], 0.0)

    # capital actually at work: deposits weighted by time held, over the part of
    # the window the wallet has been active (a 3-day-old wallet isn't idle for 27)
    span_start = max(w0, data_from or w0)
    first = min(t["open_ts"] for t in tr)
    active = max(3600, now - max(first, span_start))
    start = now - active
    held = 0.0
    for p in scope:
        a, b = max(p["open_ts"], start), (p["close_ts"] or now)
        if b > a:
            held += p["dep"] * (b - a)
    busy = 0.0
    for t in tr:
        a, b = max(t["open_ts"], start), (t["close_ts"] or now)
        busy += max(0, b - a)
    capital = max(held / active, size, 1e-9)
    concur = busy / active

    roi = net / capital * 100 * (30 * DAY / max(active, DAY))  # under a day of data: no wild extrapolation

    # per trade: a ladder that never got touched is flat, neither a win nor a loss
    res = [(sum(pnl(p) for p in t["legs"]), t["dep"]) for t in t_closed]
    wins = [x for x, dep in res if x > FLAT * dep]
    losses = [x for x, dep in res if x < -FLAT * dep]
    decided = len(wins) + len(losses)
    win = len(wins) / decided * 100 if decided else None
    pf = min(PF_CAP, sum(wins) / -sum(losses)) if losses else (PF_CAP if wins else None)

    days = {}
    for p in closed:
        d = int(p["close_ts"] // DAY)
        days[d] = days.get(d, 0.0) + pnl(p)
    daily = sum(1 for v in days.values() if v > 0) / len(days) * 100

    # drawdown of the realised equity curve, and how long the worst one took to heal
    cum = peak = max_dd = 0.0
    peak_ts = closed[0]["open_ts"]
    worst = None  # [started at (the peak's time), healed at or None]
    for p in closed:
        cum += pnl(p)
        if cum >= peak:
            if worst and worst[0] == peak_ts and worst[1] is None:
                worst[1] = p["close_ts"]
            peak, peak_ts = cum, p["close_ts"]
        elif peak - cum > max_dd:
            max_dd = peak - cum
            if not worst or worst[0] != peak_ts:
                worst = [peak_ts, None]
    unrecovered = bool(worst) and worst[1] is None
    recover = 0.0 if not worst else (now if unrecovered else worst[1]) - worst[0]

    holds = [t["close_ts"] - t["open_ts"] for t in t_closed] or [p["close_ts"] - p["open_ts"] for p in closed]
    kind, kind_fit = player_kind(holds, [now - t["open_ts"] for t in t_open], multiday_s)
    dep_all = sum(p["dep"] for p in scope) or 1e-9
    eth_share = sum(p["dep"] for p in scope if p["eth_pair"]) / dep_all * 100
    single = sum(1 for p in scope if p["single"]) / len(scope) * 100
    legs = [len(t["legs"]) for t in tr]

    m = {
        "net": net, "float": flt, "total": net + flt, "roi": roi, "win": win, "pf": pf, "daily": daily,
        "flat": (len(t_closed) - decided) / len(t_closed) * 100 if t_closed else 0.0,
        "dd": -max_dd / capital * 100, "recover": recover, "recover_open": unrecovered,
        "size": size, "eth": eth_share, "mcap": _median([t["mcap"] for t in tr]),
        "duration": _median(holds), "range": _median([range_pct(t) for t in tr]),
        "fees": fees, "pos": len(scope), "closed": len(closed), "open": len(open_),
        "trades": len(tr), "closed_trades": len(t_closed), "legs": _median(legs),
        "ladder": sum(1 for n in legs if n > 1) / len(tr) * 100,
        "bid": sum(1 for t in tr if t["side"] == "bid") / len(tr) * 100,
        "ask": sum(1 for t in tr if t["side"] == "ask") / len(tr) * 100,
        "tier": _median([p.get("sell_fee") for p in scope]),
        "open_now": sum(p["now"] for p in open_), "concur": concur, "capital": capital,
        "single": single, "per_day": len(tr) / max(1.0, active / DAY), "span_days": active / DAY,
        "kind": kind, "kind_fit": kind_fit,
    }
    m["style"], m["style_conf"] = style(tr)
    return m


# ---- score -----------------------------------------------------------------
# Each class is ranked against itself, on what matters for how it plays.
# (metric, weight, higher is better)
WEIGHTS = {
    # in and out within hours: realized PnL, and how reliably the trades pay
    "degen": [("net", 0.25, True), ("roi", 0.15, True), ("pf", 0.15, True), ("win", 0.10, True),
              ("daily", 0.10, True), ("dd", 0.10, True), ("fees", 0.05, True), ("closed_trades", 0.10, True)],
    # positions held for hours to days: what is still open counts, and a smooth ride
    "multiday": [("total", 0.25, True), ("roi", 0.15, True), ("daily", 0.15, True), ("dd", 0.15, True),
                 ("win", 0.10, True), ("fees", 0.05, True), ("recover", 0.05, False), ("closed_trades", 0.10, True)],
}
VERDICTS = [(70, "Elite"), (60, "Strong"), (50, "Decent"), (40, "Weak"), (0, "Avoid")]


def _pct_ranks(values, higher_better):
    """value -> percentile 0..1 among the list (ties share a rank)."""
    clean = sorted(v for v in values if v is not None)
    n = len(clean)
    out = []
    for v in values:
        if v is None or n < 2:
            out.append(0.5)
            continue
        lo = _bisect(clean, v, False)
        hi = _bisect(clean, v, True)
        r = ((lo + hi - 1) / 2) / (n - 1)
        out.append(r if higher_better else 1 - r)
    return out


def _bisect(a, x, right):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x or (right and a[mid] == x):
            lo = mid + 1
        else:
            hi = mid
    return lo


def score_all(metrics_list, kind="degen"):
    """Scores 0..100 for a list of metric dicts of one class (same order)."""
    weights = WEIGHTS[kind]
    ranks = {k: _pct_ranks([m.get(k) for m in metrics_list], hb) for k, _, hb in weights}
    pnl_key = "total" if kind == "multiday" else "net"
    scores = []
    for i, m in enumerate(metrics_list):
        raw = sum(w * ranks[k][i] for k, w, _ in weights) * 100
        n = m.get("closed_trades", m["closed"])
        s = 50 + (raw - 50) * n / (n + 4)  # few trades: pulled toward 50
        if m.get(pnl_key, m["net"]) <= 0:
            s = min(s, 45)
        scores.append(round(s, 1))
    return scores


def verdict(score):
    return next(v for t, v in VERDICTS if score >= t)


def copyable(m):
    """✓ a person can follow it, ✗ bot pace, ? in between."""
    d = m["duration"] or 0
    if d < BOT_HOLD_S or m["per_day"] > BOT_PER_DAY:
        return "✗"
    if d >= 1800 and m["per_day"] <= 30:
        return "✓"
    return "?"


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


# ---- similarity ------------------------------------------------------------
FEATURES = [
    ("duration", lambda v: math.log1p(max(0, v or 0) / 60)),
    ("range", lambda v: math.log1p(max(0, v or 0))),
    ("legs", lambda v: math.log1p(max(0, (v or 1) - 1))),
    ("bid", lambda v: (v or 0) / 100),
    ("ask", lambda v: (v or 0) / 100),
    ("eth", lambda v: (v or 0) / 100),
    ("mcap", lambda v: math.log10(max(1, v or 1))),
    ("size", lambda v: math.log10(max(1, v or 1))),
    ("per_day", lambda v: math.log1p(max(0, v or 0))),
    ("concur", lambda v: math.log1p(max(0, v or 0))),
    ("tier", lambda v: (v or 0) * 100),
    ("win", lambda v: (v or 0) / 100),
]


def feature_matrix(metrics_list):
    raw = [[f(m.get(k)) for k, f in FEATURES] for m in metrics_list]
    if not raw:
        return []
    cols = list(zip(*raw))
    mu = [statistics.fmean(c) for c in cols]
    sd = [statistics.pstdev(c) or 1.0 for c in cols]
    return [[(x - mu[j]) / sd[j] for j, x in enumerate(r)] for r in raw]


def similarity(a, b):
    """Cosine similarity mapped to 0..100."""
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    cos = sum(x * y for x, y in zip(a, b)) / (na * nb)
    return round((cos + 1) / 2 * 100)
