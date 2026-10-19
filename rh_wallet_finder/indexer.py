"""Discovery: scans Uniswap V4 LP activity on Robinhood Chain into the DB.

One eth_getLogs filter covers both things we need:
  - PoolManager ModifyLiquidity (every open / add / remove / fee claim)
  - PositionManager Transfer (mint = position opened, burn = closed, owner)

The first run starts at the chain head and works backwards one segment at a
time, so the newest days are usable while older ones still load. Later runs
catch up from where the last one stopped. Every segment is committed with its
cursor, so stopping at any time loses nothing.
"""
import time

import chain as C
import config
from db import get_meta, set_meta

CONFIRMATIONS = 30
SEGMENT_BLOCKS = 120_000  # ~3.4 h of Robinhood Chain at ~0.1 s per block


def block_time(con, rpc, head):
    cached = get_meta(con, "block_time")
    if cached and time.time() - cached["at"] < 86400:
        return cached["bt"]
    span = min(1_000_000, head - 1)
    t1 = int(rpc.call("eth_getBlockByNumber", [hex(head), False])["timestamp"], 16)
    t0 = int(rpc.call("eth_getBlockByNumber", [hex(head - span), False])["timestamp"], 16)
    bt = max(0.01, (t1 - t0) / span)
    set_meta(con, "block_time", {"bt": bt, "at": time.time()})
    return bt


def _block_ts(rpc, n):
    return int(rpc.call("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)


def scan_segment(con, rpc, start, end, chunk, log):
    flt = {"address": [config.POOL_MANAGER, config.POSITION_MANAGER],
           "topics": [[C.MODIFY_LIQUIDITY, C.TRANSFER]]}
    t0 = time.time()

    def progress(done, total, n):
        if time.time() - progress.last > 5:
            progress.last = time.time()
            log(f"    blocks {done:,}/{total:,}  logs {n:,}  chunk {state['chunk']:,}")
    progress.last = time.time()
    state = {"chunk": chunk}
    logs, state["chunk"] = rpc.get_logs(flt, start, end, chunk, progress)

    # logs without blockTimestamp: interpolate between the segment's ends
    ts0 = ts1 = None
    if any(not lg.get("blockTimestamp") for lg in logs):
        ts0, ts1 = _block_ts(rpc, start), _block_ts(rpc, end)

    def ts_of(d):
        if d["ts"] is not None:
            return d["ts"]
        return int(ts0 + (ts1 - ts0) * (d["block"] - start) / max(1, end - start))

    pm = config.POSITION_MANAGER
    evs, trs = [], []
    for lg in logs:
        addr = lg["address"].lower()
        t0p = lg["topics"][0]
        if t0p == C.MODIFY_LIQUIDITY and addr == config.POOL_MANAGER:
            d = C.decode_modify(lg)
            if d["sender"] != pm:
                continue  # not an NFT position (other LP managers / hooks)
            evs.append((d["block"], d["log_index"], d["tx"], ts_of(d), d["salt"], d["pool"],
                        d["tl"], d["tu"], str(d["delta"])))
        elif t0p == C.TRANSFER and addr == pm and len(lg["topics"]) == 4:
            d = C.decode_nft_transfer(lg)
            trs.append((d["block"], d["log_index"], ts_of(d), d["token_id"], d["from"], d["to"]))

    con.executemany("INSERT OR IGNORE INTO events(block, log_index, tx, ts, token_id, pool, tl, tu, delta) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", evs)
    con.executemany("INSERT OR IGNORE INTO transfers(block, log_index, ts, token_id, from_addr, to_addr) "
                    "VALUES(?,?,?,?,?,?)", trs)
    touched = {e[4] for e in evs} | {t[3] for t in trs}
    rebuild_positions(con, touched)
    log(f"    {len(evs):,} LP events, {len(trs):,} NFT transfers, {len(touched):,} positions "
        f"({time.time() - t0:.0f}s)")
    return state["chunk"]


def rebuild_positions(con, token_ids):
    """Re-derive positions from their events and transfers."""
    ids = list(token_ids)
    zero = config.ZERO
    rows = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = ",".join("?" * len(part))
        evs, trs = {}, {}
        for r in con.execute(f"SELECT token_id, block, ts, pool, tl, tu, delta FROM events "
                             f"WHERE token_id IN ({q}) ORDER BY block, log_index", part):
            evs.setdefault(r[0], []).append(r)
        for r in con.execute(f"SELECT token_id, block, ts, from_addr, to_addr FROM transfers "
                             f"WHERE token_id IN ({q}) ORDER BY block, log_index", part):
            trs.setdefault(r[0], []).append(r)
        for tid in part:
            e, t = evs.get(tid, []), trs.get(tid, [])
            if not e:
                continue  # transfers only: no liquidity seen yet
            mint = next((x for x in t if x[3] == zero), None)
            burn = next((x for x in t if x[4] == zero), None)
            owner = None
            for x in t:
                if x[4] != zero:
                    owner = x[4]
            if burn is not None:
                owner = burn[3]
            liq = sum(int(x[6]) for x in e)
            open_block, open_ts = (mint[1], mint[2]) if mint else (e[0][1], e[0][2])
            close_block = close_ts = None
            if burn is not None:
                close_block, close_ts = burn[1], burn[2]
            elif mint and liq == 0 and int(e[-1][6]) < 0:
                close_block, close_ts = e[-1][1], e[-1][2]
            rows.append((tid, owner, e[0][3], e[0][4], e[0][5], open_block, open_ts, close_block, close_ts,
                         1 if mint else 0, str(liq) if mint else None, len(e), e[-1][2]))
    con.executemany("""
        INSERT INTO positions(token_id, owner, pool, tl, tu, open_block, open_ts, close_block, close_ts,
                              minted, liq, n_events, last_ts)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(token_id) DO UPDATE SET
          owner=COALESCE(excluded.owner, positions.owner), pool=excluded.pool, tl=excluded.tl, tu=excluded.tu,
          open_block=excluded.open_block, open_ts=excluded.open_ts,
          close_block=excluded.close_block, close_ts=excluded.close_ts, minted=excluded.minted,
          liq=excluded.liq, last_ts=excluded.last_ts,
          valued=CASE WHEN positions.n_events=excluded.n_events
                       AND positions.close_block IS excluded.close_block
                       AND positions.minted=excluded.minted THEN positions.valued ELSE 0 END,
          n_events=excluded.n_events
    """, rows)


def refresh_pools_tokens(con, rpc, log):
    """Pool keys and token info for anything new."""
    new_pools = [r[0] for r in con.execute(
        "SELECT DISTINCT pool FROM positions WHERE pool NOT IN (SELECT id FROM pools)")]
    for i in range(0, len(new_pools), 150):
        part = new_pools[i:i + 150]
        res = multicall(rpc, [(config.POSITION_MANAGER, C.cd_pool_keys(p)) for p in part], "latest")
        rows = []
        for p, r in zip(part, res):
            k = C.decode_pool_key(r) if r else None
            if k:
                rows.append((p, k["c0"], k["c1"], k["fee"], k["spacing"], k["hooks"]))
        con.executemany("INSERT OR IGNORE INTO pools VALUES(?,?,?,?,?,?)", rows)
    if new_pools:
        log(f"  {len(new_pools):,} new pools")

    new_tokens = [r[0] for r in con.execute(
        "SELECT a FROM (SELECT c0 AS a FROM pools UNION SELECT c1 FROM pools) "
        "WHERE a NOT IN (SELECT addr FROM tokens)")]
    if config.ZERO in new_tokens:
        con.execute("INSERT OR IGNORE INTO tokens(addr, symbol, decimals) VALUES(?, 'ETH', 18)", (config.ZERO,))
        new_tokens.remove(config.ZERO)
    for i in range(0, len(new_tokens), 60):
        part = new_tokens[i:i + 60]
        calls = []
        for t in part:
            calls += [(t, C.cd_simple(C.SEL_SYMBOL)), (t, C.cd_simple(C.SEL_DECIMALS)),
                      (t, C.cd_simple(C.SEL_TOTAL_SUPPLY))]
        res = multicall(rpc, calls, "latest")
        rows = []
        for j, t in enumerate(part):
            sym, dec, sup = res[3 * j:3 * j + 3]
            d = int(dec, 16) if dec else 18
            d = d if 0 <= d <= 36 else 18
            rows.append((t, (C.decode_string(sym) if sym else "")[:24] or t[:8], d,
                         int(sup, 16) / 10 ** d if sup else None))
        con.executemany("INSERT OR IGNORE INTO tokens(addr, symbol, decimals, supply) VALUES(?,?,?,?)", rows)
    if new_tokens:
        log(f"  {len(new_tokens):,} new tokens")


def multicall(rpc, calls, block):
    """[(target, calldata)] at one block -> [return hex or None]. A reply that
    keeps getting cut off is asked again in halves."""
    from rpc import TruncatedError
    out = []
    for i in range(0, len(calls), 300):
        part = calls[i:i + 300]
        try:
            ret = rpc.call("eth_call", [{"to": config.MULTICALL3, "data": C.encode_aggregate3(part)},
                                        block if isinstance(block, str) else hex(block)])
        except TruncatedError:
            if len(part) == 1:
                raise
            half = len(part) // 2
            out += multicall(rpc, part[:half], block) + multicall(rpc, part[half:], block)
            continue
        out += C.decode_aggregate3(ret)
    return out


def refresh_wallets(con):
    con.execute("""
        INSERT INTO wallets(addr, first_seen, last_seen, n_pos, n_open, n_closed)
        SELECT owner, MIN(open_ts), MAX(last_ts), COUNT(*),
               SUM(close_ts IS NULL AND minted=1), SUM(close_ts IS NOT NULL)
        FROM positions WHERE owner IS NOT NULL GROUP BY owner
        ON CONFLICT(addr) DO UPDATE SET first_seen=excluded.first_seen, last_seen=excluded.last_seen,
          n_pos=excluded.n_pos, n_open=excluded.n_open, n_closed=excluded.n_closed
    """)


def sync(con, rpc, cfg, log=print, max_segments=None, stop=lambda: False, call_rpc=None):
    """Catch up to the head, then keep filling history back to BACKFILL_DAYS.

    rpc does eth_getLogs; call_rpc (default: the same) the multicalls."""
    head = int(rpc.call("eth_blockNumber", []), 16) - CONFIRMATIONS
    bt = block_time(con, rpc, head)
    target = max(1, head - int(float(cfg["BACKFILL_DAYS"]) * 86400 / bt))
    chunk = get_meta(con, "log_chunk") or int(cfg["LOG_CHUNK"])
    fwd, back = get_meta(con, "fwd_block"), get_meta(con, "back_block")
    seg = 0

    def run(s, e):
        nonlocal chunk, seg
        log(f"  scanning blocks {s:,}–{e:,}")
        chunk = scan_segment(con, rpc, s, e, chunk, log)
        set_meta(con, "log_chunk", chunk)
        seg += 1

    if fwd is None:  # first run: newest segment first
        s = max(target, head - SEGMENT_BLOCKS + 1)
        run(s, head)
        set_meta(con, "fwd_block", head)
        set_meta(con, "back_block", s)
        con.commit()
        fwd, back = head, s
    # forward catch-up, oldest first
    while fwd < head and not stop() and (max_segments is None or seg < max_segments):
        e = min(head, fwd + SEGMENT_BLOCKS)
        run(fwd + 1, e)
        fwd = e
        set_meta(con, "fwd_block", fwd)
        con.commit()
    # history, newest first
    while back > target and not stop() and (max_segments is None or seg < max_segments):
        s = max(target, back - SEGMENT_BLOCKS)
        run(s, back - 1)
        back = s
        set_meta(con, "back_block", back)
        con.commit()
    refresh_pools_tokens(con, call_rpc or rpc, log)
    refresh_wallets(con)
    set_meta(con, "synced_at", int(time.time()))
    set_meta(con, "data_from_ts", con.execute("SELECT MIN(ts) FROM events").fetchone()[0])
    con.commit()
    return {"head": head, "fwd": fwd, "back": back, "target": target, "segments": seg,
            "done": back <= target and fwd >= head}
