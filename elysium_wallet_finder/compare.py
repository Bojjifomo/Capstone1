"""Check our figures against RH Discovery's, wallet by wallet.

Copy rows from RH Discovery's table (select them, Cmd+C) and paste them into
the app's "vs RH Discovery" tab, or save them to a file for
`python3 finder.py compare FILE`. Each row's wallet (0x74a9…181b) is matched to
a full address in our database and the figures are set side by side.

Both sides measure the same things:
  PnL 30D / 7D / 3M / ALL  realized PnL of positions closed in that time
  Net worth               ETH + USDG in the wallet + its open positions
  ROI 30D                 PnL 30D / net worth
  ROI ALL                 PnL ALL / everything those positions deposited
  Avg / month             PnL ALL / calendar months since the first LP
  Win                     winning positions / positions that won or lost
  Open PnL                open positions' value + unclaimed fees - deposits
"""
import re
import time

import config
import queries as Q
from db import get_meta

# RH Discovery's columns, left to right, as its table shows them
COLUMNS = ["rank", "score", "wallet", "worth", "net", "roi_nw", "non_usdg", "usdg", "avg_month", "net_7d", "momentum",
           "pnl_3m", "pnl_1y", "pnl_all", "float", "roi_all", "win_pos"]
HEADERS = {"rank": "rank", "score": "score", "wallet": "wallet", "net worth": "worth", "pnl 30d": "net",
           "roi 30d": "roi_nw", "non-usdg": "non_usdg", "usdg%": "usdg", "usdg %": "usdg", "avg / month": "avg_month",
           "avg/month": "avg_month", "pnl 7d": "net_7d", "7d / 30d": "momentum", "7d/30d": "momentum",
           "pnl 3m": "pnl_3m", "pnl 1y": "pnl_1y", "pnl all": "pnl_all", "open pnl": "float", "roi all": "roi_all",
           "win": "win_pos", "win rate": "win_pos"}
WALLET_RE = re.compile(r"0x[0-9a-fA-F]{40}|0x[0-9a-fA-F]{3,}\s*(?:…|\.{2,3})\s*[0-9a-fA-F]{3,}")

# (key, label, how far apart still counts as the same: (relative, absolute))
CHECKS = [
    ("worth", "Net worth", (0.15, 50)),
    ("net", "PnL 30D", (0.10, 25)),
    ("roi_nw", "ROI 30D", (0.15, 3)),
    ("net_7d", "PnL 7D", (0.20, 25)),
    ("win_pos", "Win", (0, 3)),
    ("pnl_all", "PnL ALL", (0.10, 25)),
    ("avg_month", "Avg / month", (0.10, 25)),
    ("roi_all", "ROI ALL", (0.20, 0.3)),
    ("float", "Open PnL", (0.25, 25)),
]


def value(cell):
    """'+$83.4k' -> 83400, '-$95.81' -> -95.81, '+4140.1%' -> 4140.1, '–' / 'none open' -> None."""
    t = (cell or "").strip().replace(",", "").replace("−", "-").replace(" ", "")
    if t.lower() in ("", "–", "—", "-", "none open", "none_open", "n/a", "∞"):
        return None
    neg = t.startswith("-")
    t = t.lstrip("+-").replace("$", "").rstrip("%").strip()
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(t[-1:].lower(), 1)
    if mult != 1:
        t = t[:-1]
    try:
        v = float(t) * mult
    except ValueError:
        return None
    return -v if neg else v


def _header(cells):
    """A copied header row -> our key per column, or None when it isn't RH Discovery's."""
    keys = [HEADERS.get(re.sub(r"\s+", " ", re.sub(r"[*▾▴•↓↑_]", " ", c)).strip().lower()) for c in cells]
    if "wallet" not in keys or sum(k is not None for k in keys) < 0.6 * len(keys):
        return None
    if len(keys) == len(COLUMNS):  # a title we don't know: its place says what it is
        keys = [k or COLUMNS[i] for i, k in enumerate(keys)]
    return keys


def parse(text):
    """Pasted RH Discovery rows -> [{'wallet': '0x74a9…181b', 'net': 83400.0, ...}].

    Tab-separated rows (a copy of the table) use the header row when it was
    copied too; otherwise cells are read in RH Discovery's column order, lined
    up on the wallet cell."""
    rows, keys = [], None
    for line in text.splitlines():
        if not line.strip():
            continue
        tabbed = "\t" in line
        cells = [c.strip() for c in line.split("\t")] if tabbed else \
            re.sub(r"none open", "none_open", line, flags=re.I).split()
        m = WALLET_RE.search(line)
        if not m:
            keys = (_header(cells) if tabbed else None) or keys
            continue
        wallet = re.sub(r"\s|\.{2,3}", lambda x: "…" if x.group().startswith(".") else "", m.group())
        at = next((i for i, c in enumerate(cells) if WALLET_RE.search(c)), None)
        if at is None:
            continue
        if keys and len(keys) == len(cells):
            named = dict(zip(keys, cells))
        else:
            w = COLUMNS.index("wallet")
            named = dict(zip(COLUMNS[w + 1:], cells[at + 1:]))
            named.update(zip(COLUMNS[:w][::-1], cells[:at][::-1]))
        row = {"wallet": wallet}
        for k, c in named.items():
            if k and k != "wallet":
                row[k] = value(c)
        row["open_none"] = bool(re.search(r"none.open", line, re.I))
        rows.append(row)
    return rows


def resolve(con, wallet):
    """Short or full address -> [full addresses in our database that fit]."""
    w = wallet.lower()
    if re.fullmatch(r"0x[0-9a-f]{40}", w):
        return [r[0] for r in con.execute("SELECT addr FROM wallets WHERE addr=?", (w,))]
    head, _, tail = w.partition("…")
    return [r[0] for r in con.execute("SELECT addr FROM wallets WHERE addr LIKE ? AND addr LIKE ?",
                                      (head + "%", "%" + tail))]


def why_not_ranked(con, addr, cfg, now):
    """A wallet in our database that isn't on the board: the reason, in words."""
    days, active = float(cfg["METRIC_DAYS"]), float(cfg.get("ACTIVE_DAYS") or 0)
    w0 = now - days * 86400
    w = con.execute("SELECT last_seen FROM wallets WHERE addr=?", (addr,)).fetchone()
    if active and w and (w[0] or 0) < now - active * 86400:
        return f"no LP action for {(now - (w[0] or 0)) / 86400:.0f} days (Settings leaves out wallets idle > {active:g} d)"
    closed, unpriced, priced = con.execute(
        """SELECT COUNT(*), SUM(valued=0), SUM(valued=1) FROM positions
           WHERE owner=? AND minted=1 AND close_ts >= ?""", (addr, w0)).fetchone()
    if (closed or 0) < int(cfg["MIN_CLOSED"]):
        return f"only {closed or 0} closed positions in our {days:g}-day window"
    if not priced:
        return f"its {unpriced:,} positions are still in the pricing queue"
    return "not ranked in the last pass (bot rule, token launches, or priced since): see its dossier"


def _same(theirs, ours, tol):
    rel, ab = tol
    if theirs is None or ours is None:
        return None
    return abs(ours - theirs) <= max(ab, rel * abs(theirs))


def compare(con, rhd_rows, now=None, cfg=None):
    """Each pasted row with our figures next to RH Discovery's ->
    {'rows': [...], 'summary': {...}}"""
    now = now or time.time()
    cfg = cfg or config.load_env()
    ours = {r["addr"]: r for r in Q.wallet_rows(con, "raw")[0]}
    data_from = get_meta(con, "data_from_ts")
    out = []
    for r in rhd_rows:
        found = resolve(con, r["wallet"])
        row = {"wallet": r["wallet"], "rhd": r, "addr": found[0] if len(found) == 1 else None, "checks": [],
               "notes": []}
        if not found:
            since = time.strftime("%d %b", time.gmtime(data_from)) if data_from else "the start of our history"
            row.update(status="not in our data", reason=f"we have no LP position of it (our history starts {since})")
        elif len(found) > 1:
            row.update(status="ambiguous", reason=f"{len(found)} of our wallets fit {r['wallet']}: paste full "
                                                  "addresses")
        elif found[0] not in ours:
            row.update(status="not ranked", reason=why_not_ranked(con, found[0], cfg, now))
        else:
            m = ours[found[0]]
            row.update(status="ranked", reason="", ours=m)
            for key, label, tol in CHECKS:
                theirs, mine = r.get(key), m.get(key)
                if key == "float" and r.get("open_none"):
                    theirs = 0.0
                if key == "float" and not m.get("open"):
                    mine = 0.0 if mine is None else mine
                row["checks"].append({"key": key, "label": label, "theirs": theirs, "ours": mine,
                                      "same": _same(theirs, mine, tol)})
            if m.get("priced_from"):
                row["notes"].append(f"our PnL 30D covers only its last {(now - m['priced_from']) / 86400:.1f} days "
                                    "so far (older positions still being priced)")
            if m.get("hist_from"):
                row["notes"].append("our PnL ALL starts " + time.strftime("%d %b", time.gmtime(m["hist_from"]))
                                    + " (History to load in Settings); RH Discovery's goes back to its first LP")
            elif m.get("hist_gap"):
                row["notes"].append(f"{m['hist_gap']:,} of its older positions aren't priced yet")
        out.append(row)

    ranked = [r for r in out if r["status"] == "ranked"]
    per = {}
    for key, label, _ in CHECKS:
        cs = [c for r in ranked for c in r["checks"] if c["key"] == key and c["same"] is not None]
        per[key] = {"label": label, "compared": len(cs), "same": sum(1 for c in cs if c["same"])}
    summary = {"pasted": len(out), "ranked": len(ranked),
               "not_in_data": sum(1 for r in out if r["status"] == "not in our data"),
               "not_ranked": sum(1 for r in out if r["status"] == "not ranked"),
               "ambiguous": sum(1 for r in out if r["status"] == "ambiguous"), "per_metric": per,
               "scored_at": get_meta(con, "scored_at"), "metric_days": float(cfg["METRIC_DAYS"]),
               "data_from": data_from, "launch": config.CHAIN_LAUNCH_TS}
    return {"rows": out, "summary": summary}


def explain(summary):
    """Plain-language caveats for a comparison."""
    out = []
    if summary["metric_days"] != 30:
        out.append(f"Our window is {summary['metric_days']:g} days, RH Discovery's 30: PnL 30D and ROI 30D can't "
                   "match until Performance window in Settings is 30.")
    if summary.get("data_from") and summary["data_from"] > summary["launch"] + 86400:
        days = (time.time() - summary["data_from"]) / 86400
        out.append(f"Our history goes back {days:.0f} days, RH Discovery's to the chain's launch: PnL ALL, Avg / "
                   "month and ROI ALL only match for wallets that started after our history does.")
    out.append("Both boards move all the time: compare right after a ranking pass (and a fresh RH Discovery "
               "page). PnL 7D, Open PnL and net worth drift the fastest.")
    return out


def table(result):
    """The comparison as text, for the command line."""
    lines = []
    s = result["summary"]
    keys = ("net", "win_pos", "pnl_all", "worth")
    head = f"{'wallet':<14}" + "".join(f"{lab:>22}" for k, lab, _ in CHECKS if k in keys) + "  status"
    lines.append(head)
    lines.append("-" * len(head))
    fmt = {"win_pos": lambda v: f"{v:.1f}%"}
    for r in result["rows"]:
        cells = ""
        for c in r["checks"]:
            if c["key"] not in keys:
                continue
            f = fmt.get(c["key"], lambda v: f"${v:,.0f}")
            t = "–" if c["theirs"] is None else f(c["theirs"])
            o = "–" if c["ours"] is None else f(c["ours"])
            mark = "  " if c["same"] is None else " ✓" if c["same"] else " ✗"
            cells += f"{t + ' / ' + o + mark:>22}"
        if not r["checks"]:
            cells = " " * (22 * len(keys))
        lines.append(f"{r['wallet']:<14}{cells}  {r['status']}{': ' + r['reason'] if r['reason'] else ''}")
        for n in r["notes"]:
            lines.append(f"{'':<14}  · {n}")
    lines.append("")
    lines.append(f"{s['pasted']} pasted · {s['ranked']} ranked by us · {s['not_ranked']} found but not ranked · "
                 f"{s['not_in_data']} not in our data" + (f" · {s['ambiguous']} ambiguous" if s["ambiguous"] else ""))
    lines.append("Within tolerance (RHD / ours): " + ", ".join(
        f"{p['label']} {p['same']}/{p['compared']}" for p in s["per_metric"].values() if p["compared"]))
    lines += [f"Note: {x}" for x in explain(s)]
    return "\n".join(lines)
