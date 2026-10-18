"""Wallet metrics, scores, styles and similarity. Pure functions over plain
dicts so they can be tested without a chain or a database.

A position dict has: open_ts, close_ts (None = still open), dep, wd, fee, now,
pnl, cost, eth_pair, single, tl, tu, mcap.
"""
import math
import statistics

DAY = 86400


def _median(xs, default=None):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else default


FULL_RANGE_PCT = 1e6  # anything wider reads as "full range"


def range_pct(p):
    return min(FULL_RANGE_PCT, (math.pow(1.0001, min(p["tu"] - p["tl"], 200_000)) - 1) * 100)


def wallet_metrics(positions, now, window_days, data_from, real=False):
    """Everything the table shows for one wallet, or None without closed positions."""
    w0 = now - window_days * DAY
    closed = sorted((p for p in positions if p["close_ts"] and p["close_ts"] >= w0),
                    key=lambda p: p["close_ts"])
    open_ = [p for p in positions if not p["close_ts"]]
    if not closed:
        return None
    pnl = (lambda p: p["pnl"] - p["cost"]) if real else (lambda p: p["pnl"])
    scope = closed + open_

    net = sum(pnl(p) for p in closed)
    flt = sum(pnl(p) for p in open_)
    fees = sum(p["fee"] for p in scope)
    size = _median([p["dep"] for p in scope], 0.0)

    # capital actually at work: deposits weighted by time held inside the window
    span_start = max(w0, data_from or w0)
    span = max(3600, now - span_start)
    held = 0.0
    busy = 0.0
    for p in scope:
        a, b = max(p["open_ts"], span_start), (p["close_ts"] or now)
        if b > a:
            held += p["dep"] * (b - a)
            busy += b - a
    capital = max(held / span, size, 1e-9)
    first = min(p["open_ts"] for p in scope)
    active = max(3600, now - max(first, span_start))
    concur = busy / active

    roi = net / capital * 100 * (30 * DAY / max(span, DAY))  # under a day of data: no wild extrapolation

    wins = sum(1 for p in closed if pnl(p) > 0)
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

    durations = [p["close_ts"] - p["open_ts"] for p in closed]
    dep_all = sum(p["dep"] for p in scope) or 1e-9
    eth_share = sum(p["dep"] for p in scope if p["eth_pair"]) / dep_all * 100
    single = sum(1 for p in scope if p["single"]) / len(scope) * 100

    return {
        "net": net, "float": flt, "roi": roi, "win": wins / len(closed) * 100, "daily": daily,
        "dd": -max_dd / capital * 100, "recover": recover, "recover_open": unrecovered,
        "size": size, "eth": eth_share, "mcap": _median([p.get("mcap") for p in scope]),
        "duration": _median(durations), "range": _median([range_pct(p) for p in scope]),
        "fees": fees, "pos": len(scope), "closed": len(closed), "open": len(open_),
        "open_now": sum(p["now"] for p in open_), "concur": concur, "capital": capital,
        "single": single, "per_day": len(scope) / max(1.0, active / DAY),
    }


# ---- score -----------------------------------------------------------------
# (metric, weight, higher is better)
WEIGHTS = [
    ("net", 0.25, True), ("roi", 0.20, True), ("win", 0.15, True), ("daily", 0.10, True),
    ("dd", 0.10, True), ("recover", 0.05, False), ("fees", 0.05, True), ("closed", 0.10, True),
]
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


def score_all(metrics_list):
    """Scores 0..100 for a list of metric dicts (same order)."""
    ranks = {k: _pct_ranks([m[k] for m in metrics_list], hb) for k, _, hb in WEIGHTS}
    scores = []
    for i, m in enumerate(metrics_list):
        raw = sum(w * ranks[k][i] for k, w, _ in WEIGHTS) * 100
        conf = m["closed"] / (m["closed"] + 4)  # few trades: pulled toward 50
        s = 50 + (raw - 50) * conf
        if m["net"] <= 0:
            s = min(s, 45)
        scores.append(round(s, 1))
    return scores


def verdict(score):
    return next(v for t, v in VERDICTS if score >= t)


def copyable(m):
    """✓ a person can follow it, ✗ bot pace, ? in between."""
    d = m["duration"] or 0
    if d < 300 or m["per_day"] > 100:
        return "✗"
    if d >= 1800 and m["per_day"] <= 30:
        return "✓"
    return "?"


# ---- style -----------------------------------------------------------------
def style(positions, m):
    """('Day·Degen', 64): horizon and risk, with the share of positions that fit."""
    durs = [(p["close_ts"] or 0) - p["open_ts"] for p in positions if p["close_ts"]]
    if not durs:
        return None, 0

    def horizon(d):
        return "Scalp" if d < 1800 else "Day" if d < 86400 else "Multiday"

    def risk(p):
        mc = p.get("mcap")
        if p["eth_pair"] and mc is None:
            return "Blue"
        if mc is None:
            return None
        return "Degen" if mc < 1e6 else "Mid" if mc < 2e7 else "Blue"

    hs = [horizon(d) for d in durs]
    rs = [r for r in (risk(p) for p in positions) if r]
    h = max(set(hs), key=hs.count)
    r = max(set(rs), key=rs.count) if rs else None
    share = hs.count(h) / len(hs)
    if r:
        share = (share + rs.count(r) / len(rs)) / 2
    return (f"{h}·{r}" if r else h), round(share * 100)


# ---- similarity ------------------------------------------------------------
FEATURES = [
    ("duration", lambda v: math.log1p(max(0, v or 0) / 60)),
    ("range", lambda v: math.log1p(max(0, v or 0))),
    ("single", lambda v: (v or 0) / 100),
    ("eth", lambda v: (v or 0) / 100),
    ("mcap", lambda v: math.log10(max(1, v or 1))),
    ("size", lambda v: math.log10(max(1, v or 1))),
    ("per_day", lambda v: math.log1p(max(0, v or 0))),
    ("concur", lambda v: math.log1p(max(0, v or 0))),
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
