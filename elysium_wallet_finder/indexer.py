"""Discovery: Uniswap v4 NFT positions and v3 NFT positions on Elysium.

One eth_getLogs filter covers:
  - PoolManager Initialize (pool currencies) and ModifyLiquidity
  - every known v4 PositionManager's Transfer (mint / burn / owner)
  - each v3 NonfungiblePositionManager's Increase / Decrease / Collect / Transfer

ModifyLiquidity whose sender is not a PositionManager and not an EOA is a
protocol (router, vault, launchpad) holding the position itself. Those events
are counted and skipped: there is no wallet to attribute them to.

A direct EOA call is kept (venue 'eoa:<wallet>'). Salts that don't fit in a
63-bit integer are skipped; the NFT managers mint small ids, which is where
the activity is.

The first run starts at the head and walks backwards, so the newest blocks are
usable while older ones still load.
"""
import time

import chain as C
import config
from db import get_meta, set_meta

CONFIRMATIONS = 30
SEGMENT_BLOCKS = 20_000  # ~40 min at ~0.11 s/block; eth_getLogs here caps at 10k logs
SALT_MAX = 2 ** 63 - 1


def block_time(con, rpc, head):
    cached = get_meta(con, "block_time")
    if cached and time.time() - cached["at"] < 86400:
        return cached["bt"]
    span = min(200_000, head - 1)
    t1 = int(rpc.call("eth_getBlockByNumber", [hex(head), False])["timestamp"], 16)
    t0 = int(rpc.call("eth_getBlockByNumber", [hex(head - span), False])["timestamp"], 16)
    bt = max(0.01, (t1 - t0) / span)
    set_meta(con, "block_time", {"bt": bt, "at": time.time()})
    return bt


def _block_ts(rpc, n):
    return int(rpc.call("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)


def _keys(ids):
    """token id 7 (the tests) or 'venue:id' -> pos_key."""
    out = []
    for k in ids:
        s = str(k)
        out.append(s if ":" in s else f"v4:{s}")
    return out


def position_managers(con):
    known = set(config.V4_PMS)
    for r in con.execute("SELECT addr FROM venues WHERE kind='v4'"):
        known.add(r[0])
    return known


def classify_sender(con, rpc, addr, cache):
    """'pm' (v4 position NFT), 'eoa' (the wallet called PoolManager itself) or 'skip'."""
    addr = addr.lower()
    if addr in cache:
        return cache[addr]
    if addr in config.V4_PMS:
        cache[addr] = "pm"
        return "pm"
    code = rpc.call("eth_getCode", [addr, "latest"])
    if not code or code in ("0x", "0x0"):
        cache[addr] = "eoa"
        return "eoa"
    try:
        nxt = rpc.call("eth_call", [{"to": addr, "data": "0x" + C.SEL_NEXT_TOKEN_ID}, "latest"])
        pm = rpc.call("eth_call", [{"to": addr, "data": "0x" + C.SEL_POOL_MANAGER}, "latest"])
    except Exception:  # noqa: BLE001 - not a position manager
        nxt = pm = None
    ok = bool(nxt and nxt != "0x" and pm and pm[-40:] == config.POOL_MANAGER[-40:])
    cache[addr] = "pm" if ok else "skip"
    if ok:
        con.execute("INSERT OR IGNORE INTO venues(addr, kind, label, seen) VALUES(?, 'v4', 'discovered', ?)",
                    (addr, int(time.time())))
    return cache[addr]


def scan_segment(con, rpc, start, end, chunk, log):
    pms = position_managers(con)
    v3 = set(config.V3_NPM)
    addrs = [config.POOL_MANAGER, *sorted(pms), *sorted(v3)]
    topics = [C.MODIFY_LIQUIDITY, C.TRANSFER, C.INCREASE_LIQUIDITY, C.DECREASE_LIQUIDITY,
              C.COLLECT, C.INITIALIZE]
    flt = {"address": addrs, "topics": [topics]}
    t0 = time.time()

    def progress(done, total, n):
        if time.time() - progress.last > 5:
            progress.last = time.time()
            log(f"    blocks {done:,}/{total:,}  logs {n:,}  chunk {state['chunk']:,}")
    progress.last = time.time()
    state = {"chunk": chunk}
    logs, state["chunk"] = rpc.get_logs(flt, start, end, chunk, progress)

    ts0 = ts1 = None
    if any(not lg.get("blockTimestamp") for lg in logs):
        ts0, ts1 = _block_ts(rpc, start), _block_ts(rpc, end)

    def ts_of(d):
        if d["ts"] is not None:
            return d["ts"]
        return int(ts0 + (ts1 - ts0) * (d["block"] - start) / max(1, end - start))

    cache = get_meta(con, "sender_class") or {}
    evs, trs, pools = [], [], []
    skipped = wide = 0
    for lg in logs:
        addr = lg["address"].lower()
        top = lg["topics"][0]
        if top == C.INITIALIZE and addr == config.POOL_MANAGER:
            d = C.decode_initialize(lg)
            if d and d["c0"] != d["c1"]:
                pools.append((d["pool"], d["c0"], d["c1"], d["fee"], d["spacing"], d["hooks"], 0, "v4"))
            continue
        if top == C.MODIFY_LIQUIDITY and addr == config.POOL_MANAGER:
            d = C.decode_modify(lg)
            how = classify_sender(con, rpc, d["sender"], cache)
            if how == "skip":
                skipped += 1
                continue
            if d["salt"] > SALT_MAX:
                wide += 1
                continue
            venue = d["sender"] if how == "pm" else "eoa:" + d["sender"]
            evs.append((d["block"], d["log_index"], d["tx"], ts_of(d), d["salt"], d["pool"],
                        d["tl"], d["tu"], str(d["delta"]), str(d["salt"]), venue, "v4", None, None))
            continue
        if top == C.TRANSFER and addr in pms | v3 and len(lg["topics"]) == 4:
            d = C.decode_nft_transfer(lg)
            if d["token_id"] > SALT_MAX:
                continue
            trs.append((d["block"], d["log_index"], ts_of(d), d["token_id"], d["from"], d["to"], addr))
            continue
        if addr in v3 and top in (C.INCREASE_LIQUIDITY, C.DECREASE_LIQUIDITY, C.COLLECT):
            if top == C.COLLECT:
                d = C.decode_collect(lg)
                kind, liq = "col", 0
            else:
                d = C.decode_v3_liq(lg)
                kind = "inc" if top == C.INCREASE_LIQUIDITY else "dec"
                liq = d["liq"] if kind == "inc" else -d["liq"]
            if d["token_id"] > SALT_MAX:
                continue
            evs.append((d["block"], d["log_index"], d["tx"], ts_of(d), d["token_id"], None,
                        None, None, str(liq), None, addr, kind, str(d["amount0"]), str(d["amount1"])))

    set_meta(con, "sender_class", cache)
    # A PositionManager we hadn't heard of yet wasn't in this segment's address
    # filter, so its Transfer logs (the owner) were missed. Grab just those.
    new_pms = position_managers(con) - pms
    if new_pms:
        extra, _ = rpc.get_logs({"address": sorted(new_pms), "topics": [[C.TRANSFER]]}, start, end, chunk, None)
        if any(not lg.get("blockTimestamp") for lg in extra) and ts0 is None:
            ts0, ts1 = _block_ts(rpc, start), _block_ts(rpc, end)
        for lg in extra:
            if len(lg["topics"]) != 4:
                continue
            d = C.decode_nft_transfer(lg)
            if d["token_id"] > SALT_MAX:
                continue
            trs.append((d["block"], d["log_index"], ts_of(d), d["token_id"], d["from"], d["to"],
                        lg["address"].lower()))
        log(f"    {len(new_pms)} new v4 position manager(s), {len(extra):,} transfers")
    con.executemany("""INSERT OR IGNORE INTO events(block, log_index, tx, ts, token_id, pool, tl, tu, delta,
                       salt, venue, kind, r0, r1) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", evs)
    con.executemany("""INSERT OR IGNORE INTO transfers(block, log_index, ts, token_id, from_addr, to_addr, venue)
                       VALUES(?,?,?,?,?,?,?)""", trs)
    con.executemany("""INSERT INTO pools(id, c0, c1, fee, spacing, hooks, creator_fee, venue) VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO NOTHING""", pools)
    touched = {f"{e[10]}:{e[4]}" for e in evs} | {f"{t[6]}:{t[3]}" for t in trs}
    rebuild_positions(con, touched)
    log(f"    {len(evs):,} LP events, {len(trs):,} NFT transfers, {len(touched):,} positions, "
        f"{skipped:,} protocol mods skipped, {wide:,} wide salts skipped ({time.time() - t0:.0f}s)")
    prev = get_meta(con, "skipped_mods") or 0
    set_meta(con, "skipped_mods", prev + skipped)
    return state["chunk"]


def rebuild_positions(con, token_ids):
    """Re-derive positions from their events and transfers. Keys are pos_keys
    ('venue:tokenId') or bare token ids, which mean the v4 test venue."""
    ids = _keys(token_ids)
    zero = config.ZERO
    rows = []
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        q = ",".join("?" * len(part))
        evs, trs = {}, {}
        for r in con.execute(f"""SELECT pos_key, block, ts, pool, tl, tu, delta, venue, token_id FROM events
                                 WHERE pos_key IN ({q}) ORDER BY block, log_index""", part):
            evs.setdefault(r[0], []).append(r)
        for r in con.execute(f"""SELECT pos_key, block, ts, from_addr, to_addr, venue, token_id FROM transfers
                                 WHERE pos_key IN ({q}) ORDER BY block, log_index""", part):
            trs.setdefault(r[0], []).append(r)
        for key in part:
            e, t = evs.get(key, []), trs.get(key, [])
            if not e:
                continue
            venue, tid = e[0][7], e[0][8]
            eoa = (venue or "").startswith("eoa:")
            mint = next((x for x in t if x[3] == zero), None)
            burn = next((x for x in t if x[4] == zero), None)
            owner = venue.split(":", 1)[1] if eoa else None
            if not eoa:
                for x in t:
                    if x[4] != zero:
                        owner = x[4]
                if burn is not None:
                    owner = burn[3]
            liq = sum(int(x[6] or 0) for x in e)
            if eoa:
                open_block, open_ts = e[0][1], e[0][2]
                minted = 1 if any(int(x[6] or 0) > 0 for x in e) else 0
            else:
                open_block, open_ts = (mint[1], mint[2]) if mint else (e[0][1], e[0][2])
                minted = 1 if mint else 0
            close_block = close_ts = None
            if burn is not None:
                close_block, close_ts = burn[1], burn[2]
            elif minted and liq == 0 and int(e[-1][6] or 0) < 0:
                close_block, close_ts = e[-1][1], e[-1][2]
            pool, tl, tu = e[0][3], e[0][4], e[0][5]
            rows.append((tid, owner, pool, tl, tu, open_block, open_ts, close_block, close_ts,
                         minted, str(liq) if minted else None, len(e), e[-1][2], venue, key))
    con.executemany("""
        INSERT INTO positions(token_id, owner, pool, tl, tu, open_block, open_ts, close_block, close_ts,
                              minted, liq, n_events, last_ts, venue, pos_key)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(venue, token_id) DO UPDATE SET
          owner=COALESCE(excluded.owner, positions.owner),
          pool=COALESCE(excluded.pool, positions.pool), tl=COALESCE(excluded.tl, positions.tl),
          tu=COALESCE(excluded.tu, positions.tu),
          open_block=excluded.open_block, open_ts=excluded.open_ts,
          close_block=excluded.close_block, close_ts=excluded.close_ts, minted=excluded.minted,
          liq=excluded.liq, last_ts=excluded.last_ts,
          valued=CASE WHEN positions.n_events=excluded.n_events
                       AND positions.close_block IS excluded.close_block
                       AND positions.minted=excluded.minted THEN positions.valued ELSE 0 END,
          n_events=excluded.n_events
    """, rows)


def resolve_v3(con, rpc, log):
    """Fill pool address and ticks for v3 positions from positions() + getPool()."""
    npms = list(config.V3_NPM)
    if not npms:
        return
    q = ",".join("?" * len(npms))
    rows = [dict(r) for r in con.execute(
        f"""SELECT venue, token_id, pos_key FROM positions
            WHERE venue IN ({q}) AND (pool IS NULL OR pool='')""", npms)]
    if not rows:
        return
    for i in range(0, len(rows), 80):
        part = rows[i:i + 80]
        calls = [(r["venue"], C.cd_v3_positions(r["token_id"])) for r in part]
        try:
            res = multicall(rpc, calls, "latest")
        except Exception as e:  # noqa: BLE001
            log(f"  v3 position read failed: {e}")
            return
        pool_calls, decoded = [], []
        for r, ret in zip(part, res):
            pos = C.decode_v3_position(ret)
            if not pos:
                continue
            fac = config.V3_NPM[r["venue"]]["factory"]
            c0, c1 = (pos["c0"], pos["c1"]) if int(pos["c0"], 16) < int(pos["c1"], 16) else (pos["c1"], pos["c0"])
            # positions() already returns sorted token0 < token1
            pool_calls.append((fac, C.cd_get_pool(pos["c0"], pos["c1"], pos["fee"])))
            decoded.append((r, pos))
        if not pool_calls:
            continue
        try:
            pools = multicall(rpc, pool_calls, "latest")
        except Exception as e:  # noqa: BLE001
            log(f"  v3 getPool failed: {e}")
            return
        for (r, pos), ret in zip(decoded, pools):
            if not ret:
                continue
            pool = "0x" + ret[-40:]
            if int(pool, 16) == 0:
                continue
            con.execute("""UPDATE positions SET pool=?, tl=?, tu=? WHERE pos_key=?""",
                        (pool, pos["tl"], pos["tu"], r["pos_key"]))
            con.execute("""UPDATE events SET pool=?, tl=?, tu=? WHERE pos_key=? AND (pool IS NULL OR pool='')""",
                        (pool, pos["tl"], pos["tu"], r["pos_key"]))
            label = config.V3_NPM[r["venue"]]["label"]
            con.execute("""INSERT INTO pools(id, c0, c1, fee, spacing, hooks, creator_fee, venue)
                           VALUES(?,?,?,?,?, ?, 0, ?) ON CONFLICT(id) DO NOTHING""",
                        (pool, pos["c0"], pos["c1"], pos["fee"], 0, config.ZERO, label))
    log(f"  resolved {len(rows):,} v3 positions")


def mark_launches(con, early_minutes):
    """Locker-owned v3 positions are the launch inventory: their pools pay the
    creator, not outside LPs, and a wallet that joins within EARLY_MINUTES is early."""
    locker = config.LOCKER
    con.execute("UPDATE positions SET launch=1 WHERE owner=?", (locker,))
    con.execute("""UPDATE pools SET creator_fee=1 WHERE id IN
                   (SELECT pool FROM positions WHERE owner=? AND pool IS NOT NULL)""", (locker,))
    window = int(float(early_minutes) * 60)
    launches = con.execute("""SELECT pool, MIN(open_ts) FROM positions
                              WHERE owner=? AND pool IS NOT NULL AND open_ts IS NOT NULL GROUP BY pool""",
                           (locker,)).fetchall()
    n = 0
    for pool, ts in launches:
        cur = con.execute("""UPDATE positions SET early=1 WHERE pool=? AND IFNULL(owner,'')!=?
                             AND open_ts IS NOT NULL AND open_ts<=? AND IFNULL(early,0)=0""",
                          (pool, locker, ts + window))
        n += cur.rowcount
    return n


def refresh_pools_tokens(con, rpc, log):
    """Pool keys (v4) and token info for anything new. v3 pools are filled in resolve_v3."""
    pms = list(position_managers(con))
    new_pools = [r[0] for r in con.execute(
        "SELECT DISTINCT pool FROM positions WHERE pool IS NOT NULL AND pool != '' "
        "AND pool NOT IN (SELECT id FROM pools)")]
    for i in range(0, len(new_pools), 80):
        part = new_pools[i:i + 80]
        # 32-byte ids are v4; 20-byte addresses were missed by resolve_v3
        v4 = [p for p in part if len(p) == 66]
        for pm in pms:
            if not v4:
                break
            res = multicall(rpc, [(pm, C.cd_pool_keys(p)) for p in v4], "latest")
            rows, still = [], []
            for p, r in zip(v4, res):
                k = C.decode_pool_key(r) if r else None
                if k and k["c0"] != k["c1"]:
                    rows.append((p, k["c0"], k["c1"], k["fee"], k["spacing"], k["hooks"], 0, "v4"))
                else:
                    still.append(p)
            con.executemany("""INSERT INTO pools(id, c0, c1, fee, spacing, hooks, creator_fee, venue)
                               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", rows)
            v4 = still
    if new_pools:
        log(f"  {len(new_pools):,} new pools")

    new_tokens = [r[0] for r in con.execute(
        "SELECT a FROM (SELECT c0 AS a FROM pools UNION SELECT c1 FROM pools) "
        "WHERE a NOT IN (SELECT addr FROM tokens) AND a IS NOT NULL")]
    if config.ZERO in new_tokens:
        con.execute("INSERT OR IGNORE INTO tokens(addr, symbol, decimals) VALUES(?, 'HYPE', 18)", (config.ZERO,))
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
    """[(target, calldata)] at one block -> [return hex or None]."""
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
    """Catch up to the head, then keep filling history back to BACKFILL_DAYS
    (0 = block 1). rpc does eth_getLogs; call_rpc (default: the same) the multicalls."""
    head = int(rpc.call("eth_blockNumber", []), 16) - CONFIRMATIONS
    bt = block_time(con, rpc, head)
    days = float(cfg["BACKFILL_DAYS"])
    target = 1 if days <= 0 else max(1, head - int(days * 86400 / bt))
    chunk = get_meta(con, "log_chunk") or int(cfg["LOG_CHUNK"])
    fwd, back = get_meta(con, "fwd_block"), get_meta(con, "back_block")
    if fwd is not None and back is not None:
        # The next history segment starts at `back`, not at the head. Everything
        # from `back` through `fwd` was scanned on an earlier pass, so this log
        # jumps over it on purpose.
        log(f"  already scanned blocks {back:,}–{fwd:,}; this pass adds the new head, then older history")
    seg = 0

    def run(s, e):
        nonlocal chunk, seg
        log(f"  scanning blocks {s:,}–{e:,}")
        chunk = scan_segment(con, rpc, s, e, chunk, log)
        set_meta(con, "log_chunk", chunk)
        seg += 1

    if fwd is None:
        s = max(target, head - SEGMENT_BLOCKS + 1)
        run(s, head)
        set_meta(con, "fwd_block", head)
        set_meta(con, "back_block", s)
        con.commit()
        fwd, back = head, s
    while fwd < head and not stop() and (max_segments is None or seg < max_segments):
        e = min(head, fwd + SEGMENT_BLOCKS)
        run(fwd + 1, e)
        fwd = e
        set_meta(con, "fwd_block", fwd)
        con.commit()
    while back > target and not stop() and (max_segments is None or seg < max_segments):
        s = max(target, back - SEGMENT_BLOCKS)
        run(s, back - 1)
        back = s
        set_meta(con, "back_block", back)
        con.commit()
    calls = call_rpc or rpc
    resolve_v3(con, calls, log)
    mark_launches(con, cfg.get("EARLY_MINUTES") or 30)
    refresh_pools_tokens(con, calls, log)
    refresh_wallets(con)
    set_meta(con, "synced_at", int(time.time()))
    set_meta(con, "data_from_ts", con.execute("SELECT MIN(ts) FROM events").fetchone()[0])
    con.commit()
    return {"head": head, "fwd": fwd, "back": back, "target": target, "segments": seg,
            "done": back <= target and fwd >= head,
            "positions": con.execute("SELECT COUNT(*) FROM positions").fetchone()[0],
            "wallets": con.execute("SELECT COUNT(*) FROM wallets").fetchone()[0],
            "by_venue": {r[0]: r[1] for r in con.execute(
                "SELECT venue, COUNT(*) FROM positions GROUP BY venue")},
            "skipped_mods": get_meta(con, "skipped_mods") or 0}
