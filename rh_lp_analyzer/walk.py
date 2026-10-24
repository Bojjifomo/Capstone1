"""Finding a wallet's positions with eth_call only: no eth_getLogs over big
ranges, so it works on any archive RPC (Alchemy's free tier included).

Position NFT ids only go up. Reading PositionManager.nextTokenId() at blocks
spread over a stretch tells which ids were minted between two of those blocks,
and one Multicall3 of ownerOf() at the later block says who got each (300 ids a
request). For the wallet's own ids, a binary search on ownerOf() finds the
exact mint block (and, when it no longer holds the id, the block it closed or
moved it), and a one-block eth_getLogs there gives the transaction. Liquidity
added or removed in between shows up as a step in getPositionLiquidity(), found
the same way.

Who-got-which-id is kept (table owners), so every wallet read after the first
reuses it. A position opened and closed between two sample blocks (shorter than
SAMPLE_BLOCKS, about 2.5 minutes on short stretches) can't be seen this way.
"""
import math

import chain as C
import config
from keccak import selector
from rpc import RPCError

SEL_NEXT_ID = selector("nextTokenId()")
SEL_OWNER_OF = selector("ownerOf(uint256)")
SEL_LIQUIDITY = selector("getPositionLiquidity(uint256)")
SAMPLE_BLOCKS = 1500  # ~2.5 min at 0.1 s a block: the finest the id → block map gets
MAX_SAMPLES = 2000  # longer stretches get coarser samples instead of more requests
MAX_TOKENS = 600_000  # ids minted in a stretch past which this costs too much (use the other sources)
PER_CALL = 300  # ownerOf reads per Multicall3

SCHEMA = """
CREATE TABLE IF NOT EXISTS walk_samples (block INTEGER PRIMARY KEY, next_id INTEGER);
-- each id minted in a walked stretch: its owner at seen_block, shortly after it was minted
-- (after_block < mint block <= seen_block); '' = gone already
CREATE TABLE IF NOT EXISTS owners (token_id INTEGER PRIMARY KEY, owner TEXT, after_block INTEGER, seen_block INTEGER);
CREATE INDEX IF NOT EXISTS owners_owner ON owners(owner);
"""


class TooMany(RPCError):
    """More ids minted in the stretch than walking it is worth."""


def _reverted(e):
    return isinstance(e, RPCError) and any(w in str(e).lower() for w in ("revert", "not_minted", "invalid token"))


class TokenWalk:
    def __init__(self, con, rpc, progress=lambda text, frac=None: None, missing=None, mark=None):
        self.con, self.rpc, self.progress = con, rpc, progress
        self.missing, self.mark = missing, mark
        con.executescript(SCHEMA)

    # ---- raw reads ---------------------------------------------------------
    def calls(self, items, size=25):
        """[(to, calldata, block)] -> [hex result, or None when it reverted]."""
        res = self.rpc.batch([("eth_call", [{"to": t, "data": d}, hex(b)]) for t, d, b in items], size=size)
        out = []
        for r in res:
            if isinstance(r, Exception):
                if not _reverted(r):
                    raise r  # quota, network: not an answer
                out.append(None)
            else:
                out.append(r if r and r != "0x" else None)
        return out

    def next_ids(self, blocks):
        blocks = sorted(set(blocks))
        have = dict(self.con.execute(f"SELECT block, next_id FROM walk_samples WHERE block IN "
                                     f"({','.join('?' * len(blocks))})", blocks).fetchall()) if blocks else {}
        need = [b for b in blocks if b not in have]
        for i in range(0, len(need), 500):
            part = need[i:i + 500]
            res = self.calls([(config.POSITION_MANAGER, "0x" + SEL_NEXT_ID, b) for b in part])
            rows = [(b, int(r, 16)) for b, r in zip(part, res) if r]
            self.con.executemany("INSERT OR IGNORE INTO walk_samples VALUES(?,?)", rows)
            have.update(rows)
        return have

    def owners_at(self, groups):
        """{block: [token ids]} -> {token id: owner or None}, one Multicall3 per 300 ids."""
        jobs = []
        for b, ids in groups.items():
            for i in range(0, len(ids), PER_CALL):
                jobs.append((b, ids[i:i + PER_CALL]))
        out = {}
        for i in range(0, len(jobs), 40):
            part = jobs[i:i + 40]
            self.progress(f"Reading who got which position NFT… {i:,}/{len(jobs):,} requests", i / max(1, len(jobs)))
            res = self.calls([(config.MULTICALL3, C.encode_aggregate3(
                [(config.POSITION_MANAGER, "0x" + SEL_OWNER_OF + C.word(t)) for t in ids]), b) for b, ids in part],
                size=5)
            for (b, ids), r in zip(part, res):
                if r is None:
                    raise RPCError("Multicall3 ownerOf read failed")
                for t, o in zip(ids, C.decode_aggregate3(r)):
                    out[t] = ("0x" + o[-40:]).lower() if o else None
        return out

    # ---- who got which id ----------------------------------------------------
    def fill(self, lo, hi):
        """Read the owner of every id minted in [lo, hi] (the parts not read yet)."""
        for a, b in self.missing(self.con, "walk", lo, hi):
            span = b - a + 1
            k = max(1, min(MAX_SAMPLES, math.ceil(span / SAMPLE_BLOCKS)))
            samples = sorted({a - 1 + round(span * i / k) for i in range(k + 1)})
            self.progress(f"Mapping position ids to blocks ({len(samples):,} samples)…")
            ends = self.next_ids([samples[0], samples[-1]])
            if len(ends) < 2:
                raise RPCError("nextTokenId() can't be read at these blocks")
            if ends[samples[-1]] - ends[samples[0]] > MAX_TOKENS:
                raise TooMany(f"{ends[samples[-1]] - ends[samples[0]]:,} positions were opened in this stretch: "
                              "too many to check one by one")
            nxt = self.next_ids(samples)
            groups, spans = {}, []
            for s0, s1 in zip(samples, samples[1:]):
                if s0 in nxt and s1 in nxt and nxt[s1] > nxt[s0]:
                    groups[s1] = list(range(nxt[s0], nxt[s1]))
                    spans.append((s0, s1))
            owners = self.owners_at(groups)
            rows = []
            for s0, s1 in spans:
                rows += [(t, owners.get(t) or "", s0, s1) for t in groups[s1]]
            self.con.executemany("INSERT OR REPLACE INTO owners VALUES(?,?,?,?)", rows)
            self.mark(self.con, "walk", a, b)
            self.con.commit()

    # ---- exact blocks ----------------------------------------------------------
    def first_block(self, items, read, pred):
        """Batched binary search: for each (key, lo, hi) with pred false at lo and true
        at hi, the first block where pred(read result) holds. read(key, block) ->
        (to, calldata). -> {key: block}"""
        live = {k: [lo, hi] for k, lo, hi in items}
        while True:
            todo = [(k, (lh[0] + lh[1]) // 2) for k, lh in live.items() if lh[1] - lh[0] > 1]
            if not todo:
                return {k: lh[1] for k, lh in live.items()}
            res = self.calls([(*read(k, m), m) for k, m in todo])
            for (k, m), r in zip(todo, res):
                if pred(k, r):
                    live[k][1] = m
                else:
                    live[k][0] = m

    def owner_read(self, tid, block):
        return config.POSITION_MANAGER, "0x" + SEL_OWNER_OF + C.word(tid)

    def liquidity_read(self, tid, block):
        return config.POSITION_MANAGER, "0x" + SEL_LIQUIDITY + C.word(tid)

    def one_block_logs(self, hits, flt):
        """eth_getLogs for exactly one block each: [(block, topics)] -> logs."""
        res = self.rpc.batch([("eth_getLogs", [dict(flt, topics=t, fromBlock=hex(b), toBlock=hex(b))])
                              for b, t in hits], size=20)
        logs = []
        for r in res:
            if isinstance(r, Exception):
                raise r
            logs += r or []
        return logs

    def nft_logs(self, hits):
        """[(token id, block)] -> that id's NFT Transfer logs in that block."""
        return self.one_block_logs([(b, [C.TRANSFER, None, None, "0x" + C.word(t)]) for t, b in hits],
                                   {"address": config.POSITION_MANAGER})

    def wallet(self, addr, lo, hi, known_open):
        """The wallet's NFT Transfer logs in [lo, hi]: ids minted to it there, and
        known_open ids (it held them at lo - 1) that left it by hi."""
        self.fill(lo, hi)
        mine = self.con.execute("SELECT token_id, after_block, seen_block FROM owners WHERE owner=? AND seen_block "
                                "BETWEEN ? AND ?", (addr, lo, hi)).fetchall()
        is_mine = lambda t, r: r is not None  # noqa: E731 - exists = minted by then
        mints = self.first_block([(t, max(a, lo - 1), s) for t, a, s in mine], self.owner_read, is_mine)
        # who still holds what at hi: ids it got here, and ids it held before the stretch
        held = {t: b for t, b in mints.items()}
        held.update({t: lo - 1 for t in known_open if t not in held})
        now = {}
        ids = list(held)
        for i in range(0, len(ids), PER_CALL):
            now.update(self.owners_at({hi: ids[i:i + PER_CALL]}))
        gone = [(t, held[t], hi) for t in ids if now.get(t) != addr]
        left = self.first_block(gone, self.owner_read, lambda t, r: r is None or ("0x" + r[-40:]).lower() != addr)
        self.progress(f"Reading {len(mints) + len(left):,} position opens / closes…")
        return self.nft_logs(list(mints.items()) + list(left.items()))

    def liquidity_steps(self, spans):
        """Blocks where a position's liquidity changed between known events:
        spans [(token id, block a, block b)] with liquidity known after a and before b.
        -> [(token id, block)]"""
        found = []
        todo = list(spans)
        while todo:
            ends = self.calls([(*self.liquidity_read(t, 0), b) for t, a, b in todo] +
                              [(*self.liquidity_read(t, 0), a) for t, a, b in todo])
            n = len(todo)
            nxt = []
            for (t, a, b), lb, la in zip(todo, ends[:n], ends[n:]):
                if (int(la, 16) if la else 0) == (int(lb, 16) if lb else 0):
                    continue  # nothing changed in between (or it came back to the same amount)
                if b - a <= 1:
                    found.append((t, b))
                    continue
                m = (a + b) // 2
                nxt += [(t, a, m), (t, m, b)]
            todo = nxt
            if len(found) > 2000:
                break
        return found
