"""One wallet (or a few combined) dissected for copy-trading, Apex Liquidity
style: PnL, capital ROI, risk, profit quality, how it LPs, and a verdict.

Pure functions over the position dicts lpmath.py takes, plus dep, wd, fee,
now, cost, eth_pair and:
  pair (e.g. 'TOKEN/ETH'), owner, oor ('above' | 'below' = the last withdrawal
  came back all in one token, so it closed out of range; None = in range or
  unknown).
Money is USD.
"""
import calendar
import math
import statistics
import time

import lpmath as M

DAY = M.DAY

# ---- classifications --------------------------------------------------------
# Quality tiers, best first: (name, short tag, what it means)
TIERS = [
    ("ELITE", "Strong & profitable", "Consistent, shallow drawdowns and clearly profitable. Main-capital tier."),
    ("SOLID", "Copyable", "Good balance of profit and risk. Fine for a moderate allocation."),
    ("MIXED", "Caution", "Profitable, but with real drawdowns or uneven days. Size down."),
    ("WEAK", "Avoid", "A thin edge for the risk it takes. Avoid, or test with a small amount."),
    ("REJECT", "Not copyable", "Net loss, or its losing days outweigh its winning days. Not worth copying."),
]
TIER_TEXT = {t: x for t, _, x in TIERS}
# what a wallet needs for ELITE / SOLID (MIXED: profit factor 1.2+, WEAK: any profit)
ELITE = {"pf": 2.0, "dd": -20.0, "top2": 50.0, "closed": 15, "win": 55.0}
SOLID = {"pf": 1.5, "dd": -30.0, "top2": 65.0, "closed": 10, "win": 0.0}
MIXED_PF = 1.2
COPY_MIN_CLOSED = SOLID["closed"]

KINDS = [
    ("multiday", "Larger caps, wider ranges, longer holds. Lower risk, steadier medium profit — main-capital style."),
    ("hybrid", "A mix: some patient big-cap LPs, some small-cap snipes."),
    ("degen", "Smaller caps, tight ranges, fast exits. High risk, high upside."),
]
KIND_TEXT = dict(KINDS)

ARCHETYPES = [
    ("Spike Hunter", "Snipes volatility on small tokens: tight range, in and out within the hour. High risk, "
                     "high variance."),
    ("Liquid Scalper", "Fast in and out of liquid ($1M+) tokens with a tight range, catching short moves."),
    ("Micro Degen", "Sits in small tokens for a few hours. Big upside, big risk."),
    ("Intraday Swinger", "Rides intraday moves for a few hours on liquid pools with a moderate range."),
    ("Conviction Holder", "Holds a narrow band for days: a bet on calm prices. Precise, but hurts when price runs."),
    ("Patient Big-Cap LP", "Holds bigger tokens for days with wider ranges. Lower risk, steady fee farming."),
    ("Range Farmer", "Wide ranges held for days: staying in range and collecting fees beats precision."),
    ("Adaptive", "No single style dominates: it mixes token size, hold time and range."),
]
ARCHETYPE_TEXT = dict(ARCHETYPES)

# the distributions, in the order they're drawn
RANGE_BUCKETS = [(10, "Ultra Tight"), (20, "Super Tight"), (50, "Tight"), (65, "Mid"), (80, "Wide"),
                 (90, "Super Wide"), (math.inf, "Ultra Wide")]
RANGE_LEGEND = "Ultra Tight <10% · Super Tight 10–20% · Tight 20–49% · Mid 50–65% · Wide 65–80% · " \
               "Super Wide 80–90% · Ultra Wide >90%"
HOLD_BUCKETS = [(3600, "0 – 1h"), (6 * 3600, "1 – 6h"), (DAY, "6 – 24h"), (3 * DAY, "1d – 3d"), (math.inf, "≥ 3d")]
MCAP_BUCKETS = [(1e5, "< $100K"), (2.5e5, "$100K – $250K"), (5e5, "$250K – $500K"), (1e6, "$500K – $1M"),
                (3e6, "$1M – $3M"), (5e6, "$3M – $5M"), (1e7, "$5M – $10M"), (math.inf, "> $10M")]
SIDE_LABEL = {"bid": "ETH/USDG Only", "both": "Dual Sided", "ask": "Token Only"}

# recommended filter grid: what a copier can check before following a trade
FILTER_EDGES = [0, 1e5, 2.5e5, 5e5, 1e6, 3e6, 1e7, math.inf]
FILTER_SIDES = [(("bid", "both", "ask"), "any side"), (("bid",), "ETH/USDG only"), (("both",), "dual sided"),
                (("ask",), "token only"), (("bid", "both"), "no token-only")]
FILTER_QUOTES = [(None, "any pool"), (1, "ETH pools"), (0, "USDG pools")]
FILTER_MIN_TRADES = 5


def bucket(v, buckets):
    return next(name for top, name in buckets if v < top)


def distribution(values, buckets):
    """[(bucket name, share %)] in bucket order, from the values that are known."""
    vals = [v for v in values if v is not None]
    counts = {name: 0 for _, name in buckets}
    for v in vals:
        counts[bucket(v, buckets)] += 1
    n = len(vals) or 1
    return [(name, counts[name] / n * 100) for _, name in buckets]


def deployed(positions, start, end):
    """Capital sitting in positions over [start, end] (each one's deposit while
    it is open): (peak, time-weighted median while anything is open)."""
    edges = []
    for p in positions:
        a, b = max(p["open_ts"], start), min(p["close_ts"] or end, end)
        if b > a and p["dep"] > 0:
            edges += [(a, p["dep"]), (b, -p["dep"])]
    if not edges:
        return 0.0, 0.0
    edges.sort()
    level = peak = 0.0
    spans = []  # (capital, seconds)
    for (t, d), nxt in zip(edges, edges[1:] + [(edges[-1][0], 0)]):
        level += d
        peak = max(peak, level)
        if level > 1e-9 and nxt[0] > t:
            spans.append((level, nxt[0] - t))
    if not spans:
        return peak, 0.0
    spans.sort()
    half, acc = sum(s for _, s in spans) / 2, 0.0
    for lvl, s in spans:
        acc += s
        if acc >= half:
            return peak, lvl
    return peak, spans[-1][0]


def money_label(v):
    a = abs(v)
    return f"${a / 1e6:g}M" if a >= 1e6 else f"${a / 1e3:g}K" if a >= 1e3 else f"${a:g}"


def archetype(trades, now):
    """(name, share %) of the dominant cap × duration × range pattern, or Adaptive."""
    counts = {}
    for t in trades:
        hold = (t["close_ts"] or now) - t["open_ts"]
        big = (t["mcap"] or 0) >= 1e6
        rng = M.range_pct(t)
        if hold < 3600:
            k = "Liquid Scalper" if big else "Spike Hunter"
        elif hold < DAY:
            k = "Intraday Swinger" if big else "Micro Degen"
        elif rng < 20:
            k = "Conviction Holder"
        else:
            k = "Patient Big-Cap LP" if big else "Range Farmer"
        counts[k] = counts.get(k, 0) + 1
    if not counts:
        return "Adaptive", 0
    k, n = max(counts.items(), key=lambda kv: kv[1])
    share = n / len(trades) * 100
    return (k, round(share)) if share >= 45 else ("Adaptive", round(share))


def trader_type(trades, now, dd, multiday_s=M.MULTIDAY_S):
    """('multiday' | 'hybrid' | 'degen', share % of its trades that fit): a trade
    leans multiday with 2 of: a $1M+ token, held past the line, a 20%+ range."""
    if not trades:
        return "degen", 0
    patient = sum(1 for t in trades if ((t["mcap"] or 0) >= 1e6) + ((t["close_ts"] or now) - t["open_ts"] >= multiday_s)
                  + (M.range_pct(t) >= 20) >= 2) / len(trades)
    lean = patient - (0.1 if dd is not None and dd <= -40 else 0)  # deep drawdowns read degen
    kind = "multiday" if lean >= 0.65 else "degen" if lean <= 0.35 else "hybrid"
    return kind, round((patient if kind != "degen" else 1 - patient) * 100)


def flags(a):
    """Plain-word reads of the profit and loss structure: [(tone, title, text)]
    with tone 'good' | 'info' | 'bad'."""
    out = []
    top2 = a["concentration"]
    if top2 is not None and a["green_days"] >= 3:
        if top2 >= 60:
            out.append(("bad", "High Profit Concentration", f"{top2:.0f}% of the profit came from its 2 best days. "
                                                            "Performance leans on momentum."))
        elif top2 <= 35:
            out.append(("good", "Steady Profit", "Profit is spread over many days, not a couple of lucky ones."))
    if a["worst_day"] is not None and a["avg_green"]:
        r = -a["worst_day"] / a["avg_green"]
        if r <= 1:
            out.append(("good", "Controlled Loss", "Its worst day is smaller than a typical green day."))
        elif r <= 2:
            out.append(("info", "Moderate Tail Risk", f"Its worst day costs ≈{r:.1f} green days."))
        else:
            out.append(("bad", "High Tail Risk" if r > 4 else "Elevated Tail Risk",
                        f"1 bad day can erase ≈{r:.1f} days of profit."))
    if a["red_sum"] < 0:
        e = a["green_sum"] / -a["red_sum"]
        if e >= 2:
            out.append(("good", "Efficient Profit Structure", f"Green days made {e:.1f}× what red days lost."))
        elif e >= 1:
            out.append(("info", "Moderate Efficiency", "Green days only slightly outweigh red days."))
        else:
            out.append(("bad", "Inefficient", "Red days lost more than green days made."))
    elif a["green_sum"] > 0:
        out.append(("good", "No Red Days", "Every day with a close ended in profit."))
    if a["oor_rate"] is not None and a["oor_rate"] >= 30:
        out.append(("bad", "Often Caught Out Of Range", f"{a['oor_rate']:.0f}% of its closes ended out of range at a "
                                                         "loss: price ran through its ranges."))
    if a["follow"] == "✗":
        out.append(("bad", "Bot Pace", "Holds under 5 min or trades 100+ times a day: not followable by hand."))
    return out


def tier(a):
    """(tier, what keeps it from the next tier up)."""
    if a["net"] <= 0 or (a["pf"] is not None and a["pf"] < 1) or a["red_sum"] + a["green_sum"] <= 0:
        return "REJECT", []

    def misses(bar):
        why = []
        if a["pf"] is not None and a["pf"] < bar["pf"]:
            why.append(f"profit factor {a['pf']:.2f} (needs {bar['pf']:g})")
        if a["dd"] is not None and a["dd"] < bar["dd"]:
            why.append(f"drawdown {a['dd']:.0f}% (limit {bar['dd']:.0f}%)")
        if a["concentration"] is not None and a["green_days"] >= 3 and a["concentration"] > bar["top2"]:
            why.append(f"{a['concentration']:.0f}% of profit from 2 days (limit {bar['top2']:.0f}%)")
        if a["closed"] < bar["closed"]:
            why.append(f"{a['closed']} closed positions (needs {bar['closed']})")
        if a["win"] is not None and a["win"] < bar["win"]:
            why.append(f"win rate {a['win']:.0f}% (needs {bar['win']:.0f}%)")
        if a["follow"] == "✗":
            why.append("bot pace")
        return why
    if not misses(ELITE):
        return "ELITE", []
    if not misses(SOLID):
        return "SOLID", misses(ELITE)
    return ("MIXED" if a["pf"] is None or a["pf"] >= MIXED_PF else "WEAK"), misses(SOLID)


def filter_positions(positions, mcap_min=None, mcap_max=None, sides=None):
    """Positions whose token market cap at entry is in [mcap_min, mcap_max] and
    whose deposit side is one of sides (None = no limit)."""
    out = []
    for p in positions:
        m = p.get("mcap")
        if (mcap_min and (m is None or m < mcap_min)) or (mcap_max and (m is None or m > mcap_max)):
            continue
        if sides is not None and p.get("side") and p["side"] not in sides:
            continue
        out.append(p)
    return out


def recommend_filters(positions, pnl):
    """Grid search over entry market cap range × deposit side × ETH vs USDG pool:
    which filter would have kept the most PnL. Counted per trade.
    -> {'all': row, 'best': [rows that beat taking everything, best first]},
    row = {name, trades, pnl, win, roi, mcap_min, mcap_max, sides, quote}"""
    tr = [t for t in M.trades(positions) if t["close_ts"]]

    def row(name, ts, **f):
        res = [(sum(pnl(p) for p in t["legs"]), t["dep"]) for t in ts]
        w = sum(1 for x, d in res if x > M.FLAT * d)
        decided = w + sum(1 for x, d in res if x < -M.FLAT * d)
        dep = sum(d for _, d in res)
        return {"name": name, "trades": len(ts), "pnl": sum(x for x, _ in res),
                "win": w / decided * 100 if decided else None, "roi": sum(x for x, _ in res) / dep * 100 if dep else None,
                **f}

    caps = [(0, math.inf)] + [(lo, hi) for i, lo in enumerate(FILTER_EDGES[:-1]) for hi in FILTER_EDGES[i + 1:]
                              if (lo, hi) != (0, math.inf)]  # simplest first: it wins ties
    rows, seen = [], set()
    for lo, hi in caps:
        for sides, sname in FILTER_SIDES:
            for quote, qname in FILTER_QUOTES:
                ts = [t for t in tr if lo <= (t["mcap"] if t["mcap"] is not None else -1) < hi
                      and (t["side"] in sides or t["side"] is None)
                      and (quote is None or t["legs"][0].get("eth_pair") == quote)] \
                    if lo or hi != math.inf else \
                    [t for t in tr if (t["side"] in sides or t["side"] is None)
                     and (quote is None or t["legs"][0].get("eth_pair") == quote)]
                key = frozenset(id(t) for t in ts)
                if len(ts) < FILTER_MIN_TRADES or key in seen:
                    continue
                seen.add(key)  # the same trades as a simpler filter: keep the simpler one
                cap = "any mcap" if not lo and hi == math.inf else f"mcap {money_label(lo)}+" if hi == math.inf \
                    else f"mcap under {money_label(hi)}" if not lo else f"mcap {money_label(lo)}–{money_label(hi)}"
                rows.append(row(" · ".join(x for x in (cap, sname, qname) if not x.startswith("any")) or "everything",
                                ts, mcap_min=lo or None, mcap_max=None if hi == math.inf else hi,
                                sides=list(sides), quote=quote))
    everything = row("everything", tr, mcap_min=None, mcap_max=None, sides=["bid", "both", "ask"], quote=None)
    rows.sort(key=lambda r: -r["pnl"])
    return {"all": everything, "best": [r for r in rows if r["pnl"] > everything["pnl"] + 0.005][:6]}


def drawdown_stats(daily, capital, end_day=None):
    """Drawdowns of a realized PnL curve by day ([{day, pnl, cum}], days in
    order). -> {max_dd (% of capital), max_dd_usd, fall_days (peak to bottom),
    recover_days (bottom back to the peak, None = not yet), underwater_days
    (longest stretch below a previous high, counting one still open),
    current_dd, days_since_peak}"""
    out = {"max_dd": 0.0, "max_dd_usd": 0.0, "fall_days": 0, "recover_days": 0, "underwater_days": 0,
           "current_dd": 0.0, "days_since_peak": 0, "recovered": True}
    if not daily:
        return out
    cap = capital or 1.0
    peak, peak_day = 0.0, daily[0]["day"] - DAY  # flat before the first close
    worst = None  # (dd, peak_day, bottom_day)
    longest, start = 0, None
    for d in daily:
        if d["cum"] >= peak - 1e-9:
            if start is not None:
                longest = max(longest, round((d["day"] - start) / DAY))
                if worst and worst[1] == start and worst[3] is None:
                    worst[3] = d["day"]
                start = None
            peak, peak_day = d["cum"], d["day"]
        else:
            if start is None:
                start = peak_day
            dd = peak - d["cum"]
            if worst is None or dd > worst[0]:
                worst = [dd, peak_day, d["day"], None]
    last = daily[-1]["day"] if end_day is None else max(daily[-1]["day"], end_day)
    if start is not None:
        longest = max(longest, round((last - start) / DAY))
    if worst:
        out.update(max_dd=-worst[0] / cap * 100, max_dd_usd=-worst[0], fall_days=round((worst[2] - worst[1]) / DAY),
                   recover_days=None if worst[3] is None else round((worst[3] - worst[2]) / DAY),
                   recovered=worst[3] is not None)
    out.update(underwater_days=longest, current_dd=-(peak - daily[-1]["cum"]) / cap * 100,
               days_since_peak=round((last - peak_day) / DAY) if daily[-1]["cum"] < peak - 1e-9 else 0)
    return out


def loss_recovery(daily):
    """After each red day: how many days until the running total is back above
    where it was before that day. -> (average days, longest, red days not won back yet)"""
    waits, open_ = [], 0
    for i, d in enumerate(daily):
        if d["pnl"] >= 0:
            continue
        before = d["cum"] - d["pnl"]
        back = next((x["day"] for x in daily[i + 1:] if x["cum"] >= before - 1e-9), None)
        if back is None:
            open_ += 1
        else:
            waits.append(round((back - d["day"]) / DAY))
    return (statistics.fmean(waits) if waits else None), (max(waits) if waits else None), open_


def streak(values):
    """Longest run of values below zero."""
    best = cur = 0
    for v in values:
        cur = cur + 1 if v < 0 else 0
        best = max(best, cur)
    return best


def risk_stats(closed, vals, daily, capital, fees, net, now, end):
    """The Apex-style risk read of one wallet (see drawdown_stats, loss_recovery)."""
    r = drawdown_stats(daily, capital, int(end // DAY) * DAY)
    avg_rec, max_rec, unrec = loss_recovery(daily)
    day_pnl = [d["pnl"] for d in daily]
    sd = statistics.pstdev(day_pnl) if len(day_pnl) > 1 else 0.0
    last7 = sum(x for p, x in zip(closed, vals) if p["close_ts"] >= end - 7 * DAY)
    weeks = max(1.0, len(daily) / 7)
    r.update({
        "recover_avg": avg_rec, "recover_max": max_rec, "red_open": unrec,
        "loss_streak": streak(vals), "red_streak": streak(day_pnl),
        "best": max(vals) if vals else None, "worst": min(vals) if vals else None,
        "expectancy": statistics.fmean(vals) if vals else None,
        "daily_vol": sd / capital * 100 if capital else None,  # % of capital, one day
        "consistency": statistics.fmean(day_pnl) / sd if sd else None,  # mean ÷ spread of daily PnL
        "green_share": sum(1 for x in day_pnl if x > 0) / len(day_pnl) * 100 if day_pnl else None,
        "fee_share": fees / net * 100 if net > 0 else None,  # how much of the profit is fees
        "last7": last7, "momentum": last7 / (net / weeks) if net > 0 else None,  # 1 = its usual week
    })
    return r


def analyze(positions, now, start, end=None, real=False, multiday_s=M.MULTIDAY_S):
    """Everything the Analyze tab shows for positions closed in [start, end]
    (and, when the period runs to now, the ones still open), or None if there
    is nothing in it."""
    end = min(end or now, now)
    live = end >= now - 60
    pnl = (lambda p: p["pnl"] - (p.get("cost") or 0)) if real else (lambda p: p["pnl"])
    closed = sorted((p for p in positions if p["close_ts"] and start <= p["close_ts"] <= end),
                    key=lambda p: p["close_ts"])
    open_ = [p for p in positions if not p["close_ts"] and p["open_ts"] <= end] if live else []
    scope = closed + open_
    if not scope:
        return None

    vals = [pnl(p) for p in closed]
    wins = [x for p, x in zip(closed, vals) if x > M.FLAT * p["dep"]]
    losses = [x for p, x in zip(closed, vals) if x < -M.FLAT * p["dep"]]
    profit, loss = sum(wins), -sum(losses)
    net = sum(vals)
    flt = sum(pnl(p) for p in open_)
    decided = len(wins) + len(losses)

    first = min(p["open_ts"] for p in scope)
    t0 = max(start, first)
    active = max(3600.0, end - t0)
    peak, median_cap = deployed(scope, t0, end)
    capital = median_cap or peak or 0.0
    roi = net / capital * 100 if capital else None

    days = {}
    for p, x in zip(closed, vals):
        d = int(p["close_ts"] // DAY)
        days[d] = days.get(d, 0.0) + x
    green = [v for v in days.values() if v > 0]
    red = [v for v in days.values() if v < 0]
    top2 = sum(sorted(green, reverse=True)[:2])
    daily = []
    cum = 0.0
    if days:
        for d in range(min(days), max(days) + 1):
            cum += days.get(d, 0.0)
            daily.append({"day": d * DAY, "pnl": days.get(d, 0.0), "cum": cum})

    cum = top = dd = 0.0
    for x in vals:
        cum += x
        top = max(top, cum)
        dd = max(dd, top - cum)

    oor_known = [(p, x) for p, x in zip(closed, vals) if "oor" in p]
    oor_loss = [x for p, x in oor_known if p["oor"] and x < 0]
    avg_green = statistics.fmean(green) if green else None

    tr = M.trades(scope)
    t_closed = [t for t in tr if t["close_ts"]]
    holds = [t["close_ts"] - t["open_ts"] for t in t_closed]
    res = [(sum(pnl(p) for p in t["legs"]), t["dep"]) for t in t_closed]
    t_wins = sum(1 for x, d in res if x > M.FLAT * d)
    t_dec = t_wins + sum(1 for x, d in res if x < -M.FLAT * d)
    hold = M._median(holds)
    rng = M._median([M.range_pct(t) for t in tr])
    mcap = M._median([t["mcap"] for t in tr])
    per_day = len(tr) / max(1.0, active / DAY)
    follow = "✗" if (hold is not None and hold < M.BOT_HOLD_S) or per_day > M.BOT_PER_DAY else \
        "✓" if hold is not None and hold >= 1800 and per_day <= 30 else "?"
    sides = [p.get("side") for p in scope if p.get("side")]
    play, play_share = archetype(tr, now)
    dd_pct = -dd / capital * 100 if capital else None
    kind, kind_fit = trader_type(tr, now, dd_pct, multiday_s)

    pools = {}
    still_open = {id(p) for p in open_}
    for p in scope:
        g = pools.setdefault(p["pool"], {"pool": p["pool"], "pair": p.get("pair") or p["pool"][:10], "positions": 0,
                                         "closed": 0, "wins": 0, "pnl": 0.0, "fees": 0.0, "dep": 0.0, "open": 0})
        g["positions"] += 1
        g["fees"] += p["fee"] or 0.0
        g["dep"] += p["dep"] or 0.0
        if id(p) in still_open:
            g["open"] += 1
        else:
            x = pnl(p)
            g["closed"] += 1
            g["pnl"] += x
            g["wins"] += x > M.FLAT * p["dep"]

    a = {
        "start": t0, "end": end, "live": live, "first_ts": first, "active_days": active / DAY,
        "positions": len(scope), "closed": len(closed), "open": len(open_), "wins": len(wins),
        "losses": len(losses), "flat": len(closed) - decided,
        "net": net, "float": flt, "total": net + flt, "profit": profit, "loss": loss,
        "per_position": net / len(closed) if closed else None,
        "pf": min(M.PF_CAP, profit / loss) if loss else (M.PF_CAP if profit else None),
        "win": len(wins) / decided * 100 if decided else None,
        "trade_win": t_wins / t_dec * 100 if t_dec else None, "trades": len(tr), "closed_trades": len(t_closed),
        "capital": capital, "peak": peak, "roi": roi,
        "roi_30d": roi * 30 * DAY / max(active, DAY) if roi is not None else None,
        "open_now": sum(p["now"] or 0.0 for p in open_), "fees": sum(p["fee"] or 0.0 for p in scope),
        "size": M._median([t["dep"] for t in tr], 0.0), "largest": max((p["dep"] for p in scope), default=0.0),
        "dd": dd_pct, "dd_usd": -dd, "green_sum": sum(green), "red_sum": sum(red),
        "worst_day": min(red) if red else None, "avg_green": avg_green,
        "avg_red": statistics.fmean(red) if red else None, "green_days": len(green), "red_days": len(red),
        "concentration": top2 / sum(green) * 100 if green else None,
        "oor_rate": len(oor_loss) / len(oor_known) * 100 if oor_known else None,
        "oor_n": len(oor_loss), "oor_of": len(oor_known),
        "oor_worst": min(oor_loss) if oor_loss else None,
        "oor_median": statistics.median(oor_loss) if oor_loss else None,
        "kind": kind, "kind_fit": kind_fit, "hold": hold, "range": rng, "mcap": mcap, "per_day": per_day,
        "follow": follow, "play": play, "play_text": ARCHETYPE_TEXT[play], "play_share": play_share,
        "style": M.style(tr)[0],
        "sides": [(SIDE_LABEL[s], sides.count(s) / len(sides) * 100) for s in ("ask", "both", "bid")] if sides else [],
        "token_only": sides.count("ask") / len(sides) * 100 if sides else None, "token_only_n": sides.count("ask"),
        "range_dist": [(n, v) for n, v in distribution([M.range_pct(t) for t in tr], RANGE_BUCKETS) if v > 0],
        "hold_dist": distribution(holds, HOLD_BUCKETS),
        "mcap_dist": distribution([t["mcap"] for t in tr], MCAP_BUCKETS),
        "mcap_known": sum(1 for t in tr if t["mcap"] is not None), "daily": daily,
        "pools": sorted(pools.values(), key=lambda g: -g["pnl"]),
        "eth_share": sum(p["dep"] for p in scope if p.get("eth_pair")) / (sum(p["dep"] for p in scope) or 1) * 100,
    }
    a["risk"] = risk_stats(closed, vals, daily, capital, a["fees"], net, now, end)
    a["flags"] = flags(a)
    a["verdict"], a["why"] = tier(a)
    return a


def sizing(a, mine):
    """Scale its trades to your capital (USD): {ratio, per_trade, max_position, peak}."""
    base = a["capital"] or a["peak"]
    if not base or not mine:
        return None
    r = mine / base
    return {"ratio": r, "per_trade": a["size"] * r, "max_position": a["largest"] * r, "peak": a["peak"] * r}


def period(name, now):
    """(start, end) of a named period; end None = up to now."""
    t = time.gmtime(now)
    month0 = calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0))
    if name == "This month":
        return month0, None
    if name == "Last month":
        y, m = (t.tm_year, t.tm_mon - 1) if t.tm_mon > 1 else (t.tm_year - 1, 12)
        return calendar.timegm((y, m, 1, 0, 0, 0)), month0 - 1
    if name == "All history":
        return 0, None
    n, unit = name.split()
    return now - float(n) * (3600 if unit.startswith("hour") else DAY), None


PERIODS = ("24 hours", "7 days", "14 days", "30 days", "This month", "Last month", "All history")


# ---- splitting your capital over a batch of wallets ------------------------
# Every followed wallet gets a share of the money that goes in (DEPLOY_PCT of
# your capital); the profile decides how the shares lean. cap: the most one
# wallet may get (raised to 1/n when there are few wallets, so it all goes in);
# floor: the least any wallet gets.
PROFILES = {
    "Safety": {"cap": 0.25, "floor": 0.04,
               "text": "Risk parity: the steadier a wallet's daily PnL, the more it gets; better tiers and multiday "
                       "players get more, deep drawdowns and lucky-day profit less."},
    "Balanced": {"cap": 0.35, "floor": 0.03,
                 "text": "ROI × profit factor, damped by how much the wallet swings day to day."},
    "Aggressive": {"cap": 0.50, "floor": 0.02,
                   "text": "Chases ROI: the best performers get far more, the rest a small floor. Biggest upside, "
                           "biggest drawdowns."},
}
TIER_WEIGHT = {
    "Safety": {"ELITE": 1.3, "SOLID": 1.0, "MIXED": 0.55, "WEAK": 0.3, "REJECT": 0.1},
    "Balanced": {"ELITE": 1.2, "SOLID": 1.0, "MIXED": 0.7, "WEAK": 0.45, "REJECT": 0.15},
    "Aggressive": {"ELITE": 1.0, "SOLID": 1.0, "MIXED": 0.9, "WEAK": 0.7, "REJECT": 0.25},
}
DEPLOY_PCT = 95.0
MIN_TICKET = 50.0  # USD: less than this in a wallet isn't worth mirroring
SMALL_TRADE = 20.0  # USD per trade below which copying gets impractical


def _weight(profile, a):
    m = TIER_WEIGHT[profile][a["verdict"]]
    r = max(0.0, a["roi_30d"] or 0.0)
    pf = min(a["pf"] or 0.0, 5.0)
    vol = max(0.25, (a.get("risk") or {}).get("daily_vol") or abs(a["dd"] or 0.0) / 3 or 1.0)
    if profile == "Safety":
        conc = a["concentration"] if a["concentration"] is not None else 50.0
        dd = abs(a["dd"] or 0.0)
        return (m * (min(pf, 3) / 3 + 0.2) / vol * (1 - min(conc, 100) / 250) / (1 + dd / 50)
                * {"multiday": 1.2, "hybrid": 1.0}.get(a["kind"], 0.8))
    if profile == "Balanced":
        return m * (r + 5) ** 0.7 * (min(pf, 4) / 4 + 0.2) / math.sqrt(vol)
    return m * (r + 5) ** 1.5


def _shares(weights, cap, floor):
    """Shares summing to 1, each between floor and cap, otherwise in proportion to
    the weights. With few wallets the cap rises (1.6 / n) so everything still goes
    in without forcing an even split."""
    w = {k: v for k, v in weights.items() if v > 0}
    if not w:
        return {}
    n = len(w)
    cap = min(1.0, max(cap, 1.6 / n, 1 / n))
    floor = min(floor, 1 / n)
    fixed = {}
    while True:
        free = {k: v for k, v in w.items() if k not in fixed}
        if not free:
            return fixed
        left = 1.0 - sum(fixed.values())
        tot = sum(free.values())
        props = {k: v / tot * left for k, v in free.items()}
        over = {k for k, v in props.items() if v > cap + 1e-12}
        under = {k for k, v in props.items() if v < floor - 1e-12}
        if not over and not under:
            fixed.update(props)
            return fixed
        for k in over or under:  # settle one side at a time, then share the rest again
            fixed[k] = cap if over else floor


def allocate(analyses, capital, profile, deploy_pct=DEPLOY_PCT, include_reject=False):
    """Split deploy_pct % of capital (USD) over every followed wallet {addr: analysis}
    (REJECT ones only when include_reject), leaning the profile's way.
    -> {rows: [{addr, share, usd, per_trade, max_position, roi_30d, dd, verdict, small}], deployed, reserve,
        exp_30d, dd_usd, skipped: [addr], dropped: [addr], text, sim}"""
    p = PROFILES[profile]
    weights = {k: _weight(profile, a) for k, a in analyses.items()
               if a and (include_reject or a["verdict"] != "REJECT")}
    deploy = capital * min(100.0, max(0.0, deploy_pct)) / 100
    shares = _shares(weights, p["cap"], p["floor"])
    dropped = []
    # a ticket too small to mirror: its money goes to the others
    while len(shares) > 1 and min(shares.values()) * deploy < MIN_TICKET:
        k = min(shares, key=shares.get)
        dropped.append(k)
        del weights[k]
        shares = _shares(weights, p["cap"], p["floor"])
    if shares and min(shares.values()) * deploy < MIN_TICKET:
        dropped += list(shares)
        shares = {}
    rows = []
    for k, sh in sorted(shares.items(), key=lambda kv: -kv[1]):
        a = analyses[k]
        amt = sh * deploy
        sz = sizing(a, amt) or {"per_trade": 0.0, "max_position": 0.0}
        rows.append({"addr": k, "share": amt / capital * 100 if capital else 0.0, "usd": amt,
                     "per_trade": sz["per_trade"], "max_position": sz["max_position"], "roi_30d": a["roi_30d"],
                     "dd": a["dd"], "verdict": a["verdict"], "small": sz["per_trade"] < SMALL_TRADE})
    deployed = sum(r["usd"] for r in rows)
    sim = simulate(analyses, rows)
    return {"rows": rows, "deployed": deployed, "reserve": capital - deployed,
            "exp_30d": sum(r["usd"] * (r["roi_30d"] or 0) / 100 for r in rows),
            "dd_usd": sum(r["usd"] * (r["dd"] or 0) / 100 for r in rows),
            "skipped": [k for k, a in analyses.items() if a and k not in weights and k not in dropped],
            "dropped": dropped, "text": p["text"], "sim": sim}


def _corr(x, y):
    if len(x) < 3:
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx and sy else None


def simulate(analyses, rows):
    """Your copy of the batch, replayed: each wallet's daily realized PnL scaled by
    your share ÷ its median deployed capital, added up. -> {daily, total, roi, dd
    stats (as drawdown_stats, % of what went in), worst_day, best_day, green_share,
    corr (average pairwise correlation of the wallets' daily PnL), pairs}"""
    deployed = sum(r["usd"] for r in rows)
    series = {}
    for r in rows:
        a = analyses[r["addr"]]
        k = r["usd"] / a["capital"] if a["capital"] else 0.0
        series[r["addr"]] = {d["day"]: d["pnl"] * k for d in a["daily"]}
    days = sorted({d for s in series.values() for d in s})
    if not days:
        return None
    days = list(range(days[0], days[-1] + DAY, DAY))
    daily, cum = [], 0.0
    for d in days:
        v = sum(s.get(d, 0.0) for s in series.values())
        cum += v
        daily.append({"day": d, "pnl": v, "cum": cum})
    keys = list(series)
    pairs = []
    for i, x in enumerate(keys):
        for y in keys[i + 1:]:
            c = _corr([series[x].get(d, 0.0) for d in days], [series[y].get(d, 0.0) for d in days])
            if c is not None:
                pairs.append((x, y, c))
    vals = [d["pnl"] for d in daily]
    dd = drawdown_stats(daily, deployed)
    avg_rec, max_rec, unrec = loss_recovery(daily)
    return {"daily": daily, "total": cum, "roi": cum / deployed * 100 if deployed else None,
            "roi_30d": cum / deployed * 100 * 30 / max(1, len(days)) if deployed else None,
            **dd, "recover_avg": avg_rec, "recover_max": max_rec, "red_open": unrec,
            "worst_day": min(vals), "best_day": max(vals),
            "green_share": sum(1 for v in vals if v > 0) / len(vals) * 100,
            "corr": statistics.fmean(c for _, _, c in pairs) if pairs else None, "pairs": pairs,
            "series": {k: [{"day": d, "pnl": series[k].get(d, 0.0)} for d in days] for k in keys}}


def overlaps(analyses, min_share=0.5):
    """Pairs of wallets that LP mostly the same pools (Jaccard ≥ min_share): copying
    both is less diversified than it looks. -> [(a, b, share %)]"""
    pools = {k: {g["pool"] for g in a["pools"]} for k, a in analyses.items() if a}
    keys = list(pools)
    out = []
    for i, x in enumerate(keys):
        for y in keys[i + 1:]:
            u = pools[x] | pools[y]
            j = len(pools[x] & pools[y]) / len(u) if u else 0
            if j >= min_share:
                out.append((x, y, j * 100))
    return out
