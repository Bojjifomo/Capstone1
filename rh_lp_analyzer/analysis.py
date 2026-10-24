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
# Each profile: which wallets it takes, how it weighs them, how much stays in
# cash, and the most one wallet may get (share of what is deployed).
PROFILES = {
    "Safety": {"reserve": 0.30, "cap": 0.25, "top": None,
               "text": "Only ELITE and SOLID wallets: profitable, shallow drawdowns, enough history, followable. "
                       "Weighted toward smooth returns (multiday players first), 30% kept in cash."},
    "Balanced": {"reserve": 0.15, "cap": 0.35, "top": None,
                 "text": "ELITE, SOLID and MIXED wallets that aren't bots, weighted by ROI × profit factor and damped "
                         "by drawdown. 15% kept in cash."},
    "Aggressive": {"reserve": 0.05, "cap": 0.50, "top": 3,
                   "text": "The 3 highest-ROI wallets that aren't rejected, weighted hard toward the best. Only 5% in "
                           "cash: bigger upside, bigger drawdowns."},
}
MIN_TICKET = 50.0  # USD: less than this in a wallet isn't worth mirroring
SMALL_TRADE = 20.0  # USD per trade below which copying gets impractical


def _weight(profile, a):
    r = max(0.0, a["roi_30d"] or 0.0)
    dd = abs(a["dd"] or 0.0)
    pf = min(a["pf"] or 0.0, 5.0)
    if a["net"] <= 0 or r <= 0:
        return 0.0
    if profile == "Safety":
        ok = a["verdict"] in ("ELITE", "SOLID")
        conc = a["concentration"] if a["concentration"] is not None else 50.0
        return (math.sqrt(r) * min(pf, 3) / 3 / (1 + dd / 10) * (1 - min(conc, 100) / 200)
                * {"multiday": 1.2, "hybrid": 1.0}.get(a["kind"], 0.8)) if ok else 0.0
    if profile == "Balanced":
        ok = a["verdict"] in ("ELITE", "SOLID", "MIXED") and a["follow"] != "✗"
        return r * min(pf, 4) / 4 / (1 + dd / 25) if ok else 0.0
    return r ** 1.5 if a["verdict"] != "REJECT" else 0.0  # Aggressive: the best ones far ahead


def _capped(weights, cap):
    """Shares summing to <= 1 with none above cap (what can't be placed is left over)."""
    w = {k: v for k, v in weights.items() if v > 0}
    out, left = {}, 1.0
    while w:
        tot = sum(w.values())
        over = {k for k, v in w.items() if v / tot * left > cap}
        if not over:
            out.update({k: v / tot * left for k, v in w.items()})
            break
        for k in over:
            out[k] = cap
            del w[k]
        left = 1.0 - sum(out.values())
        if left <= 1e-9:
            break
    return out


def allocate(analyses, capital, profile):
    """Split capital (USD) over wallets {addr: analysis} for one profile.
    -> {rows: [{addr, share, usd, per_trade, max_position, roi_30d, dd, small}], reserve, deployed,
        exp_30d, dd_usd, skipped: [addr], text}"""
    p = PROFILES[profile]
    weights = {k: _weight(profile, a) for k, a in analyses.items() if a}
    if p["top"]:
        keep = sorted((k for k, v in weights.items() if v > 0), key=lambda k: -weights[k])[:p["top"]]
        weights = {k: weights[k] for k in keep}
    deploy = capital * (1 - p["reserve"])
    shares = _capped(weights, p["cap"])
    # drop tickets too small to mirror, and share their money among the rest
    while shares and min(shares.values()) * deploy < MIN_TICKET:
        del weights[min(shares, key=shares.get)]
        shares = _capped(weights, p["cap"])
    rows = []
    for k, s in sorted(shares.items(), key=lambda kv: -kv[1]):
        a = analyses[k]
        amt = s * deploy
        sz = sizing(a, amt) or {"per_trade": 0.0, "max_position": 0.0}
        rows.append({"addr": k, "share": amt / capital * 100, "usd": amt, "per_trade": sz["per_trade"],
                     "max_position": sz["max_position"], "roi_30d": a["roi_30d"], "dd": a["dd"],
                     "verdict": a["verdict"], "small": sz["per_trade"] < SMALL_TRADE})
    deployed = sum(r["usd"] for r in rows)
    return {"rows": rows, "deployed": deployed, "reserve": capital - deployed,
            "exp_30d": sum(r["usd"] * (r["roi_30d"] or 0) / 100 for r in rows),
            "dd_usd": sum(r["usd"] * (r["dd"] or 0) / 100 for r in rows),
            "skipped": [k for k in analyses if k not in shares], "text": p["text"]}


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
