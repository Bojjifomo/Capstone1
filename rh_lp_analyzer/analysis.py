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

# verdict lines: COPY needs every one of these, REJECT is a losing wallet
COPY_MIN_CLOSED = 10
COPY_MIN_PF = 1.5
COPY_MIN_WIN = 50.0
COPY_MAX_DD = -30.0  # % of capital
COPY_MAX_TOP2 = 60.0  # % of the profit from its two best days
VERDICT_TEXT = {
    "COPY": "Profitable, steady and followable by hand. A copy candidate.",
    "WATCH": "Making money, but something below is off. Watch it before you copy.",
    "REJECT": "Net loss (or loses more than it wins) over the period. Not worth copying.",
}
KIND_TEXT = {
    "multiday": "Holds its trades for hours to days. Slower, wider plays that leave time to follow by hand.",
    "degen": "In and out within hours. Fast trades: you need to be quick (or automated) to copy it.",
}

RANGE_BUCKETS = [(5, "< 5%"), (10, "5–10%"), (20, "10–20%"), (50, "20–50%"), (100, "50–100%"),
                 (300, "100–300%"), (math.inf, "> 300%")]
HOLD_BUCKETS = [(3600, "< 1h"), (6 * 3600, "1–6h"), (DAY, "6–24h"), (3 * DAY, "1–3d"), (math.inf, "≥ 3d")]
MCAP_BUCKETS = [(1e5, "< $100K"), (2.5e5, "$100K–250K"), (5e5, "$250K–500K"), (1e6, "$500K–1M"),
                (3e6, "$1M–3M"), (1e7, "$3M–10M"), (math.inf, "> $10M")]
SIDE_LABEL = {"bid": "ETH/USDG only", "both": "Two-sided", "ask": "Token only"}

# recommended filter: what a copier can see before following a trade
FILTER_MCAPS = [(0, "any mcap"), (1e5, "mcap ≥ $100K"), (5e5, "mcap ≥ $500K"), (1e6, "mcap ≥ $1M"),
                (1e7, "mcap ≥ $10M")]
FILTER_SIDES = [(None, "any side"), ("bid", "ETH/USDG only"), ("both", "two-sided"), ("ask", "token only")]
FILTER_QUOTES = [(None, "any quote"), (1, "ETH pools"), (0, "USDG pools")]
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


def gameplay(hold, rng, mcap):
    """(name, what it means) from the median trade's hold, range and token size."""
    if hold is None:
        return "Fresh", "No closed trade yet: too early to say how it plays."
    big = mcap is not None and mcap >= 1e6
    if hold < 3600:
        if rng is not None and rng < 20:
            return (("Liquid scalper", "Fast in and out of liquid (≥ $1M) tokens with tight ranges, riding short moves.")
                    if big else ("Micro-cap sniper", "Fast in and out of small tokens with tight ranges. High risk, "
                                                     "needs quick hands to follow."))
        return "Fast rotator", "Short holds on wide ranges: farms the fees of a burst of volume, then leaves."
    if hold < DAY:
        if rng is not None and rng < 50:
            return "Intraday range LP", "Tight-to-mid ranges held for hours: picks a level and farms around it."
        return "Intraday wide LP", "Wide ranges held for hours: steady fee farming, less babysitting."
    if rng is not None and rng >= 100:
        return "Wide position holder", "Wide ranges held for days: low maintenance, rides the trend for fees."
    return "Swing range LP", "Mid ranges held for days: a view on where price stays, rebalanced now and then."


def flags(a):
    """Plain-word reads of the profit and loss structure: [(tone, title, text)]
    with tone 'good' | 'bad' | 'info'."""
    out = []
    top2 = a["concentration"]
    if top2 is not None and a["green_days"] >= 3:
        if top2 >= COPY_MAX_TOP2:
            out.append(("bad", "High profit concentration",
                        f"{top2:.0f}% of the profit came from its 2 best days: it depends on a few lucky runs."))
        elif top2 <= 35:
            out.append(("good", "Steady profit", f"Its 2 best days are only {top2:.0f}% of the profit: it earns "
                                                 "across many days."))
    if a["worst_day"] is not None and a["avg_green"]:
        r = -a["worst_day"] / a["avg_green"]
        if r <= 1:
            out.append(("good", "Controlled loss", "Its worst day loses less than a typical green day makes."))
        elif r >= 3:
            out.append(("bad", "Heavy loss days", f"Its worst day wiped out ≈{r:.1f} typical green days."))
    if a["loss"] > 0:
        r = a["profit"] / a["loss"]
        if r >= 3:
            out.append(("good", "Efficient profit structure", f"It makes {r:.1f}× what it loses."))
        elif r < 1:
            out.append(("bad", "Losing structure", "Its losing positions cost more than the winners make."))
    elif a["profit"] > 0:
        out.append(("good", "No losing position", "Every decided position in the period made money."))
    if a["oor_rate"] is not None and a["oor_rate"] >= 30:
        out.append(("bad", "Often caught out of range", f"{a['oor_rate']:.0f}% of its closes were out of range at a "
                                                         "loss: price ran through its ranges."))
    if a["follow"] == "✗":
        out.append(("bad", "Bot pace", "Holds under 5 min or trades 100+ times a day: not followable by hand."))
    elif a["follow"] == "✓":
        out.append(("good", "Followable", "Holds long and trades slowly enough to copy by hand."))
    if a["dd"] is not None and a["dd"] <= COPY_MAX_DD:
        out.append(("bad", "Deep drawdown", f"Its PnL fell {-a['dd']:.0f}% of its capital from a high at one point."))
    return out


def verdict(a):
    """('COPY' | 'WATCH' | 'REJECT', reasons it isn't COPY)."""
    if a["net"] <= 0 or (a["pf"] is not None and a["pf"] < 1):
        return "REJECT", []
    why = []
    if a["closed"] < COPY_MIN_CLOSED:
        why.append(f"only {a['closed']} closed positions (needs {COPY_MIN_CLOSED})")
    if a["pf"] is not None and a["pf"] < COPY_MIN_PF:
        why.append(f"profit factor {a['pf']:.2f} (needs {COPY_MIN_PF})")
    if a["win"] is not None and a["win"] < COPY_MIN_WIN:
        why.append(f"win rate {a['win']:.0f}% (needs {COPY_MIN_WIN:.0f}%)")
    if a["dd"] is not None and a["dd"] <= COPY_MAX_DD:
        why.append(f"drawdown {a['dd']:.0f}% (limit {COPY_MAX_DD:.0f}%)")
    if a["concentration"] is not None and a["green_days"] >= 3 and a["concentration"] >= COPY_MAX_TOP2:
        why.append(f"{a['concentration']:.0f}% of profit from 2 days (limit {COPY_MAX_TOP2:.0f}%)")
    if a["follow"] == "✗":
        why.append("bot pace")
    return ("WATCH" if why else "COPY"), why


def recommend_filters(positions, pnl):
    """Grid of entry filters a copier can apply (token mcap, side, ETH vs USDG
    pool): which combination would have kept the most PnL. Counted per trade.
    -> {'all': row, 'best': [rows that beat taking everything, best first]},
    row = {name, trades, pnl, win}"""
    tr = [t for t in M.trades(positions) if t["close_ts"]]

    def row(name, ts):
        res = [(sum(pnl(p) for p in t["legs"]), t["dep"]) for t in ts]
        w = sum(1 for x, d in res if x > M.FLAT * d)
        decided = w + sum(1 for x, d in res if x < -M.FLAT * d)
        return {"name": name, "trades": len(ts), "pnl": sum(x for x, _ in res),
                "win": w / decided * 100 if decided else None}

    rows, seen = [], set()
    for lo, mname in FILTER_MCAPS:
        for side, sname in FILTER_SIDES:
            for quote, qname in FILTER_QUOTES:
                ts = [t for t in tr if (not lo or (t["mcap"] or 0) >= lo) and (side is None or t["side"] == side)
                      and (quote is None or t["legs"][0].get("eth_pair") == quote)]
                key = frozenset(id(t) for t in ts)
                if len(ts) >= FILTER_MIN_TRADES and (lo or side or quote is not None) and key not in seen:
                    seen.add(key)  # the same trades as a simpler filter: keep the simpler one
                    rows.append(row(" · ".join(n for n, on in ((mname, lo), (sname, side), (qname, quote is not None))
                                               if on), ts))
    everything = row("everything", tr)
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
    kind, kind_fit = M.player_kind(holds, [now - t["open_ts"] for t in tr if not t["close_ts"]], multiday_s)
    hold = M._median(holds)
    rng = M._median([M.range_pct(t) for t in tr])
    mcap = M._median([t["mcap"] for t in tr])
    per_day = len(tr) / max(1.0, active / DAY)
    follow = "✗" if (hold is not None and hold < M.BOT_HOLD_S) or per_day > M.BOT_PER_DAY else \
        "✓" if hold is not None and hold >= 1800 and per_day <= 30 else "?"
    sides = [p.get("side") for p in scope if p.get("side")]
    play = gameplay(hold, rng, mcap)

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
        "dd": -dd / capital * 100 if capital else None, "dd_usd": -dd,
        "worst_day": min(red) if red else None, "avg_green": avg_green,
        "avg_red": statistics.fmean(red) if red else None, "green_days": len(green), "red_days": len(red),
        "concentration": top2 / sum(green) * 100 if green else None,
        "oor_rate": len(oor_loss) / len(oor_known) * 100 if oor_known else None,
        "oor_n": len(oor_loss), "oor_of": len(oor_known),
        "oor_worst": min(oor_loss) if oor_loss else None,
        "oor_median": statistics.median(oor_loss) if oor_loss else None,
        "kind": kind, "kind_fit": kind_fit, "hold": hold, "range": rng, "mcap": mcap, "per_day": per_day,
        "follow": follow, "play": play[0], "play_text": play[1],
        "style": M.style(tr)[0],
        "sides": [(SIDE_LABEL[s], sides.count(s) / len(sides) * 100) for s in ("bid", "both", "ask")] if sides else [],
        "range_dist": distribution([M.range_pct(t) for t in tr], RANGE_BUCKETS),
        "hold_dist": distribution(holds, HOLD_BUCKETS),
        "mcap_dist": distribution([t["mcap"] for t in tr], MCAP_BUCKETS),
        "mcap_known": sum(1 for t in tr if t["mcap"] is not None), "daily": daily,
        "pools": sorted(pools.values(), key=lambda g: -g["pnl"]),
        "eth_share": sum(p["dep"] for p in scope if p.get("eth_pair")) / (sum(p["dep"] for p in scope) or 1) * 100,
    }
    a["flags"] = flags(a)
    a["verdict"], a["why"] = verdict(a)
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
               "text": "Only steady, proven wallets (COPY/WATCH with profit factor ≥ 1.3, drawdown under 25%, "
                       "10+ closed positions, followable). Weighted toward smooth returns, 30% kept in cash."},
    "Balanced": {"reserve": 0.15, "cap": 0.35, "top": None,
                 "text": "Every non-rejected, followable wallet, weighted by ROI × profit factor and damped by "
                         "drawdown. 15% kept in cash."},
    "Aggressive": {"reserve": 0.05, "cap": 0.50, "top": 3,
                   "text": "The 3 highest-ROI profitable wallets, weighted hard toward the best. Only 5% in cash: "
                           "bigger upside, bigger drawdowns."},
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
        ok = (a["verdict"] in ("COPY", "WATCH") and pf >= 1.3 and dd < 25 and a["follow"] != "✗"
              and a["closed"] >= COPY_MIN_CLOSED)
        conc = a["concentration"] if a["concentration"] is not None else 50.0
        return (math.sqrt(r) * min(pf, 3) / 3 / (1 + dd / 10) * (1 - min(conc, 100) / 200)
                * (1.2 if a["kind"] == "multiday" else 0.8)) if ok else 0.0
    if profile == "Balanced":
        ok = a["verdict"] != "REJECT" and a["follow"] != "✗"
        return r * min(pf, 4) / 4 / (1 + dd / 25) if ok else 0.0
    return r ** 1.5  # Aggressive: any profitable wallet, the best ones far ahead


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
