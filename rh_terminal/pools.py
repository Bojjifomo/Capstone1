"""Pool table: chain index + DexScreener + token metadata -> one row per pool, with the
entry check and the PING list.

Row money is in USD; the quote amounts are kept too so the page can show ETH or USDG.
"""
from __future__ import annotations

import threading
import time

from . import chain, filters, market

META_TTL = 6 * 3600          # symbol/decimals never change; supply rarely does


class Book:
    def __init__(self, index, cfg: dict):
        self.ix = index
        self.cfg = cfg
        self.lock = threading.Lock()
        self.meta: dict[str, dict] = {}
        self.meta_t: dict[str, float] = {}
        self.extra: dict[str, dict] = {}     # pools found by search / watchlist / positions: id -> key
        self.prev_ping: list[str] | None = None
        self.ping_new: list[str] = []

    # ---------------------------------------------------------------- meta ----
    def tokens(self, addrs: list[str]) -> dict[str, dict]:
        now = time.time()
        need = [a for a in set(addrs) if a not in self.meta or now - self.meta_t.get(a, 0) > META_TTL]
        if need:
            try:
                got = chain.token_meta(need)
            except Exception:
                got = {}
            with self.lock:
                for a, m in got.items():
                    self.meta[a], self.meta_t[a] = m, now
        return {a: self.meta[a] for a in addrs if a in self.meta}

    def add_pools(self, ids: list[str]) -> None:
        """Make pools that are not trading (or not indexed yet) part of the table."""
        new = [i for i in ids if i not in self.extra and i not in self.ix.keys]
        if new:
            for pid, key in chain.pool_keys(new).items():
                if key and chain.quote_side(key) is not None:
                    self.extra[pid] = key
        for i in ids:
            if i in self.ix.keys and self.ix.keys[i]:
                self.extra.setdefault(i, self.ix.keys[i])

    # ---------------------------------------------------------------- rows ----
    def _row(self, pid: str, key: dict, act: dict | None, dx: dict | None, meta: dict, eth: float | None,
             now: float, s0: dict | None = None) -> dict:
        q = chain.quote_side(key)
        qaddr = key["currency0" if q == 0 else "currency1"]
        taddr = key["currency1" if q == 0 else "currency0"]
        qusd = 1.0 if qaddr == chain.USDG else eth
        mt, mq = meta.get(taddr, {}), chain.DECIMALS.get(qaddr, 18)
        dt = mt.get("decimals", 18)
        last = (act or {}).get("last") or {}
        sqrt = int(last["sqrt"]) if last.get("sqrt") else (s0 or {}).get("sqrtPriceX96")
        tick = last.get("tick") if last.get("sqrt") else (s0 or {}).get("tick")
        price_q = None
        if sqrt:
            p01 = chain.price_of(sqrt, dt if q == 1 else mq, mq if q == 1 else dt)   # currency0 in currency1
            price_q = p01 if q == 1 else (1 / p01 if p01 else None)
        price_usd = price_q * qusd if price_q is not None and qusd else (dx or {}).get("priceUsd")
        supply = mt.get("supply")
        mcap = supply / 10 ** dt * price_usd if supply and price_usd else None
        if mcap is None and dx and dx["base"]["address"] == taddr:
            mcap = dx.get("mcapUsd")
        dynamic = bool(key["fee"] & 0x800000)
        lp_fee = (last.get("fee") if dynamic else key["fee"])
        if dynamic and lp_fee is None and s0:
            lp_fee = s0.get("lpFee")
        fee_frac = (lp_fee or 0) / 1e6
        tvl = (dx or {}).get("tvlUsd")
        cov = self.ix.coverage()
        win = (act or {}).get("win") or {}
        fees, vols = {}, {}
        for w, hrs in (("5m", 1 / 12), ("1h", 1), ("6h", 6), ("24h", 24)):
            iv = (win.get(w) or {}).get("feesUsd")
            ivol = (win.get(w) or {}).get("volUsd")
            dvol = ((dx or {}).get("vol") or {}).get(w)
            if iv is not None and (cov >= hrs * 3600 - 300 or dvol is None):
                fees[w], vols[w] = iv, ivol
            elif dvol is not None:
                fees[w], vols[w] = dvol * fee_frac, dvol
            else:
                fees[w], vols[w] = iv, ivol
        hrs = {"5m": 1 / 12, "1h": 1, "6h": 6, "24h": 24}
        fee_pct_day = {w: (fees[w] / tvl * 100 * 24 / h if tvl and fees[w] is not None else None) for w, h in hrs.items()}
        created = (dx or {}).get("createdAt")
        sym = mt.get("symbol") or ((dx or {}).get("base") or {}).get("symbol") or taddr[2:8]
        return {
            "pool": pid, "token": taddr, "quote": qaddr, "quoteSymbol": chain.QUOTE_SYMBOL.get(qaddr, "?"),
            "quoteUsd": qusd, "symbol": sym, "name": ((dx or {}).get("base") or {}).get("name") if dx and dx["base"]["address"] == taddr else sym,
            "key": key, "hooks": key["hooks"], "tickSpacing": key["tickSpacing"], "dynamicFee": dynamic,
            "lpFee": lp_fee, "lpFeePct": round(lp_fee / 1e4, 4) if lp_fee is not None else None,
            "tick": tick, "priceQuote": price_q, "priceUsd": price_usd, "mcapUsd": mcap,
            "tvlUsd": tvl, "ageHours": (now - created) / 3600 if created else None,
            "fees": fees, "vol": vols, "feePctDay": fee_pct_day,
            "swaps": {w: (win.get(w) or {}).get("swaps") for w in ("5m", "1h", "24h")},
            "chg": (dx or {}).get("chg") or {}, "txns": (dx or {}).get("txns") or {},
            "icon": (dx or {}).get("icon"), "dex": bool(dx),
        }

    def rows(self, watch: list[str] | None = None) -> dict:
        cfg, now = self.cfg, time.time()
        eth = self.ix.eth_usd()
        act = {r["pool"]: r for r in self.ix.rows()}
        top = sorted(act.values(), key=lambda r: -((r["win"]["1h"]["feesUsd"] or 0) + (r["win"]["24h"]["feesUsd"] or 0) / 24))
        ids = [r["pool"] for r in top[: int(cfg["scan"]["maxPools"])]]
        for w in watch or []:
            if w not in ids:
                ids.append(w)
        self.add_pools([i for i in ids if i not in act])
        for i in self.extra:
            if i not in ids:
                ids.append(i)
        keys = {i: (act[i]["key"] if i in act else self.extra.get(i)) for i in ids}
        ids = [i for i in ids if keys.get(i)]
        dx = market.pairs(ids)
        idle = [i for i in ids if i not in act]
        s0 = chain.slot0s(idle) if idle else {}
        toks = set()
        for i in ids:
            k = keys[i]
            toks.update((k["currency0"], k["currency1"]))
        meta = self.tokens(sorted(toks))
        size = filters.planned_size_usd(cfg)
        out = []
        for i in ids:
            r = self._row(i, keys[i], act.get(i), dx.get(i), meta, eth, now, s0.get(i))
            r["verdict"] = filters.evaluate(r, cfg, now, size)
            out.append(r)
        ping = self.ping(out)
        return {"t": now, "rows": out, "ethUsd": eth, "ping": ping, "index": self.index_status(),
                "sizeUsd": size, "maxPositionUsd": filters.max_position_usd(cfg)}

    def index_status(self) -> dict:
        st = dict(self.ix.status)
        st["coverageHours"] = round(self.ix.coverage() / 3600, 2)
        st["pools"] = len(self.ix.pool_ids())
        return st

    # ---------------------------------------------------------------- PING ----
    def ping(self, rows: list[dict]) -> dict:
        p = self.cfg["ping"]
        hits = []
        for r in rows:
            if p.get("requireGates") and r["verdict"]["level"] not in ("OPEN", "CAREFUL"):
                continue
            tvl, mc, age, f1 = r["tvlUsd"], r["mcapUsd"], r["ageHours"], r["fees"].get("1h")
            if p.get("minTvlUsd") and (tvl or 0) < p["minTvlUsd"]:
                continue
            if p.get("minMcapUsd") and (mc or 0) < p["minMcapUsd"]:
                continue
            if p.get("maxMcapUsd") and mc and mc > p["maxMcapUsd"]:
                continue
            if p.get("minPoolAgeHours") and (age is None or age < p["minPoolAgeHours"]):
                continue
            if p.get("maxPoolAgeHours") and age and age > p["maxPoolAgeHours"]:
                continue
            if p.get("minFees1hUsd") and (f1 or 0) < p["minFees1hUsd"]:
                continue
            hits.append(r)
        key = {
            "fee1hPct": lambda r: r["feePctDay"].get("1h") or 0,
            "fees1hUsd": lambda r: r["fees"].get("1h") or 0,
            "volTvl1h": lambda r: (r["vol"].get("1h") or 0) / r["tvlUsd"] if r["tvlUsd"] else 0,
            "fee24hPct": lambda r: r["feePctDay"].get("24h") or 0,
        }.get(p.get("sortBy"), lambda r: r["feePctDay"].get("1h") or 0)
        best, seen = [], set()
        for r in sorted(hits, key=key, reverse=True):      # one pool per token: its best one
            if r["token"] in seen:
                continue
            seen.add(r["token"])
            best.append(r)
            if len(best) >= int(p.get("top", 10)):
                break
        ids = [r["pool"] for r in best]
        if self.prev_ping is not None:
            self.ping_new = [i for i in ids if i not in self.prev_ping]
        self.prev_ping = ids
        return {"rows": [{"pool": r["pool"], "symbol": r["symbol"], "rank": n + 1} for n, r in enumerate(best)],
                "total": len(hits), "new": self.ping_new,
                "filter": (f"gates v1 · TVL ≥ ${p.get('minTvlUsd') or 0:,.0f} · mcap ≥ ${p.get('minMcapUsd') or 0:,.0f}"
                           + (f" ≤ ${p['maxMcapUsd']:,.0f}" if p.get("maxMcapUsd") else "")
                           + f" · umur ≥ {p.get('minPoolAgeHours') or 0}h · fee 1h ≥ ${p.get('minFees1hUsd') or 0}"
                           + f" · top {p.get('top', 10)} by {p.get('sortBy')}")}

    # -------------------------------------------------------------- search ----
    def search(self, q: str) -> list[dict]:
        found = market.search(q)
        ids = []
        for d in found:
            if d["pool"] not in ids:
                ids.append(d["pool"])
        self.add_pools(ids)
        keys = {i: self.ix.keys.get(i) or self.extra.get(i) for i in ids}
        ids = [i for i in ids if keys.get(i)]
        act = {r["pool"]: r for r in self.ix.rows() if r["pool"] in ids}
        idle = [i for i in ids if i not in act]
        s0 = chain.slot0s(idle) if idle else {}
        toks = set()
        for i in ids:
            toks.update((keys[i]["currency0"], keys[i]["currency1"]))
        meta = self.tokens(sorted(toks))
        dx = market.pairs(ids)
        eth, now, size = self.ix.eth_usd(), time.time(), filters.planned_size_usd(self.cfg)
        out = []
        for i in ids:
            r = self._row(i, keys[i], act.get(i), dx.get(i), meta, eth, now, s0.get(i))
            r["verdict"] = filters.evaluate(r, self.cfg, now, size)
            out.append(r)
        out.sort(key=lambda r: -(r["tvlUsd"] or 0))
        return out
