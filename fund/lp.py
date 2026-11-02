"""LP money flow, tracked for free.

The Wallet Finder (rh_wallet_finder/) already indexes and prices every Uniswap V4 position on
Robinhood Chain in its own database. This module only *reads* that file (SQLite, read-only):
no RPC calls, no API calls, no Claude usage. Numbers are as fresh as the Wallet Finder's
worker left them; `as_of` says when that was.

Per wallet you list in config (`lp_wallets`):
  open_usd    current value of open positions that are priced
  idle_usd    USDG sitting in the wallet
  flows       USD put in, taken out and earned as fees, per day, from the priced events
  realized    PnL of positions closed in the window
"""
import sqlite3
import time
from datetime import datetime
from pathlib import Path


class Unavailable(Exception):
    """No Wallet Finder database, or no wallets configured."""


def _open(path):
    path = Path(path)
    if not path.is_file():
        raise Unavailable(f"Wallet Finder database not found: {path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=1")
    return con


def _wallets(wallets):
    w = sorted({x.strip().lower() for x in wallets or [] if x and x.strip()})
    if not w:
        raise Unavailable("no lp_wallets in config.json")
    return w


def snapshot(db_path, wallets):
    w = _wallets(wallets)
    q = ",".join("?" * len(w))
    con = _open(db_path)
    try:
        open_ = con.execute(f"""SELECT COUNT(*) n, SUM(CASE WHEN valued=1 THEN now_usd END) v,
                                       SUM(CASE WHEN valued=1 THEN 0 ELSE 1 END) unpriced
                                FROM positions WHERE lower(owner) IN ({q}) AND close_ts IS NULL""", w).fetchone()
        bal = con.execute(f"SELECT SUM(usd_bal) usd, MAX(bal_ts) ts FROM wallets WHERE lower(addr) IN ({q})",
                          w).fetchone()
        last_event = con.execute(f"""SELECT MAX(e.ts) ts FROM events e JOIN positions p ON p.token_id=e.token_id
                                     WHERE lower(p.owner) IN ({q})""", w).fetchone()["ts"]
    finally:
        con.close()
    open_usd, idle = open_["v"] or 0.0, bal["usd"] or 0.0
    return {
        "wallets": len(w),
        "open_positions": open_["n"] or 0,
        "unpriced_open": open_["unpriced"] or 0,
        "open_usd": round(open_usd, 2),
        "idle_usd": round(idle, 2),
        "total_usd": round(open_usd + idle, 2),
        "balance_as_of": _iso(bal["ts"]),
        "last_activity": _iso(last_event),
    }


def flows(db_path, wallets, days=30, now=None):
    w = _wallets(wallets)
    q = ",".join("?" * len(w))
    since = int((now or time.time()) - days * 86400)
    con = _open(db_path)
    try:
        rows = con.execute(f"""
            SELECT date(e.ts, 'unixepoch', 'localtime') day,
                   SUM(CASE WHEN CAST(e.delta AS REAL) > 0 THEN e.a0*e.u0 + e.a1*e.u1 ELSE 0 END) dep,
                   SUM(CASE WHEN CAST(e.delta AS REAL) < 0 THEN e.a0*e.u0 + e.a1*e.u1 ELSE 0 END) wd,
                   SUM(IFNULL(e.f0*e.u0, 0) + IFNULL(e.f1*e.u1, 0)) fee,
                   COUNT(*) n
            FROM events e JOIN positions p ON p.token_id = e.token_id
            WHERE lower(p.owner) IN ({q}) AND e.ts >= ? AND e.v_state = 1
            GROUP BY day ORDER BY day""", (*w, since)).fetchall()
        pending = con.execute(f"""SELECT COUNT(*) FROM events e JOIN positions p ON p.token_id = e.token_id
                                  WHERE lower(p.owner) IN ({q}) AND e.ts >= ? AND e.v_state = 0""",
                              (*w, since)).fetchone()[0]
        closed = con.execute(f"""SELECT COUNT(*) n, SUM(pnl_usd) pnl, SUM(fee_usd) fee FROM positions
                                 WHERE lower(owner) IN ({q}) AND close_ts >= ? AND valued = 1""",
                             (*w, since)).fetchone()
    finally:
        con.close()
    daily = [{"day": r["day"], "deposit": round(r["dep"] or 0, 2), "withdraw": round(r["wd"] or 0, 2),
              "fees": round(r["fee"] or 0, 2), "events": r["n"]} for r in rows]
    dep, wd, fee = (sum(d[k] for d in daily) for k in ("deposit", "withdraw", "fees"))
    return {
        "days": days,
        "deposit_usd": round(dep, 2),
        "withdraw_usd": round(wd, 2),
        "fees_usd": round(fee, 2),
        "net_in_usd": round(dep - wd, 2),          # > 0: more money went into LPs than came out
        "closed_positions": closed["n"] or 0,
        "realized_pnl_usd": round(closed["pnl"] or 0, 2),
        "unpriced_events": pending,
        "daily": daily,
    }


def _iso(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="minutes") if ts else None
