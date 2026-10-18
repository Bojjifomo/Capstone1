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
from indexer import multicall

PER_WALLET = 150  # newest positions per wallet that get priced
LAUNCH_SHARE = 0.02  # depositing >= 2% of a token's supply = launching it, not LPing it
FIRST_PASS = 20   # ...but every wallet gets this many before any gets more


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


def usd_prices(sqrtp, dec0, dec1, q, kind, eth):
    """USD per whole token0 and token1 at this pool price."""
    p = C.price1_per0(sqrtp, dec0, dec1)  # token0 in token1
    if not p:
        return None
    qusd = 1.0 if kind == "usd" else eth
    if qusd is None:
        return None
    return (p * qusd, qusd) if q == 1 else (qusd, qusd / p)


class Scorer:
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
        return {**p, "q": q, "kind": kind, "d0": d0, "d1": d1}

    # ---- pricing events --------------------------------------------------
    def value_events(self, token_ids):
        ids = list(token_ids)
        groups = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            for r in self.con.execute(
                    f"SELECT block, log_index, token_id, pool, tl, tu, delta FROM events "
                    f"WHERE token_id IN ({q}) AND v_state=0 ORDER BY block, log_index", part):
                groups.setdefault((r["token_id"], r["block"]), []).append(dict(r))
        if not groups:
            return 0
        keys = list(groups)
        calls = []
        for tid, blk in keys:
            e = groups[(tid, blk)][0]
            sub = [(config.STATE_VIEW, C.cd_slot0(e["pool"])),
                   (config.STATE_VIEW, C.cd_position_info(e["pool"], config.POSITION_MANAGER,
                                                          e["tl"], e["tu"], tid)),
                   (config.STATE_VIEW, C.cd_fee_growth_inside(e["pool"], e["tl"], e["tu"]))]
            sub += [(config.STATE_VIEW, C.cd_slot0(p)) for p in config.ETH_USD_POOLS[:2]]
            calls.append(("eth_call", [{"to": config.MULTICALL3, "data": C.encode_aggregate3(sub)},
                                       hex(blk - 1)]))
        results = self.rpc.batch(calls)
        updates = []
        failed = 0
        first_err = None
        for key, ret in zip(keys, results):
            evs = groups[key]
            pinfo = self.pool_info(evs[0]["pool"])
            if isinstance(ret, Exception) or not ret:
                failed += 1
                first_err = first_err or ret
                continue
            try:
                s0, pos, fg, *eth_slots = C.decode_aggregate3(ret)
            except (ValueError, IndexError):
                failed += 1
                continue
            slot = C.decode_slot0(s0) if s0 else None
            if not pinfo or not slot or not slot["sqrtp"]:
                updates += [(2, None, None, None, None, None, None, None, e["block"], e["log_index"]) for e in evs]
                continue
            prices = usd_prices(slot["sqrtp"], pinfo["d0"], pinfo["d1"], pinfo["q"], pinfo["kind"],
                                eth_usd(eth_slots))
            if not prices:
                updates += [(2, None, None, None, None, None, None, None, e["block"], e["log_index"]) for e in evs]
                continue
            u0, u1 = prices
            f0 = f1 = 0.0
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
        if failed == len(keys):
            hint = ""
            if any(w in str(first_err).lower() for w in ("trie", "state", "archive", "pruned", "historical")):
                hint = " Your RPC doesn't seem to serve old block state (archive), which scoring needs."
            self.log(f"  all {failed} price reads failed: {first_err}.{hint}")
        elif failed:
            self.log(f"  {failed} price reads failed (will retry next run)")
        return len(keys)

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
                prices = usd_prices(slot["sqrtp"], pinfo["d0"], pinfo["d1"], pinfo["q"], pinfo["kind"], eth)
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
                pr = usd_prices(s["sqrtp"], p["d0"], p["d1"], p["q"], p["kind"], eth)
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
                          (config.USDG, C.cd_simple(C.SEL_BALANCE_OF, w))]
            try:
                res = multicall(self.rpc, calls, "latest")
            except Exception as e:  # noqa: BLE001
                self.log(f"  balance read failed: {e}")
                continue
            for j, w in enumerate(part):
                eb, ub = res[2 * j], res[2 * j + 1]
                rows.append((int(eb, 16) / 1e18 if eb else None, int(ub, 16) / 1e6 if ub else None, now, w))
        self.con.executemany("UPDATE wallets SET eth_bal=?, usd_bal=?, bal_ts=? WHERE addr=?", rows)

    # ---- the run ---------------------------------------------------------
    def run(self, stop=lambda: False):
        con, cfg = self.con, self.cfg
        now = time.time()
        days = float(cfg["METRIC_DAYS"])
        w0 = now - days * 86400
        min_closed = int(cfg["MIN_CLOSED"])
        budget = int(cfg["SCORE_POSITIONS_PER_RUN"])

        # candidates: enough closed positions with a known open; labelled wallets
        # first, then ones that placed in an RH Smart LP Finder pool ranking
        cands = [r[0] for r in con.execute("""
            SELECT owner FROM positions
            WHERE owner IS NOT NULL AND minted=1 AND close_ts >= ?
            GROUP BY owner HAVING COUNT(*) >= ?
            ORDER BY owner IN (SELECT addr FROM labels) DESC,
                     owner IN (SELECT addr FROM slf_ranks WHERE pnl_usd > 0) DESC, COUNT(*) DESC
        """, (w0, min_closed))]
        # newest positions per wallet, unpriced ones flagged: [(token_id, closed, valued)]
        newest = {}
        for w in cands:
            newest[w] = con.execute("""
                SELECT token_id, close_ts IS NOT NULL, valued FROM positions WHERE owner=? AND minted=1
                  AND (close_ts IS NULL OR close_ts >= ?) ORDER BY open_ts DESC LIMIT ?""",
                                    (w, w0, PER_WALLET)).fetchall()
        # breadth first: the newest FIRST_PASS of every wallet, then deeper
        todo, seen = [], set()
        for depth in (FIRST_PASS, PER_WALLET):
            for w in cands:
                for tid, _, valued in newest[w][:depth]:
                    if len(todo) >= budget:
                        break
                    if valued == 0 and tid not in seen:
                        seen.add(tid)
                        todo.append(tid)
        self.log(f"  {len(cands):,} candidate wallets, pricing {len(todo):,} positions")

        t0 = time.time()
        for i in range(0, len(todo), 400):
            if stop():
                break
            part = todo[i:i + 400]
            n = self.value_events(part)
            con.commit()
            if time.time() - t0 > 5:
                self.log(f"    priced {min(i + 400, len(todo)):,}/{len(todo):,} positions ({n} reads)")
                t0 = time.time()

        # open positions get re-valued every run (their value moves)
        open_ids = [tid for rows in newest.values() for tid, closed, valued in rows
                    if not closed and (valued == 1 or tid in seen)]
        from db import get_meta
        head = get_meta(con, "fwd_block")
        open_vals = self.value_open(open_ids, head if head else "latest")
        self.aggregate(set(todo) | set(open_ids), open_vals)
        con.commit()

        pool_ids = [r[0] for r in con.execute(
            "SELECT DISTINCT pool FROM positions WHERE valued=1 AND (close_ts IS NULL OR close_ts >= ?)", (w0,))]
        eth = self.refresh_tokens(pool_ids)
        if eth:
            from db import set_meta
            set_meta(con, "eth_usd", eth)
        n = self.rank(now, days, cands)
        con.commit()
        return {"candidates": len(cands), "priced": len(todo), "scored": n}

    def rank(self, now, days, wallets):
        """Metrics + score for every wallet with enough priced, closed positions."""
        con = self.con
        from db import get_meta, set_meta
        data_from = get_meta(con, "data_from_ts") or now - days * 86400
        min_closed = int(self.cfg["MIN_CLOSED"])
        mc = {a: t.get("mcap") for a, t in self.tokens.items()}
        tok_of = {pid: (p["c1"] if p["q"] == 0 else p["c0"])
                  for pid, p in ((pid, self.pool_info(pid)) for pid in self.pools) if p}

        per_wallet = {}
        for i in range(0, len(wallets), 400):
            part = wallets[i:i + 400]
            q = ",".join("?" * len(part))
            for r in con.execute(f"""SELECT owner, pool, tl, tu, open_ts, close_ts, dep_usd, wd_usd, fee_usd,
                                     now_usd, pnl_usd, cost_usd, eth_pair, single, sell_fee
                                     FROM positions WHERE valued=1 AND launch=0 AND owner IN ({q})
                                     AND (close_ts IS NULL OR close_ts >= ?)""", (*part, now - days * 86400)):
                per_wallet.setdefault(r["owner"], []).append({
                    "pool": r["pool"], "tl": r["tl"], "tu": r["tu"], "open_ts": r["open_ts"],
                    "close_ts": r["close_ts"], "dep": r["dep_usd"], "wd": r["wd_usd"], "fee": r["fee_usd"],
                    "now": r["now_usd"] or 0.0, "pnl": r["pnl_usd"], "cost": r["cost_usd"],
                    "eth_pair": r["eth_pair"], "single": r["single"], "sell_fee": r["sell_fee"],
                    "mcap": mc.get(tok_of.get(r["pool"])),
                })

        variants = {"raw": (days, False), "real": (days, True), "d1raw": (1, False), "d1real": (1, True)}
        out = {w: {} for w in per_wallet}
        for name, (wd, real) in variants.items():
            ms = {}
            for w, ps in per_wallet.items():
                m = M.wallet_metrics(ps, now, wd, data_from, real)
                if m and m["closed"] >= (min_closed if wd == days else 1):
                    ms[w] = m
            ws = list(ms)
            scores = M.score_all([ms[w] for w in ws])
            for w, s in zip(ws, scores):
                ms[w].update(score=s, verdict=M.verdict(s), copy=M.copyable(ms[w]))
                out[w][name] = ms[w]

        scored = [w for w in out if "raw" in out[w]]
        self.refresh_balances(scored)
        copy = copiers(con, scored, now - days * 86400)
        sell_fees = []
        for w in scored:
            st, conf = M.style(per_wallet[w], out[w]["raw"])
            c = copy.get(w, {"copiers": 0, "ratio": 0.0})
            for v in out[w].values():
                v.update(style=st, style_conf=conf, copiers=c["copiers"], exposure=c["ratio"],
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
            JOIN events e1 ON e1.token_id = p1.token_id
            JOIN events e2 ON e2.pool = e1.pool AND e2.tl = e1.tl AND e2.tu = e1.tu
                          AND e2.ts BETWEEN e1.ts AND e1.ts + {window}
                          AND (e2.block > e1.block OR (e2.block = e1.block AND e2.log_index > e1.log_index))
            JOIN positions p2 ON p2.token_id = e2.token_id
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
        opens = con.execute("""SELECT COUNT(*) FROM positions p JOIN events e ON e.token_id = p.token_id
                               WHERE p.owner = ? AND p.open_ts >= ? AND CAST(e.delta AS REAL) > 0""",
                            (w, since)).fetchone()[0] or 1
        out[w] = {"copiers": len(keep), "ratio": sum(h[1] for h in keep.values()) / opens}
    return out
