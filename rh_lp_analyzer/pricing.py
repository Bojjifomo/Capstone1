"""Pricing a wallet's LP events from chain state (archive eth_call through
Multicall3), copied from RH Wallet Finder's scorer so this app stands alone.

Each event is priced one block before it:
  - StateView.getSlot0          pool price, LP fee
  - StateView.getPositionInfo   liquidity and fee checkpoints before the event
  - StateView.getFeeGrowthInside
  - getSlot0 of the ETH/USDG pools for the ETH price
Token amounts come from the liquidity delta and the price; fees are what the
position earned since its last checkpoint. A pool needs USDG or ETH on one
side to be priced; other pools are skipped.
"""
import time

import chain as C
import config

# Every position in the window gets priced, newest first and breadth first: each
# wallet's newest 20, then 200, then 1000, then the rest. (A cap of 500 left the
# busiest ladder LPs, 1,000-6,000 positions a month, measured over their last
# few days only.)
PER_WALLET = 20_000
DEPTHS = (20, 200, 1000, PER_WALLET)
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


def eth_usd(res_slots):
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


class Pricer:
    def __init__(self, con, rpc, cfg, log=print):
        self.con, self.rpc, self.cfg, self.log = con, rpc, cfg, log
        self.pools = {r["id"]: dict(r) for r in con.execute("SELECT * FROM pools")}
        self.tokens = {r["addr"]: dict(r) for r in con.execute("SELECT * FROM tokens")}
        self.slip = float(cfg["SELL_SLIPPAGE_PCT"]) / 100

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
        ids = list(token_ids)
        groups = {}
        opened = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            for r in self.con.execute(
                    f"SELECT e.block, e.log_index, e.token_id, e.pool, e.tl, e.tu, e.delta, p.open_block "
                    f"FROM events e LEFT JOIN positions p ON p.token_id = e.token_id "
                    f"WHERE e.token_id IN ({q}) AND e.v_state=0 ORDER BY e.block, e.log_index", part):
                groups.setdefault((r["token_id"], r["block"]), []).append(dict(r))
                opened[r["token_id"]] = r["open_block"]
        if not groups:
            return 0
        by_block = {}
        for key in groups:
            by_block.setdefault(key[1], []).append(key)
        calls, plan = [], []  # plan: per call, (keys, pools, {key: (posinfo index, feegrowth index) or None})
        eth_pools = config.ETH_USD_POOLS[:2]
        for blk, keys in by_block.items():
            for j in range(0, len(keys), EVENTS_PER_CALL):
                part = keys[j:j + EVENTS_PER_CALL]
                pools = list(dict.fromkeys(groups[k][0]["pool"] for k in part))
                sub = [(config.STATE_VIEW, C.cd_slot0(p)) for p in eth_pools + pools]
                where = {}
                for k in part:
                    e = groups[k][0]
                    if opened.get(k[0]) == blk:
                        where[k] = None  # minted in this block: nothing earned yet
                        continue
                    where[k] = (len(sub), len(sub) + 1)
                    sub += [(config.STATE_VIEW, C.cd_position_info(e["pool"], config.POSITION_MANAGER,
                                                                   e["tl"], e["tu"], k[0])),
                            (config.STATE_VIEW, C.cd_fee_growth_inside(e["pool"], e["tl"], e["tu"]))]
                calls.append(("eth_call", [{"to": config.MULTICALL3, "data": C.encode_aggregate3(sub)},
                                           hex(blk - 1)]))
                plan.append((part, pools, where))
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
            eth = eth_usd(res[:len(eth_pools)])
            slots = dict(zip(pools, res[len(eth_pools):len(eth_pools) + len(pools)]))
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
        return len(calls)

    def value_open(self, token_ids, block="latest"):
        """Value (+ unclaimed fees) of open positions at `block` -> {token_id: (usd, token_side_usd)}.

        `block` is the last block we indexed: reading at the real chain head
        could see a close we haven't loaded yet and value it at $0."""
        rows = []
        ids = list(token_ids)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            rows += [dict(r) for r in self.con.execute(
                f"SELECT token_id, pool, tl, tu FROM positions WHERE token_id IN ({q})", part)]
        out = {}
        for i in range(0, len(rows), 90):
            part = rows[i:i + 90]
            calls = [(config.STATE_VIEW, C.cd_slot0(p)) for p in config.ETH_USD_POOLS[:2]]
            for r in part:
                calls += [(config.STATE_VIEW, C.cd_slot0(r["pool"])),
                          (config.STATE_VIEW, C.cd_position_info(r["pool"], config.POSITION_MANAGER,
                                                                 r["tl"], r["tu"], r["token_id"])),
                          (config.STATE_VIEW, C.cd_fee_growth_inside(r["pool"], r["tl"], r["tu"]))]
            try:
                res = multicall(self.rpc, calls, block)
            except Exception as e:  # noqa: BLE001 - keep scoring the rest
                self.log(f"  open value read failed: {e}")
                continue
            eth = eth_usd(res[:2])
            for j, r in enumerate(part):
                s0, pos, fg = res[2 + 3 * j:5 + 3 * j]
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
                amts, us = (a0, a1), prices
                tok = 1 - pinfo["q"]
                out[r["token_id"]] = (a0 * us[0] + a1 * us[1], amts[tok] * us[tok])
        return out

    def aggregate(self, token_ids, open_values):
        """Roll priced events up into each position's PnL."""
        ids = list(token_ids)
        rows = []
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            pos = {r["token_id"]: dict(r) for r in self.con.execute(
                f"SELECT token_id, pool, close_ts, minted FROM positions WHERE token_id IN ({q})", part)}
            evs = {}
            for r in self.con.execute(f"SELECT * FROM events WHERE token_id IN ({q}) "
                                      f"ORDER BY block, log_index", part):
                evs.setdefault(r["token_id"], []).append(dict(r))
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
                    if int(x["delta"]) > 0:
                        dep += val
                    elif int(x["delta"]) < 0:
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
                                launch=? WHERE token_id=?""", rows)
        return len(rows)

    # ---- token prices, balances -----------------------------------------
    def refresh_tokens(self, pool_ids):
        """Current USD price and market cap of the non-quote token of each pool."""
        pinfos = [p for p in (self.pool_info(pid) for pid in pool_ids) if p]
        best = {}
        for p in pinfos:  # one pool per token: the one listed first wins
            best.setdefault(p["c1"] if p["q"] == 0 else p["c0"], p)
        items = list(best.items())
        eth = None  # stays None when every read fails
        now = int(time.time())
        for i in range(0, len(items), 200):
            part = items[i:i + 200]
            calls = [(config.STATE_VIEW, C.cd_slot0(x)) for x in config.ETH_USD_POOLS[:2]]
            calls += [(config.STATE_VIEW, C.cd_slot0(p["id"])) for _, p in part]
            try:
                res = multicall(self.rpc, calls, "latest")
            except Exception as e:  # noqa: BLE001
                self.log(f"  token price read failed: {e}")
                continue
            eth = eth_usd(res[:2])
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


    def entry_details(self, per_wallet):
        """Fill each position's side (bid / ask / both) and the token's market cap
        when it opened, from its first deposit."""
        flat = [p for ps in per_wallet.values() for p in ps]
        first = {}
        for i in range(0, len(flat), 500):
            part = [p["token_id"] for p in flat[i:i + 500]]
            for r in self.con.execute(f"""SELECT token_id, a0, a1, u0, u1 FROM events WHERE v_state=1
                                          AND token_id IN ({",".join("?" * len(part))})
                                          AND CAST(delta AS REAL) > 0 ORDER BY block DESC, log_index DESC""", part):
                first[r[0]] = r  # the last one written is the earliest
        for p in flat:
            e, pinfo = first.get(p["token_id"]), self.pool_info(p["pool"])
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

