"""Pool table: chain index + DexScreener + token metadata, one row per pool, plus PING.

Money is in USD. Quote amounts stay on the row so the page can show HYPE or USDC.
`dex` is v3 or v4; `venue` is elysiumswap, uniswap-v3, or uniswap-v4.
"""
from __future__ import annotations

import threading
import time

from . import chain, filters, market

META_TTL = 6 * 3600


class Book:
    def __init__(self, index, cfg: dict):
        self.ix = index
        self.cfg = cfg
        self.lock = threading.Lock()
        self.meta: dict[str, dict] = {}
        self.meta_t: dict[str, float] = {}
        self.extra: dict[str, dict] = {}
        self.prev_ping: list[str] | None = None
        self.ping_new: list[str] = []

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
        new = [i.lower() for i in ids if i.lower() not in self.extra and i.lower() not in self.ix.keys]
        if not new:
            return
        v4 = [i for i in new if len(i) == 66]
        v3 = [i for i in new if len(i) == 42]
        got: dict[str, dict | None] = {}
        if v4:
            try:
                got.update(chain.pool_keys(v4))
            except Exception:
                pass
            miss = [i for i in v4 if not got.get(i)]
            if miss:
                try:
                    got.update(chain.v4_keys_from_init(miss))
                except Exception:
                    pass
        if v3:
            known = {i: self.ix.v3pools.get(i) for i in v3 if self.ix.v3pools.get(i)}
            got.update(known)
            rest = [i for i in v3 if i not in known]
            if rest:
                try:
                    got.update(chain.v3_keys(rest))
                except Exception:
                    pass
        for pid, key in got.items():
            if key and chain.quote_side(key) is not None:
                self.extra[pid] = key

    def _row(self, pid: str, key: dict, act: dict | None, dx: dict | None, meta: dict, hype: float | None,
             now: float, s0: dict | None = None) -> dict:
        q = chain.quote_side(key)
        qaddr = key["currency0" if q == 0 else "currency1"]
        taddr = key["currency1" if q == 0 else "currency0"]
        qusd = 1.0 if qaddr == chain.USDC else hype
        mt = meta.get(taddr, {})
        mq = chain.DECIMALS.get(qaddr, (meta.get(qaddr) or {}).get("decimals", 18))
        dt = mt.get("decimals", 18)
        last = (act or {}).get("last") or {}
        sqrt = int(last["sqrt"]) if last.get("sqrt") else (s0 or {}).get("sqrtPriceX96")
        tick = last.get("tick") if last.get("sqrt") else (s0 or {}).get("tick")
        price_q = None
        if sqrt:
            p01 = chain.price_of(sqrt, dt if q == 1 else mq, mq if q == 1 else dt)
            price_q = p01 if q == 1 else (1 / p01 if p01 else None)
        price_usd = price_q * qusd if price_q is not None and qusd else (dx or {}).get("priceUsd")
        supply = mt.get("supply")
        mcap = supply / 10 ** dt * price_usd if supply and price_usd else None
        if mcap is None and dx and dx["base"]["address"] == taddr:
            mcap = dx.get("mcapUsd")
        dynamic = bool(key.get("fee", 0) & chain.DYNAMIC_FEE) and key.get("dex") == "v4"
        lp_fee = last.get("fee")
        if lp_fee is None and dynamic and s0:
            lp_fee = s0.get("lpFee")
        if lp_fee is None and not dynamic:
            lp_fee = key.get("fee")
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
        created = key.get("created") or (dx or {}).get("createdAt")
        sym = mt.get("symbol") or ((dx or {}).get("base") or {}).get("symbol") or taddr[2:8]
        chg = {}
        if sqrt:
            for wname in ("5m", "1h", "6h", "24h"):
                then = (win.get(wname) or {}).get("sqrtThen")
                if not then:
                    continue
                now_p = (sqrt / 2 ** 96) ** 2
                then_p = (int(then) / 2 ** 96) ** 2
                if not then_p or not now_p:
                    continue
                ratio = (now_p / then_p) if q == 1 else (then_p / now_p)
                chg[wname] = (ratio - 1) * 100
        for wname, val in ((dx or {}).get("chg") or {}).items():
            if chg.get(wname) is None and val is not None:
                chg[wname] = val
        txns = {}
        for src, dst in (("5m", "m5"), ("1h", "h1"), ("6h", "h6"), ("24h", "h24")):
            wrow = win.get(src) or {}
            if wrow.get("swaps"):
                txns[dst] = {"buys": wrow.get("buys") or 0, "sells": wrow.get("sells") or 0}
        for dst, val in ((dx or {}).get("txns") or {}).items():
            txns.setdefault(dst, val)
        return {
            "pool": pid, "dex": key.get("dex") or "v4", "venue": key.get("venue") or "",
            "token": taddr, "quote": qaddr, "quoteSymbol": chain.QUOTE_SYMBOL.get(qaddr, "?"),
            "quoteUsd": qusd, "symbol": sym,
            "name": ((dx or {}).get("base") or {}).get("name") if dx and dx["base"]["address"] == taddr else sym,
            "key": {k: key[k] for k in ("currency0", "currency1", "fee", "tickSpacing", "hooks") if k in key},
            "hooks": key.get("hooks") or chain.ZERO, "tickSpacing": key.get("tickSpacing"), "dynamicFee": dynamic,
            "lpFee": lp_fee, "lpFeePct": round(lp_fee / 1e4, 4) if lp_fee is not None else None,
            "tick": tick, "priceQuote": price_q, "priceUsd": price_usd, "mcapUsd": mcap,
            "tvlUsd": tvl, "ageHours": (now - created) / 3600 if created else None,
            "fees": fees, "vol": vols, "feePctDay": fee_pct_day,
            "swaps": {w: (win.get(w) or {}).get("swaps") for w in ("5m", "1h", "24h")},
            "chg": chg, "txns": txns, "icon": (dx or {}).get("icon"), "dexscreener": bool(dx),
            "manager": key.get("npm") or (chain.V4_POSITION_MANAGER if key.get("dex") != "v3" else None),
        }

    def _slot0(self, ids: list[str], keys: dict) -> dict:
        v4 = [i for i in ids if keys[i].get("dex") != "v3"]
        v3 = [i for i in ids if keys[i].get("dex") == "v3"]
        out = {}
        try:
            if v4:
                out.update(chain.slot0s_v4(v4))
            if v3:
                out.update(chain.slot0s_v3(v3))
        except Exception:
            pass
        return out

    def rows(self, watch: list[str] | None = None) -> dict:
        cfg, now = self.cfg, time.time()
        hype = self.ix.hype_usd()
        act = {r["pool"]: r for r in self.ix.rows()}
        top = sorted(act.values(), key=lambda r: -((r["win"]["1h"]["feesUsd"] or 0) + (r["win"]["24h"]["feesUsd"] or 0) / 24))
        ids = [r["pool"] for r in top[: int(cfg["scan"]["maxPools"])]]
        for w in watch or []:
            if w not in ids:
                ids.append(w.lower())
        self.add_pools([i for i in ids if i not in act])
        for i in self.extra:
            if i not in ids:
                ids.append(i)
        keys = {}
        for i in ids:
            keys[i] = act[i]["key"] if i in act else self.extra.get(i) or self.ix.keys.get(i)
        ids = [i for i in ids if keys.get(i)]
        try:
            dx = market.pairs(ids)
        except Exception:
            dx = {}
        idle = [i for i in ids if i not in act]
        s0 = self._slot0(idle, keys) if idle else {}
        toks = set()
        for i in ids:
            toks.update((keys[i]["currency0"], keys[i]["currency1"]))
        meta = self.tokens(sorted(toks))
        size = filters.planned_size_usd(cfg)
        out = []
        for i in ids:
            r = self._row(i, keys[i], act.get(i), dx.get(i), meta, hype, now, s0.get(i))
            r["verdict"] = filters.evaluate(r, cfg, now, size)
            out.append(r)
        return {"t": now, "rows": out, "ethUsd": hype, "hypeUsd": hype, "ping": self.ping(out),
                "index": self.index_status(), "sizeUsd": size, "maxPositionUsd": filters.max_position_usd(cfg)}

    def index_status(self) -> dict:
        st = dict(self.ix.status)
        st["coverageHours"] = round(self.ix.coverage() / 3600, 2)
        st["pools"] = len(self.ix.pool_ids())
        return st

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
        keyf = {
            "fee1hPct": lambda r: r["feePctDay"].get("1h") or 0,
            "fees1hUsd": lambda r: r["fees"].get("1h") or 0,
            "volTvl1h": lambda r: (r["vol"].get("1h") or 0) / r["tvlUsd"] if r["tvlUsd"] else 0,
            "fee24hPct": lambda r: r["feePctDay"].get("24h") or 0,
        }.get(p.get("sortBy"), lambda r: r["feePctDay"].get("1h") or 0)
        best, seen = [], set()
        for r in sorted(hits, key=keyf, reverse=True):
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
        return {"rows": [{"pool": r["pool"], "symbol": r["symbol"], "rank": n + 1, "dex": r["dex"]} for n, r in enumerate(best)],
                "total": len(hits), "new": self.ping_new,
                "filter": (f"gates · TVL ≥ ${p.get('minTvlUsd') or 0:,.0f} · mcap ≥ ${p.get('minMcapUsd') or 0:,.0f}"
                           + (f" ≤ ${p['maxMcapUsd']:,.0f}" if p.get("maxMcapUsd") else "")
                           + f" · umur ≥ {p.get('minPoolAgeHours') or 0}h · fee 1h ≥ ${p.get('minFees1hUsd') or 0}"
                           + f" · top {p.get('top', 10)} by {p.get('sortBy')}")}

    def search(self, q: str) -> list[dict]:
        q = (q or "").strip()
        found = []
        try:
            found = market.search(q)
        except Exception:
            found = []
        ids = []
        for d in found:
            if d["pool"] not in ids:
                ids.append(d["pool"])
        ql = q.lower()
        if ql.startswith("0x") and len(ql) in (42, 66) and ql not in ids:
            ids.append(ql)
        if not ql.startswith("0x"):
            for pid, key in list(self.ix.keys.items()):
                if not key:
                    continue
                # symbol match happens after meta; match address fragments and pool id
                if ql and (ql in pid or any(ql in key.get(c, "") for c in ("currency0", "currency1"))):
                    if pid not in ids:
                        ids.append(pid)
        self.add_pools(ids)
        keys = {i: self.ix.keys.get(i) or self.extra.get(i) for i in ids}
        ids = [i for i in ids if keys.get(i)]
        act = {r["pool"]: r for r in self.ix.rows() if r["pool"] in ids}
        s0 = self._slot0([i for i in ids if i not in act], keys)
        toks = set()
        for i in ids:
            toks.update((keys[i]["currency0"], keys[i]["currency1"]))
        meta = self.tokens(sorted(toks))
        if not ql.startswith("0x"):
            for pid, key in list(self.ix.keys.items()):
                if not key or pid in ids:
                    continue
                sym = (meta.get(key["currency0" if chain.quote_side(key) == 1 else "currency1"]) or {}).get("symbol") or ""
                if ql and ql in sym.lower():
                    ids.append(pid)
                    keys[pid] = key
        try:
            dx = market.pairs(ids)
        except Exception:
            dx = {}
        hype, now, size = self.ix.hype_usd(), time.time(), filters.planned_size_usd(self.cfg)
        out = []
        for i in ids:
            if not keys.get(i):
                continue
            r = self._row(i, keys[i], act.get(i), dx.get(i), meta, hype, now, s0.get(i))
            r["verdict"] = filters.evaluate(r, self.cfg, now, size)
            if not ql.startswith("0x"):
                blob = f"{r['symbol']} {r['name']} {r['pool']} {r['token']}".lower()
                if ql not in blob and i not in [d["pool"] for d in found]:
                    continue
            out.append(r)
        out.sort(key=lambda r: -(r["tvlUsd"] or 0))
        return out
