"""Loads one wallet's Uniswap V4 LP positions on demand, as cheaply as it can.

  1. The wallet's PositionManager NFT transfers (mint = opened, burn = closed)
     and the transactions it sent to the PositionManager, from the Blockscout
     explorer API: a few requests, no RPC quota. When the explorer can't be
     reached: eth_getLogs filtered on the wallet's address.
  2. The receipts of those transactions: every ModifyLiquidity of its positions
     (open, add, remove, fee claim), so pool and range too.
  3. A check per position: its liquidity changes must add up (0 once closed, its
     liquidity now while open). Only a position that doesn't (an action we
     didn't see, e.g. from another sender) gets its pool's LP logs scanned
     over its lifetime.
  4. Every event priced from chain state one block before it (pricing.py), and
     open positions valued at the head.

Everything lands in a local SQLite cache (data/cache.db), with the block
ranges already read, so analysing the wallet again only reads what is new.
"""
import json
import sqlite3
import threading
import time

import chain as C
import config
import explorer
import pricing
from keccak import selector
from pricing import Pricer, multicall

CONFIRMATIONS = 30
DAY = 86400
NFT_CEILING = 20_000_000  # blocks per eth_getLogs when filtered on one wallet (few results)
POOL_CEILING = 2_000_000  # ...when it is a whole pool's LP activity
SEL_POSITION_LIQUIDITY = selector("getPositionLiquidity(uint256)")
EXPLORER_RETRY_S = 1800  # explorer unreachable: use the RPC for this long before trying it again

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS scans (key TEXT, lo INTEGER, hi INTEGER);  -- block ranges already read, per key
CREATE INDEX IF NOT EXISTS scans_key ON scans(key);
CREATE TABLE IF NOT EXISTS blocks (n INTEGER PRIMARY KEY, ts INTEGER);
CREATE TABLE IF NOT EXISTS transfers (
  block INTEGER, log_index INTEGER, tx TEXT, ts INTEGER, token_id INTEGER, from_addr TEXT, to_addr TEXT,
  PRIMARY KEY (block, log_index)
);
CREATE INDEX IF NOT EXISTS tr_token ON transfers(token_id);
CREATE INDEX IF NOT EXISTS tr_from ON transfers(from_addr);
CREATE INDEX IF NOT EXISTS tr_to ON transfers(to_addr);
CREATE TABLE IF NOT EXISTS nfts (token_id INTEGER PRIMARY KEY, pool TEXT, tl INTEGER, tu INTEGER);
CREATE TABLE IF NOT EXISTS events (
  block INTEGER, log_index INTEGER, tx TEXT, ts INTEGER,
  token_id INTEGER, pool TEXT, tl INTEGER, tu INTEGER, delta TEXT,
  v_state INTEGER DEFAULT 0, a0 REAL, a1 REAL, f0 REAL, f1 REAL, u0 REAL, u1 REAL, lp_fee INTEGER,
  PRIMARY KEY (block, log_index)
);
CREATE INDEX IF NOT EXISTS ev_token ON events(token_id);
CREATE TABLE IF NOT EXISTS pools (id TEXT PRIMARY KEY, c0 TEXT, c1 TEXT, fee INTEGER, spacing INTEGER, hooks TEXT);
CREATE TABLE IF NOT EXISTS tokens (
  addr TEXT PRIMARY KEY, symbol TEXT, decimals INTEGER, supply REAL, price_usd REAL, mcap REAL, updated INTEGER
);
CREATE TABLE IF NOT EXISTS positions (
  token_id INTEGER PRIMARY KEY, owner TEXT, pool TEXT, tl INTEGER, tu INTEGER,
  open_block INTEGER, open_ts INTEGER, close_block INTEGER, close_ts INTEGER,
  minted INTEGER, liq TEXT, n_events INTEGER, last_ts INTEGER,
  valued INTEGER DEFAULT 0, -- 1 = every event priced, 2 = can't be priced
  dep_usd REAL, wd_usd REAL, fee_usd REAL, now_usd REAL, tok_out_usd REAL,
  pnl_usd REAL, cost_usd REAL, eth_pair INTEGER, single INTEGER, sell_fee REAL, launch INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS pos_owner ON positions(owner);
CREATE TABLE IF NOT EXISTS saved (addr TEXT PRIMARY KEY, label TEXT, added INTEGER);
CREATE TABLE IF NOT EXISTS lp_txs (addr TEXT, tx TEXT, block INTEGER, PRIMARY KEY (addr, tx));
CREATE TABLE IF NOT EXISTS receipts (tx TEXT PRIMARY KEY);  -- receipts already read
"""

_write_lock = threading.Lock()  # one loader writes at a time (Streamlit runs sessions in threads)


def connect(path=None):
    path = path or config.DB_PATH
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def set_meta(con, k, v):
    con.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


# ---- block ranges already read -------------------------------------------
def missing(con, key, lo, hi):
    """Parts of [lo, hi] not read yet for key -> [(lo, hi)]."""
    out, at = [], lo
    for a, b in con.execute("SELECT lo, hi FROM scans WHERE key=? AND hi >= ? AND lo <= ? ORDER BY lo",
                            (key, lo, hi)):
        if a > at:
            out.append((at, a - 1))
        at = max(at, b + 1)
    if at <= hi:
        out.append((at, hi))
    return out


def mark(con, key, lo, hi):
    """Record [lo, hi] as read, merged with the ranges it touches."""
    rows = con.execute("SELECT rowid, lo, hi FROM scans WHERE key=? AND hi >= ? AND lo <= ?",
                       (key, lo - 1, hi + 1)).fetchall()
    for r in rows:
        lo, hi = min(lo, r["lo"]), max(hi, r["hi"])
    con.executemany("DELETE FROM scans WHERE rowid=?", [(r["rowid"],) for r in rows])
    con.execute("INSERT INTO scans(key, lo, hi) VALUES(?,?,?)", (key, lo, hi))


def merge(spans):
    """Overlapping or touching (lo, hi) spans -> merged, sorted."""
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# ---- the loader ----------------------------------------------------------
class Loader:
    def __init__(self, con, rpc, call_rpc, cfg, progress=lambda text, frac=None: None):
        self.con, self.rpc, self.call_rpc, self.cfg, self.progress = con, rpc, call_rpc, cfg, progress

    def block_ts(self, blocks):
        """{block: timestamp}, read once and kept."""
        blocks = sorted(set(blocks))
        have = {}
        for i in range(0, len(blocks), 500):
            part = blocks[i:i + 500]
            have.update(self.con.execute(f"SELECT n, ts FROM blocks WHERE n IN ({','.join('?' * len(part))})",
                                         part).fetchall())
        need = [b for b in blocks if b not in have]
        if need:
            res = self.call_rpc.batch([("eth_getBlockByNumber", [hex(b), False]) for b in need], size=50)
            rows = [(b, int(r["timestamp"], 16)) for b, r in zip(need, res) if isinstance(r, dict)]
            self.con.executemany("INSERT OR IGNORE INTO blocks(n, ts) VALUES(?,?)", rows)
            have.update(rows)
        return have

    def block_at(self, ts, head):
        """The block at (about) a timestamp: a guess from the block time, refined."""
        head_ts = self.block_ts([head])[head]
        if ts >= head_ts:
            return head
        ref = max(1, head - 1_000_000)
        bt = max(0.01, (head_ts - self.block_ts([ref])[ref]) / (head - ref))
        guess = max(1, int(head - (head_ts - ts) / bt))
        for _ in range(4):
            t = self.block_ts([guess])[guess]
            step = int((ts - t) / bt)
            if abs(step) < 50:
                break
            guess = min(head, max(1, guess + step))
        return guess

    def _logs(self, flt, lo, hi, kind, ceiling):
        chunk = get_meta(self.con, f"chunk_{kind}") or int(self.cfg["LOG_CHUNK"])
        logs, chunk = self.rpc.get_logs(flt, lo, hi, chunk, ceiling=ceiling)
        set_meta(self.con, f"chunk_{kind}", chunk)
        return logs

    def _ts_of(self, logs):
        """{block: ts} for these logs (blockTimestamp when the RPC sends it)."""
        out = {int(lg["blockNumber"], 16): int(lg["blockTimestamp"], 16) for lg in logs if lg.get("blockTimestamp")}
        rest = {int(lg["blockNumber"], 16) for lg in logs} - set(out)
        if rest:
            out.update(self.block_ts(rest))
        return out

    # 1. the wallet's position NFTs and LP transactions
    def discover(self, addr, lo, hi, since_ts):
        """From the explorer when it answers (then the whole range counts as read),
        else from eth_getLogs."""
        if not missing(self.con, f"nft:{addr}", lo, hi):
            return
        use = str(self.cfg.get("EXPLORER_API", "1")).lower() not in ("0", "false", "no", "off")
        if use and time.time() >= (get_meta(self.con, "explorer_off_until") or 0):
            self.progress(f"Finding {C.short(addr)}'s positions on the explorer…")
            try:
                trs = explorer.nft_transfers(addr, since_ts)
                txs = explorer.lp_transactions(addr, since_ts)
            except explorer.ExplorerError as e:
                set_meta(self.con, "explorer_off_until", time.time() + EXPLORER_RETRY_S)
                set_meta(self.con, "explorer_error", str(e))
                self.con.commit()
            else:
                self.con.executemany("INSERT OR IGNORE INTO transfers VALUES(?,?,?,?,?,?,?)",
                                     [(t["block"], t["log_index"], t["tx"], t["ts"], t["token_id"], t["from"],
                                       t["to"]) for t in trs])
                self.con.executemany("INSERT OR IGNORE INTO lp_txs VALUES(?,?,?)",
                                     [(addr, t["tx"], t["block"]) for t in txs])
                self.con.executemany("INSERT OR IGNORE INTO blocks(n, ts) VALUES(?,?)",
                                     [(t["block"], t["ts"]) for t in trs + txs])
                mark(self.con, f"nft:{addr}", lo, hi)
                self.con.commit()
                return
        self.scan_nfts(addr, lo, hi)

    def scan_nfts(self, addr, lo, hi):
        key = f"nft:{addr}"
        w = "0x" + C.addr_word(addr)
        for a, b in missing(self.con, key, lo, hi):
            self.progress(f"Finding {C.short(addr)}'s positions (blocks {a:,}–{b:,})…")
            logs = []
            for topics in ([C.TRANSFER, None, w], [C.TRANSFER, w]):
                logs += self._logs({"address": config.POSITION_MANAGER, "topics": topics}, a, b, "nft",
                                   NFT_CEILING)
            ts = self._ts_of(logs)
            rows = []
            for lg in logs:
                if len(lg["topics"]) != 4:
                    continue
                d = C.decode_nft_transfer(lg)
                rows.append((d["block"], d["log_index"], d["tx"], ts.get(d["block"]), d["token_id"], d["from"],
                             d["to"]))
            self.con.executemany("INSERT OR IGNORE INTO transfers VALUES(?,?,?,?,?,?,?)", rows)
            mark(self.con, key, a, b)
            self.con.commit()

    # 2. LP events from the receipts of the transactions we know of
    def read_receipts(self, txs):
        """Every PositionManager ModifyLiquidity in these transactions' receipts
        (read once each) -> events, and each position's pool and range."""
        txs = list(dict.fromkeys(txs))
        have = set()
        for i in range(0, len(txs), 500):
            part = txs[i:i + 500]
            have.update(r[0] for r in self.con.execute(
                f"SELECT tx FROM receipts WHERE tx IN ({','.join('?' * len(part))})", part))
        todo = [t for t in txs if t not in have]
        for i in range(0, len(todo), 200):
            part = todo[i:i + 200]
            self.progress(f"Reading LP transactions… {i:,}/{len(todo):,}", i / max(1, len(todo)))
            res = self.call_rpc.batch([("eth_getTransactionReceipt", [tx]) for tx in part], size=20)
            found, done = [], []
            for tx, rc in zip(part, res):
                if not isinstance(rc, dict):
                    continue  # read again next time
                done.append((tx,))
                for lg in rc.get("logs") or []:
                    if lg["address"].lower() != config.POOL_MANAGER or not lg["topics"] \
                            or lg["topics"][0] != C.MODIFY_LIQUIDITY:
                        continue
                    d = C.decode_modify(lg)
                    if d["sender"] == config.POSITION_MANAGER:
                        found.append(d)
            ts = self.block_ts({d["block"] for d in found})
            self.con.executemany(
                "INSERT OR IGNORE INTO events(block, log_index, tx, ts, token_id, pool, tl, tu, delta) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                [(d["block"], d["log_index"], d["tx"], ts.get(d["block"]), d["salt"], d["pool"], d["tl"], d["tu"],
                  str(d["delta"])) for d in found])
            self.con.executemany("INSERT OR IGNORE INTO nfts VALUES(?,?,?,?)",
                                 [(d["salt"], d["pool"], d["tl"], d["tu"]) for d in found])
            self.con.executemany("INSERT OR IGNORE INTO receipts(tx) VALUES(?)", done)
            self.con.commit()

    def unbalanced(self, token_ids, head):
        """Positions whose liquidity changes we have don't add up: closed ones to 0,
        open ones to their liquidity now (a read at the head). Their pools get scanned."""
        q = ",".join("?" * len(token_ids))
        sums = {tid: 0 for tid in token_ids}
        seen = set()
        for tid, delta in self.con.execute(f"SELECT token_id, delta FROM events WHERE token_id IN ({q})", token_ids):
            sums[tid] += int(delta)
            seen.add(tid)
        burned = {r[0] for r in self.con.execute(
            f"SELECT token_id FROM transfers WHERE to_addr=? AND token_id IN ({q})", (config.ZERO, *token_ids))}
        bad = [t for t in token_ids if t not in seen or (t in burned and sums[t] != 0)]
        live = [t for t in token_ids if t in seen and t not in burned]
        if live:
            try:
                res = multicall(self.call_rpc, [(config.POSITION_MANAGER, "0x" + SEL_POSITION_LIQUIDITY + C.word(t))
                                                for t in live], hex(head))
            except Exception:  # noqa: BLE001 - can't check: scan them to be sure
                res = [None] * len(live)
            bad += [t for t, r in zip(live, res) if r is None or int(r, 16) != sums[t]]
        return bad

    # 3. every LP event of those positions
    def scan_events(self, token_ids, head):
        life = {}  # token -> (pool, first block, last block)
        q = ",".join("?" * len(token_ids))
        ends = {}
        for r in self.con.execute(f"SELECT token_id, block, from_addr, to_addr FROM transfers WHERE token_id IN ({q}) "
                                  "ORDER BY block", token_ids):
            if r["from_addr"] == config.ZERO:
                ends.setdefault(r["token_id"], [r["block"], head])[0] = r["block"]
            elif r["to_addr"] == config.ZERO:
                ends.setdefault(r["token_id"], [r["block"], head])[1] = r["block"]
        for r in self.con.execute(f"SELECT token_id, pool FROM nfts WHERE token_id IN ({q})", token_ids):
            if r["token_id"] in ends:
                life[r["token_id"]] = (r["pool"], *ends[r["token_id"]])
        pools = {}
        for tid, (pool, a, b) in life.items():
            gaps = missing(self.con, f"tok:{tid}", a, b)
            if gaps:
                pools.setdefault(pool, {"ids": set(), "spans": []})
                pools[pool]["ids"].add(tid)
                pools[pool]["spans"] += gaps
        pm = "0x" + C.addr_word(config.POSITION_MANAGER)
        for n, (pool, g) in enumerate(pools.items(), 1):
            ids = {t for t, (p, _, _) in life.items() if p == pool}
            for a, b in merge(g["spans"]):
                self.progress(f"Reading LP events, pool {n}/{len(pools)} (blocks {a:,}–{b:,})…", n / len(pools))
                logs = self._logs({"address": config.POOL_MANAGER, "topics": [C.MODIFY_LIQUIDITY, pool, pm]}, a, b,
                                  "pool", POOL_CEILING)
                logs = [lg for lg in logs if int(C.words(lg["data"])[3], 16) in ids]
                ts = self._ts_of(logs)
                mine = [C.decode_modify(lg) for lg in logs]
                self.con.executemany(
                    "INSERT OR IGNORE INTO events(block, log_index, tx, ts, token_id, pool, tl, tu, delta) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    [(d["block"], d["log_index"], d["tx"], ts.get(d["block"]), d["salt"], d["pool"],
                      d["tl"], d["tu"], str(d["delta"])) for d in mine])
            for tid in g["ids"]:
                _, a, b = life[tid]
                mark(self.con, f"tok:{tid}", a, b)
            self.con.commit()
        return list(life)

    def rebuild_positions(self, token_ids):
        """Positions from their events and transfers (as RH Wallet Finder's indexer)."""
        zero = config.ZERO
        rows = []
        for i in range(0, len(token_ids), 500):
            part = token_ids[i:i + 500]
            q = ",".join("?" * len(part))
            evs, trs = {}, {}
            for r in self.con.execute(f"SELECT token_id, block, ts, pool, tl, tu, delta FROM events "
                                      f"WHERE token_id IN ({q}) ORDER BY block, log_index", part):
                evs.setdefault(r[0], []).append(r)
            for r in self.con.execute(f"SELECT token_id, block, ts, from_addr, to_addr FROM transfers "
                                      f"WHERE token_id IN ({q}) ORDER BY block, log_index", part):
                trs.setdefault(r[0], []).append(r)
            for tid in part:
                e, t = evs.get(tid, []), trs.get(tid, [])
                if not e:
                    continue
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
        self.con.executemany("""
            INSERT INTO positions(token_id, owner, pool, tl, tu, open_block, open_ts, close_block, close_ts,
                                  minted, liq, n_events, last_ts)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(token_id) DO UPDATE SET
              owner=excluded.owner, pool=excluded.pool, tl=excluded.tl, tu=excluded.tu,
              open_block=excluded.open_block, open_ts=excluded.open_ts,
              close_block=excluded.close_block, close_ts=excluded.close_ts, minted=excluded.minted,
              liq=excluded.liq, last_ts=excluded.last_ts,
              valued=CASE WHEN positions.n_events=excluded.n_events
                           AND positions.close_block IS excluded.close_block
                           AND positions.minted=excluded.minted THEN positions.valued ELSE 0 END,
              n_events=excluded.n_events""", rows)
        self.con.commit()

    def refresh_pools_tokens(self):
        """Pool keys and token info for anything new."""
        new_pools = [r[0] for r in self.con.execute(
            "SELECT DISTINCT pool FROM positions WHERE pool NOT IN (SELECT id FROM pools)")]
        for i in range(0, len(new_pools), 150):
            part = new_pools[i:i + 150]
            res = multicall(self.call_rpc, [(config.POSITION_MANAGER, C.cd_pool_keys(p)) for p in part], "latest")
            rows = []
            for p, r in zip(part, res):
                k = C.decode_pool_key(r) if r else None
                if k:
                    rows.append((p, k["c0"], k["c1"], k["fee"], k["spacing"], k["hooks"]))
            self.con.executemany("INSERT OR IGNORE INTO pools VALUES(?,?,?,?,?,?)", rows)
        new_tokens = [r[0] for r in self.con.execute(
            "SELECT a FROM (SELECT c0 AS a FROM pools UNION SELECT c1 FROM pools) "
            "WHERE a NOT IN (SELECT addr FROM tokens)")]
        if config.ZERO in new_tokens:
            self.con.execute("INSERT OR IGNORE INTO tokens(addr, symbol, decimals) VALUES(?, 'ETH', 18)",
                             (config.ZERO,))
            new_tokens.remove(config.ZERO)
        for i in range(0, len(new_tokens), 60):
            part = new_tokens[i:i + 60]
            calls = []
            for t in part:
                calls += [(t, C.cd_simple(C.SEL_SYMBOL)), (t, C.cd_simple(C.SEL_DECIMALS)),
                          (t, C.cd_simple(C.SEL_TOTAL_SUPPLY))]
            res = multicall(self.call_rpc, calls, "latest")
            rows = []
            for j, t in enumerate(part):
                sym, dec, sup = res[3 * j:3 * j + 3]
                d = int(dec, 16) if dec else 18
                d = d if 0 <= d <= 36 else 18
                rows.append((t, (C.decode_string(sym) if sym else "")[:24] or t[:8], d,
                             int(sup, 16) / 10 ** d if sup else None))
            self.con.executemany("INSERT OR IGNORE INTO tokens(addr, symbol, decimals, supply) VALUES(?,?,?,?)", rows)
        self.con.commit()

    # 4. prices
    def price(self, token_ids, head):
        pr = Pricer(self.con, self.call_rpc, self.cfg, log=lambda *_: None)
        todo = [r[0] for r in self.con.execute(
            f"SELECT token_id FROM positions WHERE valued=0 AND minted=1 AND token_id IN "
            f"({','.join('?' * len(token_ids))}) ORDER BY open_ts DESC", token_ids)]
        for i in range(0, len(todo), 200):
            self.progress(f"Pricing positions from chain state… {i:,}/{len(todo):,}", i / max(1, len(todo)))
            pr.value_events(todo[i:i + 200])
            self.con.commit()
        open_ids = [r[0] for r in self.con.execute(
            f"SELECT token_id FROM positions WHERE close_ts IS NULL AND minted=1 AND valued != 2 AND token_id IN "
            f"({','.join('?' * len(token_ids))})", token_ids)]
        self.progress(f"Valuing {len(open_ids):,} open positions…")
        pr.aggregate(set(todo) | set(open_ids), pr.value_open(open_ids, head))
        eth = pr.refresh_tokens([r[0] for r in self.con.execute(
            f"SELECT DISTINCT pool FROM positions WHERE token_id IN ({','.join('?' * len(token_ids))})", token_ids)])
        if eth:
            set_meta(self.con, "eth_usd", eth)
        self.con.commit()
        return len(todo)

    def load(self, addrs, since_ts):
        """Read and price everything the analysis of these wallets from since_ts on needs."""
        with _write_lock:
            head = int(self.rpc.call("eth_blockNumber", []), 16) - CONFIRMATIONS
            lookback = float(self.cfg.get("LOOKBACK_DAYS") or 14) * DAY
            start_ts = max(config.CHAIN_LAUNCH_TS, since_ts - lookback)
            self.progress("Finding the start block…")
            lo = self.block_at(start_ts, head)
            for a in addrs:
                self.discover(a, lo, head, start_ts)
            qa = ",".join("?" * len(addrs))
            ids = [r[0] for r in self.con.execute(
                f"SELECT DISTINCT token_id FROM transfers WHERE from_addr IN ({qa}) OR to_addr IN ({qa})",
                (*addrs, *addrs))]
            if not ids:
                set_meta(self.con, "head", head)
                self.con.commit()
                return {"positions": 0, "priced": 0, "head": head, "from_ts": start_ts, "scanned": 0}
            qi = ",".join("?" * len(ids))
            txs = [r[0] for r in self.con.execute(f"SELECT tx FROM transfers WHERE token_id IN ({qi})", ids)]
            txs += [r[0] for r in self.con.execute(f"SELECT tx FROM lp_txs WHERE addr IN ({qa}) AND block >= ?",
                                                   (*addrs, lo))]
            self.read_receipts(txs)
            minted = [r[0] for r in self.con.execute(
                f"SELECT DISTINCT token_id FROM transfers WHERE from_addr=? AND token_id IN ({qi})", (config.ZERO, *ids))]
            bad = self.unbalanced(minted, head) if minted else []
            if bad:
                self.scan_events(bad, head)
            self.rebuild_positions(ids)
            self.refresh_pools_tokens()
            n = self.price(ids, head)
            for a in addrs:
                set_meta(self.con, f"loaded:{a}", {"at": int(time.time()), "from_ts": start_ts, "head": head})
            set_meta(self.con, "head", head)
            self.con.commit()
            return {"positions": len(ids), "priced": n, "head": head, "from_ts": start_ts, "scanned": len(bad)}


# ---- read side -----------------------------------------------------------
def pool_names(con, pool_ids):
    """{pool id: 'TOKEN/USDG'}."""
    ids = list(pool_ids)
    tok = {r[0]: r[1] for r in con.execute("SELECT addr, symbol FROM tokens")}
    out = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in con.execute(f"SELECT id, c0, c1 FROM pools WHERE id IN ({','.join('?' * len(part))})", part):
            out[r[0]] = f"{tok.get(r[1]) or r[1][:6]}/{tok.get(r[2]) or r[2][:6]}"
    return out


def positions(con, addrs, since, cfg):
    """Positions of these wallets open at some point after `since`, as
    analysis.analyze takes them (priced ones only), plus what was left out.
    -> (positions, {unpriced, unpriceable, launch, before})"""
    pr = Pricer(con, None, cfg)
    q = ",".join("?" * len(addrs))
    names = pool_names(con, {r[0] for r in con.execute(f"SELECT DISTINCT pool FROM positions WHERE owner IN ({q})",
                                                         addrs)})
    pos, skipped = [], {"unpriced": 0, "unpriceable": 0, "launch": 0, "before": 0}
    for r in con.execute(f"""SELECT token_id, owner, pool, tl, tu, open_block, open_ts, close_ts, minted, valued,
                             launch, dep_usd, wd_usd, fee_usd, now_usd, pnl_usd, cost_usd, eth_pair, single, sell_fee
                             FROM positions WHERE owner IN ({q}) AND (close_ts IS NULL OR close_ts >= ?)""",
                         (*addrs, since)):
        if not r["minted"]:
            skipped["before"] += 1
        elif r["valued"] == 0:
            skipped["unpriced"] += 1
        elif r["valued"] == 2:
            skipped["unpriceable"] += 1
        elif r["launch"]:
            skipped["launch"] += 1
        else:
            pos.append({"token_id": r["token_id"], "owner": r["owner"], "pool": r["pool"], "tl": r["tl"],
                        "tu": r["tu"], "open_block": r["open_block"], "open_ts": r["open_ts"],
                        "close_ts": r["close_ts"], "dep": r["dep_usd"], "wd": r["wd_usd"], "fee": r["fee_usd"],
                        "now": r["now_usd"] or 0.0, "pnl": r["pnl_usd"], "cost": r["cost_usd"],
                        "eth_pair": r["eth_pair"], "single": r["single"], "sell_fee": r["sell_fee"],
                        "mcap": None, "side": None, "pair": names.get(r["pool"])})
    pr.entry_details({"_": pos})
    for p in pos:
        info = pr.pool_info(p["pool"])
        if p["mcap"] is None and info:  # no entry price: today's market cap
            p["mcap"] = (pr.tokens.get(info["c1"] if info["q"] == 0 else info["c0"]) or {}).get("mcap")
    # out of range at the close: the last withdrawal came back all in one token
    last = {}
    ids = [p["token_id"] for p in pos if p["close_ts"]]
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in con.execute(f"""SELECT token_id, a0, a1, u0, u1 FROM events WHERE v_state=1 AND CAST(delta AS REAL) < 0
                                 AND token_id IN ({','.join('?' * len(part))}) ORDER BY block, log_index""", part):
            last[r[0]] = r
    for p in pos:
        e, info = last.get(p["token_id"]), pr.pool_info(p["pool"])
        if not e or not info or None in (e[1], e[2], e[3], e[4]):
            continue
        q_ = info["q"]
        v = (e[1] * e[3], e[2] * e[4])
        tot = v[0] + v[1]
        if tot > 0:  # all quote back: the token ran above the range; all token back: it fell below
            p["oor"] = "above" if v[1 - q_] < pricing.SIDE_DUST * tot else \
                "below" if v[q_] < pricing.SIDE_DUST * tot else None
    return pos, skipped


def first_seen(con, addrs):
    return con.execute(f"SELECT MIN(open_ts) FROM positions WHERE owner IN ({','.join('?' * len(addrs))})",
                       addrs).fetchone()[0]
