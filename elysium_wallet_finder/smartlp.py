"""Reads RH Smart LP Finder's database (never writes to it).

When that project sits next to this folder, two things come over:
  - its saved wallets (smart_lp_wallets) join the watchlist, starred, with
    their labels;
  - its per-pool rankings (pool_lp_finder_cache) become a "Smart LP" badge:
    the best place each wallet reached in a pool scan, and those wallets get
    priced first.

The database is copied (with its -wal/-shm files) to a temp folder and read
from the copy, so a running Smart LP Finder is never locked or touched.
"""
import json
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import config

RANKED_PER_POOL = 100  # what Smart LP Finder itself shows


def db_path():
    d = config.smart_lp_finder_dir()
    if not d:
        return None
    env = {}
    env.update(config._read_env(d / ".env"))
    env.update(config._read_env(d / "lp-finder" / ".env"))
    p = Path(env.get("DATABASE_PATH") or "./data/lp-finder.sqlite")
    p = p if p.is_absolute() else d / p
    return p if p.exists() else None


def _snapshot(path):
    tmp = Path(tempfile.mkdtemp(prefix="smartlp-"))
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(path) + suffix)
        if src.exists():
            shutil.copy2(src, tmp / (path.name + suffix))
    return tmp, tmp / path.name


def import_all(con, path=None, log=print):
    """-> {"saved": n, "ranked": n} (zeros when there is nothing to read)."""
    path = path or db_path()
    if not path:
        return {"saved": 0, "ranked": 0}
    tmp, copy = _snapshot(Path(path))
    try:
        theirs = sqlite3.connect(str(copy))
        tables = {r[0] for r in theirs.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        saved = _saved(con, theirs) if "smart_lp_wallets" in tables else 0
        ranked = _rankings(con, theirs) if "pool_lp_finder_cache" in tables else 0
        theirs.close()
    except sqlite3.Error as e:
        log(f"  Smart LP Finder database unreadable: {e}")
        return {"saved": 0, "ranked": 0}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    con.commit()
    return {"saved": saved, "ranked": ranked}


def _saved(con, theirs):
    rows = theirs.execute("SELECT wallet_address, label, saved_at_ms FROM smart_lp_wallets").fetchall()
    for addr, label, saved_at in rows:
        # their label fills ours only when we have none; a star is never removed here
        con.execute("""INSERT INTO labels(addr, label, starred, source, added) VALUES(?,?,1,'smart-lp-finder',?)
                       ON CONFLICT(addr) DO UPDATE SET label=COALESCE(labels.label, excluded.label), starred=1""",
                    (addr.lower(), (label or None), int((saved_at or 0) / 1000) or int(time.time())))
    return len(rows)


def _rankings(con, theirs):
    n = 0
    for pool, fb, tb, blob, at in theirs.execute(
            "SELECT pool_id, from_block, to_block, result_json, computed_at_ms FROM pool_lp_finder_cache"):
        try:
            res = json.loads(blob)
        except ValueError:
            continue
        wallets = sorted(res.get("wallets") or [], key=lambda w: -(w.get("pnlUsd") or 0))
        total = res.get("totalWalletsFound") or len(wallets)
        rows = []
        for i, w in enumerate(wallets[:RANKED_PER_POOL], 1):
            addr = (w.get("walletAddress") or "").lower()
            if len(addr) != 42:
                continue
            rows.append((addr, pool.lower(), fb, tb, i, total, w.get("pnlUsd"), w.get("pnlPercent"),
                         w.get("totalDepositedUsd"), w.get("positionCount"), int((at or 0) / 1000)))
        con.executemany("""INSERT INTO slf_ranks(addr, pool, from_block, to_block, rank, total, pnl_usd, pnl_pct,
                           deposited_usd, positions, computed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(addr, pool, from_block, to_block) DO UPDATE SET rank=excluded.rank,
                           total=excluded.total, pnl_usd=excluded.pnl_usd, pnl_pct=excluded.pnl_pct,
                           deposited_usd=excluded.deposited_usd, positions=excluded.positions,
                           computed_at=excluded.computed_at""", rows)
        n += len(rows)
    return n


def best_ranks(con):
    """{wallet: best Smart LP Finder placing} (lowest rank, then highest PnL)."""
    out = {}
    for r in con.execute("""SELECT addr, pool, rank, total, pnl_usd, pnl_pct, computed_at FROM slf_ranks
                            WHERE pnl_usd > 0 ORDER BY rank, pnl_usd DESC"""):
        out.setdefault(r["addr"], dict(r))
    for v in out.values():
        v["pools"] = 0
    for r in con.execute("SELECT addr, COUNT(DISTINCT pool) FROM slf_ranks WHERE pnl_usd > 0 GROUP BY addr"):
        if r[0] in out:
            out[r[0]]["pools"] = r[1]
    return out


def server_url():
    d = config.smart_lp_finder_dir()
    if not d:
        return None
    env = config._read_env(d / ".env")
    return f"http://localhost:{env.get('PORT') or 8787}/"
