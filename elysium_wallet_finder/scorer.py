"""Scoring: prices every LP event of candidate wallets, then ranks wallets.

Each event is priced from chain state one block before it (archive eth_call
through Multicall3, many per JSON-RPC batch):
  - StateView.getSlot0          pool price, LP fee
  - StateView.getPositionInfo   liquidity and fee checkpoints before the event
  - StateView.getFeeGrowthInside
  - getSlot0 of the ETH/USDG pools for the ETH price
Token amounts come from the liquidity delta and the price; fees are what the
position earned since its last checkpoint. A pool needs USDG or ETH on one
side to be priced; other pools are skipped.
"""
import json
import statistics
import time

import chain as C
import config
import metrics as M
from db import get_meta, set_meta
from indexer import multicall

# Pricing order, so the RPC goes where the board needs it:
#   1. every wallet's newest closed trades, SAMPLE positions over 3+ trades (a
#      whole ladder counts as one trade; at most SAMPLE_MAX positions): enough
#      to see whether the wallet is worth more,
#   2. the whole window of promising wallets (labelled ones, or a sample that
#      lost under 1% at a real size), best first, 200 / 1000 / all deep,
#   3. bots' newest BOT_DEPTH,
#   4. older history, only for wallets the board rates Decent or better.
# (A flat cap of 500 per wallet left the busiest ladder LPs, 1,000-6,000
# positions a month, measured over their last few days only.)
PER_WALLET = 20_000
SAMPLE = 20
SAMPLE_MAX = 150
DEPTHS = (200, 1000, PER_WALLET)
PROMISE_MIN_TRADES = 3     # priced closed trades a sample needs before it can say "promising"
PROMISE_MIN_SIZE = 50      # USD: average deposit per trade of a wallet worth pricing in full
PROMISE_MAX_LOSS = 0.01    # ...and its sample lost less than this share of what went in (untouched
                           # ladder ranges end flat, so "made money" alone would be a coin flip)
HISTORY_MIN_SCORE = 50     # Decent and up get their older history priced
# Reads that only refresh what is already there run this often, not every pass
OPEN_EVERY_S = 30 * 60     # open positions' value (ranked wallets only)
SLOW_EVERY_S = 60 * 60     # wallet balances and token prices
EVENTS_PER_CALL = 40  # position events of one block read in one eth_call
LAUNCH_SHARE = 0.02  # depositing >= 2% of a token's supply = launching it, not LPing it
BOT_DEPTH = 20    # bots (hold < 5 min or > 100 trades a day) get only this many, after everyone else
SIDE_DUST = 0.01  # under 1% of the deposit on one side = single-sided
# A pool drained of one token can sit at an absurd price (1e50 $/token): fees or
# leftovers in that token would read as trillions. Prices past these are unpriceable.
MAX_TOKEN_USD = 1e7
MAX_MCAP_USD = 1e10
# ...and a position paying back over 1000x its deposit (and $10k+ more) is one too
MAX_RETURN = 1000


def sample(rows):
    """The positions of a wallet's newest closed trades (rows: closed first, latest
    close first): SAMPLE+ positions over 3+ trades, at most SAMPLE_MAX positions."""
    out, trades = [], set()
    for r in rows:
        if not r[1] or len(out) >= SAMPLE_MAX:
            break
        key = (r[3], r[4])
        if key not in trades:
            if len(out) >= SAMPLE and len(trades) >= PROMISE_MIN_TRADES:
                break
            trades.add(key)
        out.append(r)
    return out


def quote_side(c0, c1):
    """(index of the quote token, 'usd'|'eth') or (None, None)."""
    if c1 in config.STABLES:
        return 1, "usd"
    if c0 in config.STABLES:
        return 0, "usd"
    if c1 in config.ETH_LIKE:
        return 1, "eth"
    if c0 in config.ETH_LIKE:
        return 0, "eth"
    return None, None


def pos_keys(ids):
    """1 or 'v4:1' -> 'v4:1'. A key that already names its venue is kept."""
    out = []
    for i in ids:
        s = str(i)
        out.append(s if ":" in s else f"v4:{s}")
    return out


def hype_calls():
    """Oracle reads prepended to every archive batch. v3 pools use slot0();
    a 32-byte v4 pool id would use StateView.getSlot0."""
    out = []
    for src in config.HYPE_USD_SOURCES:
        if src["kind"] == "v3":
            out.append((src["pool"], C.cd_v3_slot0()))
        else:
            out.append((config.STATE_VIEW, C.cd_slot0(src["pool"])))
    return out


def hype_usd(results):
    """USD per 1 HYPE from the reads hype_calls() built, or None.

    The listed pools are USDC/WHYPE with USDC as token0, so price1_per0 is
    WHYPE per USDC and the dollar price is its reciprocal."""
    for src, ret in zip(config.HYPE_USD_SOURCES, results):
        if not ret:
            continue
        s = C.decode_v3_slot0(ret) if src["kind"] == "v3" else C.decode_slot0(ret)
        if not s or not s["sqrtp"]:
            continue
        per_usdc = C.price1_per0(s["sqrtp"], src["dec0"], src["dec1"])
        if per_usdc:
            return 1.0 / per_usdc
    return None


def eth_usd(res_slots):
    """Back-compat name: the gas-token price. Prefers the HYPE/USDC shape,
    and still understands an ETH-as-token0 18/6 pool (the old layout)."""
    got = hype_usd(res_slots)
    if got:
        return got
    for r in res_slots:
        s = C.decode_slot0(r) if r else None
        if s and s["sqrtp"]:
            return C.price1_per0(s["sqrtp"], 18, 6)
    return None


def usd_prices(sqrtp, dec0, dec1, q, kind, eth, supply=None):
    """USD per whole token0 and token1 at this pool price, or None when the
    token side's price is past belief (see MAX_TOKEN_USD, MAX_MCAP_USD)."""
    p = C.price1_per0(sqrtp, dec0, dec1)  # token0 in token1
    if not p:
        return None
    qusd = 1.0 if kind == "usd" else eth
    if qusd is None:
        return None
    out = (p * qusd, qusd) if q == 1 else (qusd, qusd / p)
    tok = out[1 - q]
    if tok > MAX_TOKEN_USD or (supply and tok * supply > MAX_MCAP_USD):
        return None
    return out


class Scorer:
    def __init__(self, con, rpc, cfg, log=print):
        self.con, self.rpc, self.cfg, self.log = con, rpc, cfg, log
        self.pools = {r["id"]: dict(r) for r in con.execute("SELECT * FROM pools")}
        self.tokens = {r["addr"]: dict(r) for r in con.execute("SELECT * FROM tokens")}
        self.slip = float(cfg["SELL_SLIPPAGE_PCT"]) / 100
        self.multiday_s = float(cfg.get("MULTIDAY_HOURS") or 2) * 3600

    def pool_info(self, pid):
        p = self.pools.get(pid)
        if not p:
            return None
        q, kind = quote_side(p["c0"], p["c1"])
        d0 = (self.tokens.get(p["c0"]) or {}).get("decimals")
        d1 = (self.tokens.get(p["c1"]) or {}).get("decimals")
        if q is None or d0 is None or d1 is None:
            return None
        supply = (self.tokens.get(p["c1"] if q == 0 else p["c0"]) or {}).get("supply")
        return {**p, "q": q, "kind": kind, "d0": d0, "d1": d1, "supply": supply}

    # ---- pricing events --------------------------------------------------
    def value_events(self, token_ids):
        """Price every unpriced event of these positions. Events in one block share
        one archive eth_call (a 7-range ladder opened or closed together = 1 call,
        not 7): the pool prices once, then each position's fee checkpoint, which a
        position opened in that very block doesn't have yet."""
        ids = pos_keys(token_ids)
        groups = {}
        opened = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            for r in self.con.execute(
                    f"""SELECT e.block, e.log_index, e.pos_key, e.token_id, e.venue, e.kind, e.pool, e.tl, e.tu,
                               e.delta, e.r0, e.r1, e.salt, p.open_block
                        FROM events e LEFT JOIN positions p ON p.pos_key = e.pos_key
                        WHERE e.pos_key IN ({q}) AND e.v_state=0 ORDER BY e.block, e.log_index""", part):
                groups.setdefault((r["pos_key"], r["block"]), []).append(dict(r))
                opened[r["pos_key"]] = r["open_block"]
        if not groups:
            return 0
        v3_groups = {k: v for k, v in groups.items() if v[0].get("venue") in config.V3_NPM}
        for k in v3_groups:
            del groups[k]
        n_v3 = self._value_v3(v3_groups) if v3_groups else 0
        if not groups:
            return n_v3
        by_block = {}
        for key in groups:
            by_block.setdefault(key[1], []).append(key)
        calls, plan = [], []  # plan: per call, (keys, pools, {key: (posinfo index, feegrowth index) or None})
        oracle = hype_calls()
        for blk, keys in by_block.items():
            for j in range(0, len(keys), EVENTS_PER_CALL):
                part = keys[j:j + EVENTS_PER_CALL]
                pools = list(dict.fromkeys(groups[k][0]["pool"] for k in part))
                sub = list(oracle) + [(config.STATE_VIEW, C.cd_slot0(p)) for p in pools]
                where = {}
                for k in part:
                    e = groups[k][0]
                    if opened.get(k[0]) == blk:
                        where[k] = None  # minted in this block: nothing earned yet
                        continue
                    where[k] = (len(sub), len(sub) + 1)
                    pm = e["venue"] if (e.get("venue") or "").startswith("0x") else config.POSITION_MANAGER
                    salt = int(e["salt"] or e["token_id"])
                    sub += [(config.STATE_VIEW, C.cd_position_info(e["pool"], pm, e["tl"], e["tu"], salt)),
                            (config.STATE_VIEW, C.cd_fee_growth_inside(e["pool"], e["tl"], e["tu"]))]
                calls.append(("eth_call", [{"to": config.MULTICALL3, "data": C.encode_aggregate3(sub)},
                                           hex(blk - 1)]))
                plan.append((part, pools, where))
        self.log(f"    {len(calls):,} archive reads in flight")
        results = self.rpc.batch(calls, size=10)  # replies carry many reads each: keep a batch's reply small
        updates = []
        failed = 0
        first_err = None
        blank = lambda e: (2, None, None, None, None, None, None, None, e["block"], e["log_index"])  # noqa: E731
        for (part, pools, where), ret in zip(plan, results):
            if isinstance(ret, Exception) or not ret:
                failed += len(part)
                first_err = first_err or ret
                continue
            try:
                res = C.decode_aggregate3(ret)
            except (ValueError, IndexError):
                failed += len(part)
                continue
            eth = hype_usd(res[:len(oracle)])
            slots = dict(zip(pools, res[len(oracle):len(oracle) + len(pools)]))
            for key in part:
                evs = groups[key]
                pinfo = self.pool_info(evs[0]["pool"])
                s0 = slots.get(evs[0]["pool"])
                slot = C.decode_slot0(s0) if s0 else None
                prices = usd_prices(slot["sqrtp"], pinfo["d0"], pinfo["d1"], pinfo["q"], pinfo["kind"], eth,
                                    pinfo["supply"]) if pinfo and slot and slot["sqrtp"] else None
                if not prices:
                    updates += [blank(e) for e in evs]
                    continue
                u0, u1 = prices
                f0 = f1 = 0.0
                at = where[key]
                pos, fg = (res[at[0]], res[at[1]]) if at else (None, None)
                if pos and fg:
                    pi = C.decode_position_info(pos)
                    g0, g1 = C.decode_fee_growth(fg)
                    f0 = C.fees_owed(pi["liq"], g0, pi["fg0"]) / 10 ** pinfo["d0"]
                    f1 = C.fees_owed(pi["liq"], g1, pi["fg1"]) / 10 ** pinfo["d1"]
                for j, e in enumerate(evs):
                    a0, a1 = C.amounts_for_liquidity(int(e["delta"]), slot["sqrtp"], e["tl"], e["tu"])
                    updates.append((1, a0 / 10 ** pinfo["d0"], a1 / 10 ** pinfo["d1"],
                                    f0 if j == 0 else 0.0, f1 if j == 0 else 0.0, u0, u1, slot["lp_fee"],
                                    e["block"], e["log_index"]))
        self.con.executemany("UPDATE events SET v_state=?, a0=?, a1=?, f0=?, f1=?, u0=?, u1=?, lp_fee=? "
                             "WHERE block=? AND log_index=?", updates)
        if failed == len(groups):
            hint = ""
            if any(w in str(first_err).lower() for w in ("trie", "state", "archive", "pruned", "historical")):
                hint = " Your RPC doesn't seem to serve old block state (archive), which scoring needs."
            self.log(f"  all {failed} price reads failed: {first_err}.{hint}")
        elif failed:
            self.log(f"  {failed} price reads failed (will retry next run)")
        return len(calls) + n_v3

    def _value_v3(self, groups):
        """Price v3 events from the amounts in the logs plus one slot0 per pool.

        Principal vs fee comes from split_v3 (a collect pays back owed principal
        first). No StateView position read: the NPM already logged the amounts,
        which is most of the archive calls a v4 position would have spent."""
        # full history, so a collect can be matched to an earlier decrease
        keys = list({k[0] for k in groups})
        hist = {k: [] for k in keys}
        for i in range(0, len(keys), 400):
            part = keys[i:i + 400]
            q = ",".join("?" * len(part))
            for r in self.con.execute(
                    f"""SELECT pos_key, block, log_index, kind, r0, r1, v_state FROM events
                        WHERE pos_key IN ({q}) ORDER BY block, log_index""", part):
                hist[r["pos_key"]].append(dict(r))
        split_at = {}  # (pos_key, block, log_index) -> {a0,a1,f0,f1} raw
        for key, evs in hist.items():
            parsed = [{"kind": e["kind"], "amount0": int(e["r0"] or 0), "amount1": int(e["r1"] or 0)} for e in evs]
            for e, part in zip(evs, C.split_v3(parsed)):
                split_at[(key, e["block"], e["log_index"])] = part
        by_block = {}
        for key in groups:
            by_block.setdefault(key[1], []).append(key)
        oracle = hype_calls()
        calls, plan = [], []
        for blk, ks in by_block.items():
            for j in range(0, len(ks), EVENTS_PER_CALL):
                part = ks[j:j + EVENTS_PER_CALL]
                pools = list(dict.fromkeys(groups[k][0]["pool"] for k in part if groups[k][0].get("pool")))
                sub = list(oracle) + [(p, C.cd_v3_slot0()) for p in pools]
                calls.append(("eth_call", [{"to": config.MULTICALL3, "data": C.encode_aggregate3(sub)}, hex(blk - 1)]))
                plan.append((part, pools))
        if not calls:
            return 0
        results = self.rpc.batch(calls, size=10)
        updates, failed = [], 0
        for (part, pools), ret in zip(plan, results):
            if isinstance(ret, Exception) or not ret:
                failed += len(part)
                continue
            try:
                res = C.decode_aggregate3(ret)
            except (ValueError, IndexError):
                failed += len(part)
                continue
            eth = hype_usd(res[:len(oracle)])
            slots = dict(zip(pools, res[len(oracle):len(oracle) + len(pools)]))
            for key in part:
                evs = groups[key]
                pinfo = self.pool_info(evs[0]["pool"]) if evs[0].get("pool") else None
                slot = C.decode_v3_slot0(slots.get(evs[0]["pool"])) if evs[0].get("pool") else None
                prices = usd_prices(slot["sqrtp"], pinfo["d0"], pinfo["d1"], pinfo["q"], pinfo["kind"], eth,
                                    pinfo["supply"]) if pinfo and slot else None
                fee_ppm = (pinfo or {}).get("fee") or 0
                for e in evs:
                    if not prices:
                        updates.append((2, None, None, None, None, None, None, None, e["block"], e["log_index"]))
                        continue
                    u0, u1 = prices
                    sp = split_at.get((e["pos_key"], e["block"], e["log_index"])) or {"a0": 0, "a1": 0, "f0": 0, "f1": 0}
                    d0, d1 = 10 ** pinfo["d0"], 10 ** pinfo["d1"]
                    updates.append((1, sp["a0"] / d0, sp["a1"] / d1, sp["f0"] / d0, sp["f1"] / d1,
                                    u0, u1, fee_ppm, e["block"], e["log_index"]))
        self.con.executemany("UPDATE events SET v_state=?, a0=?, a1=?, f0=?, f1=?, u0=?, u1=?, lp_fee=? "
                             "WHERE block=? AND log_index=?", updates)
        if failed:
            self.log(f"  {failed} v3 price reads failed (will retry next run)")
        return len(calls)

    def value_open(self, token_ids, block="latest"):
        """Value (+ unclaimed fees) of open positions at `block` -> {pos_key: (usd, token_side_usd)}.

        `block` is the last block we indexed: reading at the real chain head
        could see a close we haven't loaded yet and value it at $0. v3 positions
        are the liquidity at slot0 plus tokensOwed (fees not yet checkpointed
        on the NFT are missing until the next collect)."""
        rows = []
        ids = pos_keys(token_ids)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            rows += [dict(r) for r in self.con.execute(
                f"SELECT pos_key, token_id, venue, pool, tl, tu FROM positions WHERE pos_key IN ({q})", part)]
        out = {}
        v4 = [r for r in rows if r["venue"] not in config.V3_NPM]
        v3 = [r for r in rows if r["venue"] in config.V3_NPM]
        oracle = hype_calls()
        for i in range(0, len(v4), 80):
            part = v4[i:i + 80]
            calls = list(oracle)
            for r in part:
                pm = r["venue"] if (r["venue"] or "").startswith("0x") else config.POSITION_MANAGER
                calls += [(config.STATE_VIEW, C.cd_slot0(r["pool"])),
                          (config.STATE_VIEW, C.cd_position_info(r["pool"], pm, r["tl"], r["tu"], r["token_id"])),
                          (config.STATE_VIEW, C.cd_fee_growth_inside(r["pool"], r["tl"], r["tu"]))]
            try:
                res = multicall(self.rpc, calls, block)
            except Exception as e:  # noqa: BLE001 - keep scoring the rest
                self.log(f"  open value read failed: {e}")
                continue
            eth = hype_usd(res[:len(oracle)])
            base = len(oracle)
            for j, r in enumerate(part):
                s0, pos, fg = res[base + 3 * j:base + 3 * j + 3]
                pinfo = self.pool_info(r["pool"])
                slot = C.decode_slot0(s0) if s0 else None
                if not (pinfo and slot and slot["sqrtp"] and pos and fg):
                    continue
                prices = usd_prices(slot["sqrtp"], pinfo["d0"], pinfo["d1"], pinfo["q"], pinfo["kind"], eth,
                                    pinfo["supply"])
                if not prices:
                    continue
                pi = C.decode_position_info(pos)
                g0, g1 = C.decode_fee_growth(fg)
                a0, a1 = C.amounts_for_liquidity(pi["liq"], slot["sqrtp"], r["tl"], r["tu"])
                a0 = a0 / 10 ** pinfo["d0"] + C.fees_owed(pi["liq"], g0, pi["fg0"]) / 10 ** pinfo["d0"]
                a1 = a1 / 10 ** pinfo["d1"] + C.fees_owed(pi["liq"], g1, pi["fg1"]) / 10 ** pinfo["d1"]
                tok = 1 - pinfo["q"]
                out[r["pos_key"]] = (a0 * prices[0] + a1 * prices[1], (a0, a1)[tok] * prices[tok])
        for i in range(0, len(v3), 60):
            part = v3[i:i + 60]
            calls = list(oracle)
            for r in part:
                calls += [(r["pool"], C.cd_v3_slot0()), (r["venue"], C.cd_v3_positions(r["token_id"]))]
            try:
                res = multicall(self.rpc, calls, block)
            except Exception as e:  # noqa: BLE001
                self.log(f"  v3 open value read failed: {e}")
                continue
            eth = hype_usd(res[:len(oracle)])
            base = len(oracle)
            for j, r in enumerate(part):
                s0, pos = res[base + 2 * j:base + 2 * j + 2]
                pinfo = self.pool_info(r["pool"])
                slot = C.decode_v3_slot0(s0)
                info = C.decode_v3_position(pos)
                if not (pinfo and slot and info):
                    continue
                prices = usd_prices(slot["sqrtp"], pinfo["d0"], pinfo["d1"], pinfo["q"], pinfo["kind"], eth,
                                    pinfo["supply"])
                if not prices:
                    continue
                a0, a1 = C.amounts_for_liquidity(info["liq"], slot["sqrtp"], r["tl"], r["tu"])
                a0 = a0 / 10 ** pinfo["d0"] + info["owed0"] / 10 ** pinfo["d0"]
                a1 = a1 / 10 ** pinfo["d1"] + info["owed1"] / 10 ** pinfo["d1"]
                tok = 1 - pinfo["q"]
                out[r["pos_key"]] = (a0 * prices[0] + a1 * prices[1], (a0, a1)[tok] * prices[tok])
        return out

    def aggregate(self, token_ids, open_values):
        """Roll priced events up into each position's PnL."""
        ids = pos_keys(token_ids)
        rows = []
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            pos = {r["pos_key"]: dict(r) for r in self.con.execute(
                f"SELECT pos_key, token_id, pool, close_ts, minted FROM positions WHERE pos_key IN ({q})", part)}
            evs = {}
            for r in self.con.execute(f"SELECT * FROM events WHERE pos_key IN ({q}) "
                                      f"ORDER BY block, log_index", part):
                evs.setdefault(r["pos_key"], []).append(dict(r))
            for tid in part:
                p, e = pos.get(tid), evs.get(tid, [])
                if not p or not e or not p["minted"]:
                    continue
                states = {x["v_state"] for x in e}
                if 0 in states:
                    continue  # not priced yet
                pinfo = self.pool_info(p["pool"])
                if 2 in states or not pinfo:
                    rows.append((2, None, None, None, None, None, None, None, None, None, None, 0, tid))
                    continue
                tok = 1 - pinfo["q"]
                dep = wd = fee = tok_out = 0.0
                for x in e:
                    a, u, f = (x["a0"], x["a1"]), (x["u0"], x["u1"]), (x["f0"], x["f1"])
                    val = a[0] * u[0] + a[1] * u[1]
                    kind = x["kind"] or "v4"
                    if kind == "inc" or (kind == "v4" and int(x["delta"] or 0) > 0):
                        dep += val
                    elif kind == "col" or (kind == "v4" and int(x["delta"] or 0) < 0):
                        wd += val
                        tok_out += a[tok] * u[tok]
                    fee += f[0] * u[0] + f[1] * u[1]
                    tok_out += f[tok] * u[tok]
                now = 0.0
                if p["close_ts"] is None:
                    if tid not in open_values:
                        continue  # couldn't read its current value this run
                    now, tok_now = open_values[tid]
                    tok_out += tok_now
                tok_in = sum((x["a1"] if tok else x["a0"]) for x in e if int(x["delta"]) > 0)
                supply = (self.tokens.get(pinfo["c1"] if tok else pinfo["c0"]) or {}).get("supply")
                launch = 1 if supply and tok_in >= LAUNCH_SHARE * supply else 0
                first_open = next((x for x in e if int(x["delta"]) > 0), None)
                single = 1 if first_open and (first_open["a0"] < 1e-12 or first_open["a1"] < 1e-12) else 0
                # the fee the pool charged at the last event (0 is a real fee); the
                # PoolKey fee is only a fallback, and 0x800000 there just means "dynamic"
                fee_ppm = e[-1]["lp_fee"]
                if fee_ppm is None:
                    fee_ppm = pinfo["fee"] if pinfo["fee"] < 1_000_000 else 0
                sell_fee = min(fee_ppm, 1_000_000) / 1e6 + self.slip
                pnl = wd + fee + now - dep
                if wd + fee + now > MAX_RETURN * dep and pnl > 1e4:
                    rows.append((2, None, None, None, None, None, None, None, None, None, None, 0, tid))
                    continue
                rows.append((1, dep, wd, fee, now, tok_out, pnl, tok_out * sell_fee,
                             1 if pinfo["kind"] == "eth" else 0, single, sell_fee, launch, tid))
        self.con.executemany("""UPDATE positions SET valued=?, dep_usd=?, wd_usd=?, fee_usd=?, now_usd=?,
                                tok_out_usd=?, pnl_usd=?, cost_usd=?, eth_pair=?, single=?, sell_fee=?,
                                launch=? WHERE pos_key=?""", rows)
        return len(rows)

    # ---- token prices, balances -----------------------------------------
    def refresh_tokens(self, pool_ids):
        """Current USD price and market cap of the non-quote token of each pool."""
        pinfos = [p for p in (self.pool_info(pid) for pid in pool_ids) if p]
        best = {}
        for p in pinfos:  # one pool per token: the one listed first wins
            best.setdefault(p["c1"] if p["q"] == 0 else p["c0"], p)
        items = list(best.items())
        now = int(time.time())
        for i in range(0, len(items), 200):
            part = items[i:i + 200]
            calls = list(hype_calls())
            calls += [(config.STATE_VIEW, C.cd_slot0(p["id"])) if len(p["id"]) == 66
                      else (p["id"], C.cd_v3_slot0()) for _, p in part]
            try:
                res = multicall(self.rpc, calls, "latest")
            except Exception as e:  # noqa: BLE001
                self.log(f"  token price read failed: {e}")
                continue
            eth = hype_usd(res[:len(hype_calls())])
            rows = []
            for (tok, p), r in zip(part, res[2:]):
                s = C.decode_slot0(r) if r else None
                if not s or not s["sqrtp"]:
                    continue
                pr = usd_prices(s["sqrtp"], p["d0"], p["d1"], p["q"], p["kind"], eth, p["supply"])
                if not pr:
                    continue
                price = pr[1 - p["q"]]
                supply = (self.tokens.get(tok) or {}).get("supply")
                rows.append((price, price * supply if supply else None, now, tok))
            self.con.executemany("UPDATE tokens SET price_usd=?, mcap=?, updated=? WHERE addr=?", rows)
            for price, mc, _, tok in rows:
                self.tokens.setdefault(tok, {}).update(price_usd=price, mcap=mc)
        return eth if items else None

    def refresh_balances(self, wallets):
        now = int(time.time())
        rows = []
        for i in range(0, len(wallets), 150):
            part = wallets[i:i + 150]
            calls = []
            for w in part:
                calls += [(config.MULTICALL3, C.cd_simple(C.SEL_ETH_BALANCE, w)),
                          (config.USDC, C.cd_simple(C.SEL_BALANCE_OF, w))]
            try:
                res = multicall(self.rpc, calls, "latest")
            except Exception as e:  # noqa: BLE001
                self.log(f"  balance read failed: {e}")
                continue
            for j, w in enumerate(part):
                eb, ub = res[2 * j], res[2 * j + 1]
                rows.append((int(eb, 16) / 1e18 if eb else None, int(ub, 16) / 1e6 if ub else None, now, w))
        self.con.executemany("UPDATE wallets SET eth_bal=?, usd_bal=?, bal_ts=? WHERE addr=?", rows)

    def quote_hype(self):
        """USD per HYPE from the USDC/WHYPE pools, logged and stored before the
        big pricing batch. A failure here is why the header shows '–'."""
        try:
            res = multicall(self.rpc, hype_calls(), "latest")
            px = hype_usd(res)
        except Exception as e:  # noqa: BLE001
            msg = str(e)[:200]
            self.log(f"  HYPE price failed: {msg}")
            set_meta(self.con, "hype_error", msg)
            self.con.commit()
            return None
        if not px:
            msg = "USDC/WHYPE pool returned no price (the RPC may not be serving that call)"
            self.log(f"  HYPE price failed: {msg}")
            set_meta(self.con, "hype_error", msg)
            self.con.commit()
            return None
        set_meta(self.con, "eth_usd", px)
        set_meta(self.con, "hype_error", "")
        self.con.commit()
        self.log(f"  HYPE ${px:,.2f}")
        return px

    # ---- the run ---------------------------------------------------------
    def run(self, stop=lambda: False, budget=None):
        """One pass: price what matters most first, refresh values, rank.
        budget: RPC calls this pass may spend (None = no limit)."""
        con, cfg = self.con, self.cfg
        now = time.time()
        days = float(cfg["METRIC_DAYS"])
        w0 = now - days * 86400
        per_run = int(cfg["SCORE_POSITIONS_PER_RUN"])
        c0 = self.rpc.calls
        spent = {}
        left = lambda: None if budget is None else budget - (self.rpc.calls - c0)  # noqa: E731

        self.drop_spiked_prices()
        self.quote_hype()
        people, bots = self.candidates(now, w0)
        cands = people + bots
        bot_list, bots = bots, set(bots)
        # each wallet's positions in the window, closed ones first (latest close first),
        # then open ones: [(token_id, closed, valued, pool, open_block)]
        newest = {}
        for w in cands:
            newest[w] = con.execute("""
                SELECT pos_key, close_ts IS NOT NULL, valued, pool, open_block FROM positions
                WHERE owner=? AND minted=1 AND (close_ts IS NULL OR close_ts >= ?)
                ORDER BY close_ts IS NULL, COALESCE(close_ts, open_ts) DESC, open_block DESC LIMIT ?""",
                                    (w, w0, BOT_DEPTH if w in bots else PER_WALLET)).fetchall()
        deep = self.promising(people, w0)
        rounds = [[sample(newest[w]) for w in people]] + [[newest[w][:d] for w in deep] for d in DEPTHS] \
            + [[newest[w][:BOT_DEPTH] for w in bot_list]]
        todo, seen = [], set()
        for lists in rounds:
            for rows in lists:
                for tid, _, valued, *_ in rows:
                    if len(todo) >= per_run:
                        break
                    if valued == 0 and tid not in seen:
                        seen.add(tid)
                        todo.append(tid)
        wanted = {r[0] for lists in rounds for rows in lists for r in rows if r[2] == 0}
        # room left once the window is priced: older positions of the wallets that rate well
        older = []
        good = self.rated_well(people)
        if len(todo) < per_run and good:
            older = self.history_todo(good, w0, per_run - len(todo))
            todo += older
        self.log(f"  {len(cands):,} candidate wallets ({len(deep):,} priced in full, {len(bots):,} bots last), "
                 f"pricing {len(todo):,} positions" + (f" ({len(older):,} of them older history)" if older else "")
                 + (f", within {budget:,} RPC calls" if budget is not None else ""))

        done = set()
        c = self.rpc.calls
        for i in range(0, len(todo), 400):
            if stop() or (left() is not None and left() <= 0):
                break
            part = todo[i:i + 400]
            end = min(i + 400, len(todo))
            self.log(f"    pricing positions {i + 1:,}–{end:,} of {len(todo):,}…")
            n = self.value_events(part)
            done.update(part)
            con.commit()
            self.log(f"    priced {end:,}/{len(todo):,} positions ({n} archive reads)")
        spent["prices"] = self.rpc.calls - c

        # open positions: the ones just priced always (they need a value to count);
        # every OPEN_EVERY_S, all open positions of wallets on the board (values move)
        fresh = [r[0] for rows in newest.values() for r in rows if not r[1] and r[0] in done]
        refresh = []
        if self._due("open_values_at", OPEN_EVERY_S, now) and (left() is None or left() > 0):
            refresh = [r[0] for r in self._owner_chunks(self.on_board(), """SELECT pos_key FROM positions
                       WHERE owner IN ({q}) AND minted=1 AND valued=1 AND close_ts IS NULL""")]
            set_meta(con, "open_values_at", int(now))
        c = self.rpc.calls
        head = get_meta(con, "fwd_block")
        open_vals = self.value_open(list(dict.fromkeys(fresh + refresh)), head if head else "latest")
        spent["open values"] = self.rpc.calls - c
        self.aggregate(done | set(fresh) | set(refresh), open_vals)
        con.commit()

        slow = self._due("slow_refresh_at", SLOW_EVERY_S, now) and (left() is None or left() > 0)
        if slow:
            c = self.rpc.calls
            pool_ids = [r[0] for r in con.execute(
                "SELECT DISTINCT pool FROM positions WHERE valued=1 AND (close_ts IS NULL OR close_ts >= ?)", (w0,))]
            eth = self.refresh_tokens(pool_ids)
            if eth:
                set_meta(con, "eth_usd", eth)
            spent["token prices"] = self.rpc.calls - c
        c = self.rpc.calls
        n = self.rank(now, days, cands, balances=slow)
        if slow:
            spent["balances"] = self.rpc.calls - c
            set_meta(con, "slow_refresh_at", int(now))
        set_meta(con, "price_queue", len(wanted - done))  # still to price after this run
        set_meta(con, "history_queue", self.history_left(self.rated_well(people), w0))
        con.commit()
        total = self.rpc.calls - c0
        if total:
            self.log("  RPC calls this pass: " + " · ".join(f"{k} {v:,}" for k, v in spent.items() if v)
                     + f" (total {total:,})")
        return {"candidates": len(cands), "priced": len(done), "scored": n, "calls": total,
                "budget_hit": budget is not None and total >= budget}

    def _due(self, key, every, now):
        return now - (get_meta(self.con, key) or 0) >= every

    def promising(self, people, w0):
        """People worth pricing in full, best first: labelled ones, then those whose
        priced closes in the window (the sample, or more) lost under PROMISE_MAX_LOSS
        of what went in, over PROMISE_MIN_TRADES+ trades of PROMISE_MIN_SIZE+ each."""
        labelled = {r[0] for r in self.con.execute("SELECT addr FROM labels")}
        got = {r[0]: r[1:] for r in self._owner_chunks(people, """
            SELECT owner, COUNT(DISTINCT pool || ':' || open_block), SUM(pnl_usd), SUM(dep_usd) FROM positions
            WHERE owner IN ({q}) AND valued=1 AND launch=0 AND close_ts >= ? GROUP BY owner""", (w0,))}

        def ok(w):
            trades, pnl, dep = got[w]
            return trades >= PROMISE_MIN_TRADES and (dep or 0) / trades >= PROMISE_MIN_SIZE \
                and (pnl or 0) > -PROMISE_MAX_LOSS * (dep or 0)
        good = [w for w in people if w not in labelled and w in got and ok(w)]
        good.sort(key=lambda w: -(got[w][1] or 0))
        return [w for w in people if w in labelled] + good

    def on_board(self):
        return [r[0] for r in self.con.execute("SELECT addr FROM wallets WHERE score IS NOT NULL")]

    def rated_well(self, people):
        """People whose older history is worth pricing: labelled, or Decent and up."""
        ok = {r[0] for r in self.con.execute("SELECT addr FROM labels")} | {r[0] for r in self.con.execute(
            "SELECT addr FROM wallets WHERE score >= ?", (HISTORY_MIN_SCORE,))}
        return [w for w in people if w in ok]

    def _owner_chunks(self, wallets, sql, args=()):
        for i in range(0, len(wallets), 400):
            part = wallets[i:i + 400]
            yield from self.con.execute(sql.format(q=",".join("?" * len(part))), (*part, *args))

    def history_todo(self, people, w0, limit):
        """Unpriced positions of these wallets that closed before the window, newest first."""
        rows = list(self._owner_chunks(people, """SELECT pos_key, close_ts FROM positions WHERE owner IN ({q})
                                                  AND minted=1 AND valued=0 AND close_ts < ?
                                                  ORDER BY close_ts DESC LIMIT ?""", (w0, limit)))
        rows.sort(key=lambda r: -r[1])
        return [r[0] for r in rows[:limit]]

    def history_left(self, people, w0):
        return sum(r[0] for r in self._owner_chunks(people, """SELECT COUNT(*) FROM positions WHERE owner IN ({q})
                                                              AND minted=1 AND valued=0 AND close_ts < ?""", (w0,)))

    def candidates(self, now, w0):
        """Wallets to price and rank: MIN_CLOSED closed positions in the window and
        an LP action within ACTIVE_DAYS (older ones are left out entirely). Order:
        labelled and reference wallets, ones that placed in an RH Smart LP Finder
        pool ranking, then people by activity; bots (from open/close times, no
        prices needed) last. -> (people, bots)"""
        active_days = float(self.cfg.get("ACTIVE_DAYS") or 0)
        since = now - active_days * 86400 if active_days > 0 else 0
        cands = [r[0] for r in self.con.execute("""
            SELECT owner FROM positions
            WHERE owner IS NOT NULL AND minted=1 AND close_ts >= ?
              AND owner IN (SELECT addr FROM wallets WHERE last_seen >= ?)
            GROUP BY owner HAVING COUNT(*) >= ?
            ORDER BY owner IN (SELECT addr FROM labels) DESC,
                     owner IN (SELECT addr FROM slf_ranks WHERE pnl_usd > 0) DESC, COUNT(*) DESC
        """, (w0, since, int(self.cfg["MIN_CLOSED"])))]
        labelled = {r[0] for r in self.con.execute("SELECT addr FROM labels")}
        bots = {w for w, x in self.prescreen(cands, now, w0).items() if x["bot"] and w not in labelled}
        return [w for w in cands if w not in bots], [w for w in cands if w in bots]

    def rerank(self):
        """Rank again from what is already priced, without pricing anything new:
        quick, so the app shows the current rules right after it starts."""
        now = time.time()
        days = float(self.cfg["METRIC_DAYS"])
        self.drop_spiked_prices()
        people, bots = self.candidates(now, now - days * 86400)
        n = self.rank(now, days, people + bots, balances=False)
        self.con.commit()
        return n

    def drop_spiked_prices(self):
        """Once per database: what was priced before the MAX_* guards existed at
        an absurd token price, or paying back an absurd multiple, is left out."""
        if (get_meta(self.con, "spike_sweep") or 0) >= 2:
            return
        bad = self.con.execute("""
            SELECT e.block, e.log_index, e.pos_key FROM events e JOIN pools p ON p.id = e.pool
            LEFT JOIN tokens t0 ON t0.addr = p.c0 LEFT JOIN tokens t1 ON t1.addr = p.c1
            WHERE e.v_state = 1 AND (e.u0 > :px OR e.u1 > :px OR e.u0 * IFNULL(t0.supply, 0) > :mc
                                     OR e.u1 * IFNULL(t1.supply, 0) > :mc)""",
                               {"px": MAX_TOKEN_USD, "mc": MAX_MCAP_USD}).fetchall()
        self.con.executemany("UPDATE events SET v_state=2 WHERE block=? AND log_index=?", [r[:2] for r in bad])
        self.con.executemany("UPDATE positions SET valued=0 WHERE pos_key=?", {(r[2],) for r in bad})
        n = self.con.execute("""UPDATE positions SET valued=2 WHERE valued=1 AND dep_usd > 0
                                AND wd_usd + fee_usd + IFNULL(now_usd, 0) > ? * dep_usd AND pnl_usd > 1e4""",
                             (MAX_RETURN,)).rowcount
        set_meta(self.con, "spike_sweep", 2)
        self.con.commit()
        if bad or n:
            self.log(f"  left out {len(bad):,} LP events at an impossible token price (drained pool) and "
                     f"{n:,} positions paying back over {MAX_RETURN}x")

    def prescreen(self, wallets, now, w0):
        """{wallet: {kind, hold, per_day, bot}} from open/close times only."""
        rows = {}
        for i in range(0, len(wallets), 400):
            part = wallets[i:i + 400]
            for r in self.con.execute(f"""SELECT owner, pool, open_block, open_ts, close_ts FROM positions
                                          WHERE minted=1 AND owner IN ({",".join("?" * len(part))})
                                          AND (close_ts IS NULL OR close_ts >= ?)""", (*part, w0)):
                rows.setdefault(r[0], []).append({"pool": r[1], "open_block": r[2], "open_ts": r[3], "close_ts": r[4]})
        return {w: M.prescreen(ps, now, self.multiday_s) for w, ps in rows.items()}

    def entry_details(self, per_wallet):
        """Fill each position's side (bid / ask / both) and the token's market cap
        when it opened, from its first deposit."""
        flat = [p for ps in per_wallet.values() for p in ps]
        first = {}
        for i in range(0, len(flat), 500):
            part = [p["pos_key"] for p in flat[i:i + 500]]
            for r in self.con.execute(f"""SELECT pos_key, a0, a1, u0, u1 FROM events WHERE v_state=1
                                          AND pos_key IN ({",".join("?" * len(part))})
                                          AND (kind='inc' OR (IFNULL(kind,'v4')='v4' AND CAST(delta AS REAL) > 0))
                                          ORDER BY block DESC, log_index DESC""", part):
                first[r[0]] = r  # the last one written is the earliest
        for p in flat:
            e, pinfo = first.get(p["pos_key"]), self.pool_info(p["pool"])
            if not e or not pinfo or None in (e[1], e[2], e[3], e[4]):
                continue
            q = pinfo["q"]
            amt, px = (e[1], e[2]), (e[3], e[4])
            v_tok, v_q = amt[1 - q] * px[1 - q], amt[q] * px[q]
            tot = v_tok + v_q
            if tot > 0:
                p["side"] = "bid" if v_tok < SIDE_DUST * tot else "ask" if v_q < SIDE_DUST * tot else "both"
            supply = (self.tokens.get(pinfo["c1"] if q == 0 else pinfo["c0"]) or {}).get("supply")
            if supply and px[1 - q]:
                p["mcap"] = px[1 - q] * supply

    def history_sums(self, wallets, now):
        """{wallet: {'raw'|'real': {pnl, pnl_3m, dep, first_ts, gap}}} over every
        priced closed position, for the all-time columns."""
        t3m = now - 90 * 86400
        out = {w: {"raw": {}, "real": {}} for w in wallets}
        for i in range(0, len(wallets), 400):
            part = wallets[i:i + 400]
            q = ",".join("?" * len(part))
            for w, pnl, real, dep, p3, r3 in self.con.execute(f"""
                    SELECT owner, SUM(pnl_usd), SUM(pnl_usd - IFNULL(cost_usd, 0)), SUM(dep_usd),
                           SUM(CASE WHEN close_ts >= ? THEN pnl_usd ELSE 0 END),
                           SUM(CASE WHEN close_ts >= ? THEN pnl_usd - IFNULL(cost_usd, 0) ELSE 0 END)
                    FROM positions WHERE valued=1 AND launch=0 AND close_ts IS NOT NULL AND owner IN ({q})
                    GROUP BY owner""", (t3m, t3m, *part)):
                out[w]["raw"].update(pnl=pnl, pnl_3m=p3, dep=dep)
                out[w]["real"].update(pnl=real, pnl_3m=r3, dep=dep)
            for w, first, gap in self.con.execute(f"""
                    SELECT owner, MIN(open_ts), SUM(valued=0 AND close_ts IS NOT NULL) FROM positions
                    WHERE minted=1 AND owner IN ({q}) GROUP BY owner""", part):
                for v in out[w].values():
                    v.update(first_ts=first, gap=gap)
        return out

    def rank(self, now, days, wallets, balances=True):
        """Metrics + score for every wallet with enough priced, closed positions."""
        con = self.con
        data_from = get_meta(con, "data_from_ts") or now - days * 86400
        min_closed = int(self.cfg["MIN_CLOSED"])
        mc = {a: t.get("mcap") for a, t in self.tokens.items()}
        tok_of = {pid: (p["c1"] if p["q"] == 0 else p["c0"])
                  for pid, p in ((pid, self.pool_info(pid)) for pid in self.pools) if p}

        w0 = now - days * 86400
        per_wallet = {}
        for i in range(0, len(wallets), 400):
            part = wallets[i:i + 400]
            q = ",".join("?" * len(part))
            for r in con.execute(f"""SELECT pos_key, token_id, owner, pool, tl, tu, open_block, open_ts, close_ts,
                                     dep_usd, wd_usd, fee_usd, now_usd, pnl_usd, cost_usd, eth_pair, single, sell_fee,
                                     early
                                     FROM positions WHERE valued=1 AND launch=0 AND owner IN ({q})
                                     AND (close_ts IS NULL OR close_ts >= ?)""", (*part, w0)):
                per_wallet.setdefault(r["owner"], []).append({
                    "token_id": r["token_id"], "pos_key": r["pos_key"], "pool": r["pool"], "tl": r["tl"], "tu": r["tu"],
                    "early": r["early"],
                    "open_block": r["open_block"], "open_ts": r["open_ts"],
                    "close_ts": r["close_ts"], "dep": r["dep_usd"], "wd": r["wd_usd"], "fee": r["fee_usd"],
                    "now": r["now_usd"] or 0.0, "pnl": r["pnl_usd"], "cost": r["cost_usd"],
                    "eth_pair": r["eth_pair"], "single": r["single"], "sell_fee": r["sell_fee"],
                    "mcap": None, "side": None,
                })
        self.entry_details(per_wallet)
        for p in (p for ps in per_wallet.values() for p in ps if p["mcap"] is None):
            p["mcap"] = mc.get(tok_of.get(p["pool"]))  # no entry price: today's market cap
        # a wallet whose older positions aren't priced yet is measured over the
        # stretch that is: from the oldest priced open inside the window
        partial = {r[0] for r in con.execute(f"""SELECT DISTINCT owner FROM positions WHERE valued=0 AND minted=1
                                                  AND (close_ts IS NULL OR close_ts >= ?)""", (w0,))}
        refs = {r[0]: r[1] for r in con.execute("SELECT addr, ref FROM labels WHERE ref IS NOT NULL")}

        variants = {"raw": (days, False), "real": (days, True), "d1raw": (1, False), "d1real": (1, True)}
        hist = self.history_sums(list(per_wallet), now)
        out = {w: {} for w in per_wallet}
        for name, (wd, real) in variants.items():
            ms = {}
            for w, ps in per_wallet.items():
                start = data_from
                if w in partial:
                    start = max(data_from, min((p["open_ts"] for p in ps if p["open_ts"] >= w0), default=data_from))
                m = M.wallet_metrics(ps, now, wd, start, real, self.multiday_s)
                if m and m["closed"] >= (min_closed if wd == days else 1):
                    m["early"] = sum(1 for p in ps if p.get("early"))
                    # priced only from `start` on: PnL {days}D covers that much of the window
                    m["priced_from"] = int(start) if w in partial and start > w0 + 3600 else None
                    m.update(M.history(hist.get(w, {}).get("real" if real else "raw", {}), now, data_from,
                                       config.CHAIN_LAUNCH_TS))
                    ms[w] = m
            if name == "raw":  # the class comes from the full window; a reference wallet's is set by you
                kinds = {w: refs.get(w) or m["kind"] for w, m in ms.items()}
            for kind in M.KINDS:
                ws = [w for w in ms if kinds.get(w) == kind]
                for w, sc in zip(ws, M.score_all([ms[w] for w in ws], kind)):
                    ms[w].update(score=sc, verdict=M.verdict(sc), copy=M.copyable(ms[w]), kind=kind,
                                 kind_set=w in refs)
                    out[w][name] = ms[w]

        scored = [w for w in out if "raw" in out[w]]
        if balances:  # RPC: the quick re-rank on start leaves them for the next full run
            self.refresh_balances(scored)
        copy = copiers(con, scored, now - days * 86400)
        sell_fees = []
        for w in scored:
            raw = out[w]["raw"]
            c = copy.get(w, {"copiers": 0, "ratio": 0.0})
            for v in out[w].values():
                v.update(style=raw["style"], style_conf=raw["style_conf"], copiers=c["copiers"], exposure=c["ratio"],
                         exposure_usd=c["ratio"] * v["size"])
            sell_fees += [p["sell_fee"] for p in per_wallet[w] if p["sell_fee"]]
        con.executemany("UPDATE wallets SET data=?, score=?, scored_at=? WHERE addr=?",
                        [(json.dumps(out[w]), out[w]["raw"]["score"], int(now), w) for w in scored])
        # anyone not scored this run dropped out of the window
        con.execute("UPDATE wallets SET data=NULL, score=NULL WHERE score IS NOT NULL AND scored_at < ?",
                    (int(now),))
        if sell_fees:  # median: a few 20%-fee pools shouldn't set the banner
            set_meta(con, "avg_sell_fee", statistics.median(sell_fees))
        set_meta(con, "scored_at", int(now))
        set_meta(con, "rank_version", M.RANK_VERSION)
        set_meta(con, "rated", len(per_wallet))  # wallets with priced positions in the window
        return len(scored)


def copiers(con, wallets, since, window=60):
    """Wallets that opened the same pool + exact range within `window` s after
    this wallet's opens, at least twice.

    ratio: liquidity those copiers add per open of this wallet, as a share of
    the wallet's own (same pool and range, so the units match).
    -> {wallet: {copiers, ratio}}"""
    out = {}
    for w in wallets:
        rows = con.execute(f"""
            SELECT p2.owner AS who, e1.block || ':' || e1.log_index AS k,
                   CAST(e2.delta AS REAL) / CAST(e1.delta AS REAL) AS r
            FROM positions p1
            JOIN events e1 ON e1.pos_key = p1.pos_key
            JOIN events e2 ON e2.pool = e1.pool AND e2.tl = e1.tl AND e2.tu = e1.tu
                          AND e2.ts BETWEEN e1.ts AND e1.ts + {window}
                          AND (e2.block > e1.block OR (e2.block = e1.block AND e2.log_index > e1.log_index))
            JOIN positions p2 ON p2.pos_key = e2.pos_key
            WHERE p1.owner = ? AND p1.open_ts >= ? AND CAST(e1.delta AS REAL) > 0
              AND CAST(e2.delta AS REAL) > 0 AND p2.owner IS NOT NULL AND p2.owner != p1.owner
              AND ABS(e1.tl) < {C.FULL_RANGE_TICK} AND ABS(e1.tu) < {C.FULL_RANGE_TICK}""",
                           (w, since)).fetchall()
        if not rows:
            continue
        hits = {}
        for r in rows:
            h = hits.setdefault(r["who"], [set(), 0.0])
            h[0].add(r["k"])
            h[1] += r["r"]
        keep = {who: h for who, h in hits.items() if len(h[0]) >= 2}
        if not keep:
            continue
        opens = con.execute("""SELECT COUNT(*) FROM positions p JOIN events e ON e.pos_key = p.pos_key
                               WHERE p.owner = ? AND p.open_ts >= ? AND CAST(e.delta AS REAL) > 0""",
                            (w, since)).fetchone()[0] or 1
        out[w] = {"copiers": len(keep), "ratio": sum(h[1] for h in keep.values()) / opens}
    return out
