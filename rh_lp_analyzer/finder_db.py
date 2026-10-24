"""Borrow what RH Wallet Finder already has, read-only: its database indexes
every Uniswap V4 position on Robinhood Chain over its sync window (NFT transfers
and every ModifyLiquidity, many already priced). Reading a wallet from it costs
no RPC request. The finder's files are only ever opened read-only.
"""
import json
import sqlite3
from pathlib import Path

import config


def path(cfg):
    """The finder's database: WALLET_FINDER_DB, or rh_wallet_finder/data/wallets.db
    next to this folder. None when it isn't there or is switched off ('0')."""
    v = (cfg.get("WALLET_FINDER_DB") or "").strip()
    if v.lower() in ("0", "off", "no", "false"):
        return None
    p = Path(v).expanduser() if v else config.HERE.parent / "rh_wallet_finder" / "data" / "wallets.db"
    return p if p.is_file() else None


def connect(p):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def synced(fcon):
    """(first block, last block) the finder has read every LP log of, or None."""
    meta = {r[0]: json.loads(r[1]) for r in fcon.execute(
        "SELECT k, v FROM meta WHERE k IN ('fwd_block', 'back_block')")}
    if meta.get("fwd_block") is None or meta.get("back_block") is None:
        return None
    return int(meta["back_block"]), int(meta["fwd_block"])


def copy_wallet(fcon, con, addr, lo, hi):
    """Copy the wallet's positions the finder saw in [lo, hi] into our cache ->
    (first block, last block) now covered, with each token's [start, end] block
    span whose LP logs are complete, or None if the finder has nothing for that range.
    -> (a, b, {token: (start, end)})"""
    rng = synced(fcon)
    if not rng:
        return None
    a, b = max(lo, rng[0]), min(hi, rng[1])
    if a > b:
        return None
    tids = [r[0] for r in fcon.execute("""SELECT DISTINCT token_id FROM transfers WHERE (from_addr=? OR to_addr=?)
                                          AND block BETWEEN ? AND ?""", (addr, addr, a, b))]
    spans = {}
    for i in range(0, len(tids), 400):
        part = tids[i:i + 400]
        q = ",".join("?" * len(part))
        evs = [dict(r) for r in fcon.execute(f"""SELECT block, log_index, tx, ts, token_id, pool, tl, tu, delta, v_state,
                                                 a0, a1, f0, f1, u0, u1, lp_fee FROM events WHERE token_id IN ({q})""",
                                              part)]
        tx_at = {(e["token_id"], e["block"]): e["tx"] for e in evs}
        trs = [dict(r) for r in fcon.execute(f"""SELECT block, log_index, ts, token_id, from_addr, to_addr FROM transfers
                                                 WHERE token_id IN ({q})""", part)]
        con.executemany("INSERT OR IGNORE INTO transfers VALUES(?,?,?,?,?,?,?)",
                        [(t["block"], t["log_index"], tx_at.get((t["token_id"], t["block"])), t["ts"], t["token_id"],
                          t["from_addr"], t["to_addr"]) for t in trs])
        con.executemany("""INSERT INTO events(block, log_index, tx, ts, token_id, pool, tl, tu, delta, v_state,
                           a0, a1, f0, f1, u0, u1, lp_fee) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(block, log_index) DO UPDATE SET v_state=excluded.v_state, a0=excluded.a0,
                           a1=excluded.a1, f0=excluded.f0, f1=excluded.f1, u0=excluded.u0, u1=excluded.u1,
                           lp_fee=excluded.lp_fee WHERE events.v_state=0 AND excluded.v_state != 0""",
                        [tuple(e.values()) for e in evs])
        con.executemany("INSERT OR IGNORE INTO nfts VALUES(?,?,?,?)",
                        [(e["token_id"], e["pool"], e["tl"], e["tu"]) for e in evs])
        con.executemany("INSERT OR IGNORE INTO receipts(tx) VALUES(?)", {(e["tx"],) for e in evs if e["tx"]})
        con.executemany("INSERT OR IGNORE INTO blocks(n, ts) VALUES(?,?)",
                        {(x["block"], x["ts"]) for x in evs + trs if x["ts"]})
        for t in part:
            mint = min((x["block"] for x in trs if x["token_id"] == t and x["from_addr"] == config.ZERO), default=None)
            burn = max((x["block"] for x in trs if x["token_id"] == t and x["to_addr"] == config.ZERO), default=None)
            if mint is not None:
                spans[t] = (max(mint, rng[0]), min(burn if burn is not None else rng[1], rng[1]))
        pools = list({e["pool"] for e in evs})
        if pools:
            qp = ",".join("?" * len(pools))
            rows = [tuple(r) for r in fcon.execute(f"SELECT id, c0, c1, fee, spacing, hooks FROM pools WHERE id IN ({qp})",
                                                   pools)]
            con.executemany("INSERT OR IGNORE INTO pools VALUES(?,?,?,?,?,?)", rows)
            toks = list({x for r in rows for x in (r[1], r[2])})
            if toks:
                qt = ",".join("?" * len(toks))
                con.executemany("INSERT OR IGNORE INTO tokens(addr, symbol, decimals, supply, price_usd, mcap, updated) "
                                "VALUES(?,?,?,?,?,?,?)",
                                [tuple(r) for r in fcon.execute(f"""SELECT addr, symbol, decimals, supply, price_usd, mcap,
                                                                    updated FROM tokens WHERE addr IN ({qt})""", toks)])
    return a, b, spans
