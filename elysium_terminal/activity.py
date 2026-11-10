"""On-chain swap index for Elysium: Uniswap v4 (PoolManager) and Uniswap v3 (both factories).

v4: one Swap log per trade on the PoolManager, same event as Robinhood's RH Terminal.
v3: Swap logs on pools created by ElysiumSwap and by the official Uniswap v3 factory.
Fee, volume and trade counts are summed into 5-minute buckets (5m / 1h / 6h / 24h).

Volume is the quote side (USDC, or HYPE/WHYPE priced in USDC). The LP fee is the swap fee
minus the protocol's cut. A fresh start backfills `backfillHours` (default 1). 24h of
buckets is kept on disk so a restart continues.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import chain

BUCKET = 300
KEEP = 24 * 3600
WINDOWS = {"5m": 300, "1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}


def lp_fee_v4(swap_fee: int, protocol_fee: int, zero_for_one: bool) -> int:
    """LP's part of a v4 swap fee, in hundredths of a bip.
    swapFee = pf + lp - pf*lp/1e6. pf is 12 bits per direction (low: 0->1, high: 1->0).
    A swapper paying currency0 (amount0 < 0 in the v4 event) is a 0->1 swap."""
    pf = (protocol_fee & 0xFFF) if zero_for_one else (protocol_fee >> 12) & 0xFFF
    if pf <= 0 or swap_fee <= pf:
        return max(0, swap_fee - pf)
    return round((swap_fee - pf) * 1e6 / (1e6 - pf))


def lp_fee_v3(fee_pips: int, fee_protocol: int, zero_for_one: bool) -> int:
    """LP's part of a v3 fee tier. feeProtocol is a divisor (low 4 bits token0, high token1):
    the protocol takes fee/divisor and the LP keeps the rest. 0 means the LP gets all of it."""
    pf = (fee_protocol & 0xF) if zero_for_one else (fee_protocol >> 4) & 0xF
    if pf <= 0:
        return fee_pips
    return fee_pips - fee_pips // pf


class Index:
    def __init__(self, data_dir: Path, backfill_hours: float = 1.0, poll_sec: float = 30.0):
        self.dir = Path(data_dir)
        self.backfill_hours = backfill_hours
        self.poll_sec = poll_sec
        self.lock = threading.Lock()
        self.head = 0
        self.v3_log_head = 0
        self.v3_scanned = 0
        self.ref = None
        self.traders: dict[str, dict[str, int]] = {}
        self.spb = 0.103
        self.started = time.time()
        self.pools: dict[str, dict] = {}
        self.keys: dict[str, dict | None] = {}
        self.v3pools: dict[str, dict] = {}
        self.status = {"state": "starting", "error": None, "lastPoll": 0, "logs": 0}
        self._load()

    def _load(self) -> None:
        rescan = False
        try:
            raw = json.loads((self.dir / "poolkeys.json").read_text())
            self.v3pools = raw.get("v3") or {}
            self.v3_scanned = int(raw.get("v3Scanned") or 0)
            keys = raw.get("keys") or {}
            # A null key used to mean "lookup failed" as well as "not a quote pool".
            # Failures were then never retried, which is how the table got stuck at 1 pool.
            bad = [i for i, v in keys.items() if not v]
            self.keys = {i: v for i, v in keys.items() if v}
            if len(bad) > 10 and len(bad) > len(self.keys):
                rescan = True
        except (OSError, ValueError):
            pass
        try:
            d = json.loads((self.dir / "activity.json").read_text())
            if time.time() - d.get("savedAt", 0) < KEEP:
                self.head, self.started = d["head"], d["started"]
                self.v3_log_head = int(d.get("v3LogHead") or d["head"])
                self.ref, self.spb = tuple(d["ref"]) if d.get("ref") else None, d.get("spb", self.spb)
                self.pools = {p: {"b": {int(b): v for b, v in x["b"].items()}, "last": x.get("last") or {}}
                              for p, x in d["pools"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if rescan:
            self.head = 0
            self.v3_log_head = 0

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            snap = {"savedAt": time.time(), "head": self.head, "v3LogHead": self.v3_log_head,
                    "started": self.started, "ref": self.ref, "spb": self.spb, "pools": self.pools}
            blob = {"v3Scanned": self.v3_scanned, "v3": self.v3pools, "keys": self.keys}
        tmp = self.dir / "activity.json.tmp"
        tmp.write_text(json.dumps(snap, separators=(",", ":")))
        tmp.replace(self.dir / "activity.json")
        (self.dir / "poolkeys.json").write_text(json.dumps(blob, separators=(",", ":")))

    def _calibrate(self, head: int) -> None:
        ts = chain.block_time(head)
        if self.ref and head - self.ref[0] > 1000:
            self.spb = max(0.02, min(2.0, (ts - self.ref[1]) / (head - self.ref[0])))
        elif not self.ref:
            back = max(1, head - 20000)
            self.spb = max(0.02, min(2.0, (ts - chain.block_time(back)) / (head - back)))
        self.ref = (head, ts)

    def ts_of(self, block: int) -> float:
        if not self.ref:
            return time.time()
        return self.ref[1] + (block - self.ref[0]) * self.spb

    def _touch_created(self, key: dict) -> None:
        blk = key.get("createdBlock")
        if blk and not key.get("created"):
            key["created"] = self.ts_of(int(blk))

    def _sync_v3(self, head: int) -> None:
        start = (self.v3_scanned or 0) + 1
        if start > head:
            return
        for venue, spec in chain.V3_VENUES.items():
            logs = chain.get_logs(spec["factory"], [chain.TOPIC["v3PoolCreated"]], start, head, chunk=200_000, pause=0.02)
            for lg in logs:
                pool, key = chain.decode_pool_created(lg, venue)
                self._touch_created(key)
                self.v3pools[pool] = key
                if chain.quote_side(key) is not None:
                    self.keys.setdefault(pool, key)
                else:
                    self.keys.setdefault(pool, None)
        self.v3_scanned = head

    def _note_err(self, exc: BaseException) -> None:
        with self.lock:
            self.status["error"] = str(exc)[:200]
            self.status["retries"] = int(self.status.get("retries") or 0) + 1
            self.status["lastPoll"] = time.time()

    def _store_key(self, pid: str, key: dict | None) -> None:
        if not key:
            return
        if chain.quote_side(key) is None:
            self.keys[pid] = None
            return
        self._touch_created(key)
        self.keys[pid] = key

    def _resolve_v4(self, ids: list[str]) -> None:
        """Resolve pool keys. A failed lookup is left missing so the next poll retries it.
        Only a decoded key (quote or not) is cached."""
        missing = [i for i in ids if i not in self.keys]
        if not missing:
            return
        for i in range(0, len(missing), 80):
            part = missing[i:i + 80]
            try:
                got = chain.pool_keys(part)
            except Exception as exc:
                self._note_err(exc)
                return
            need = [pid for pid in part if not got.get(pid)]
            found: dict = {}
            searched: set = set()
            if need:
                try:
                    found, searched = chain.v4_keys_from_init(need, head=self.status.get("chainHead") or None, max_requests=6)
                except Exception as exc:
                    self._note_err(exc)
                    searched = set()
            quoted = []
            for pid, key in list(got.items()) + list(found.items()):
                if key:
                    self._store_key(pid, key)
                    if self.keys.get(pid):
                        quoted.append(pid)
            for pid in need:
                if pid in searched and pid not in found and pid not in self.keys:
                    self.keys[pid] = None
            if quoted:
                try:
                    s0 = chain.slot0s_v4(quoted)
                except Exception as exc:
                    self._note_err(exc)
                    s0 = {}
                for pid in quoted:
                    key = self.keys.get(pid)
                    if key:
                        key["pf"] = (s0.get(pid) or {}).get("protocolFee", 0)

    def _fold(self, logs: list[dict], dex: str) -> int:
        n = 0
        for lg in logs:
            if dex == "v4":
                pid = lg["topics"][1].lower()
                bits = 128
            else:
                pid = lg["address"].lower()
                bits = 256
            key = self.keys.get(pid)
            if not key:
                continue
            w = chain.words(lg["data"])
            if len(w) < 5:
                continue
            q = chain.quote_side(key)
            if q is None:
                continue
            qaddr = key["currency0" if q == 0 else "currency1"]
            amt_raw = chain.to_int(w[q], bits, True)
            amt = abs(amt_raw) / 10 ** chain.DECIMALS.get(qaddr, 18)
            a0 = chain.to_int(w[0], bits, True)
            if dex == "v4":
                zero_for_one = a0 < 0
                fee = key["fee"] if not key["fee"] & chain.DYNAMIC_FEE else \
                    lp_fee_v4(chain.to_int(w[5], 24) if len(w) > 5 else 0, key.get("pf", 0), zero_for_one)
                buy = amt_raw < 0          # v4: negative quote delta means the swapper paid the quote
            else:
                zero_for_one = a0 > 0      # v3: positive means the pool received token0
                fee = lp_fee_v3(key["fee"], key.get("pf", 0), zero_for_one)
                buy = amt_raw > 0
            blk = int(lg["blockNumber"], 16)
            b = int(self.ts_of(blk) // BUCKET * BUCKET)
            sender = ""
            topics = lg.get("topics") or []
            if dex == "v4" and len(topics) > 2:
                sender = "0x" + topics[2][-40:]
            elif dex == "v3" and len(topics) > 1:
                sender = "0x" + topics[1][-40:]
            if sender and sender != chain.ZERO:
                book = self.traders.setdefault(pid, {})
                book[sender] = b
            p = self.pools.setdefault(pid, {"b": {}, "last": {}})
            cell = p["b"].setdefault(b, [0, 0.0, 0.0, 0, 0, 0])
            while len(cell) < 6:
                cell.append(0)
            sqrt = chain.to_int(w[2], 160)
            cell[0] += 1
            cell[1] += amt
            cell[2] += amt * fee / 1e6
            cell[3] = sqrt
            cell[4] += 1 if buy else 0
            cell[5] += 0 if buy else 1
            if blk >= p["last"].get("block", 0):
                p["last"] = {"block": blk, "sqrt": str(sqrt), "liq": str(chain.to_int(w[3], 128)),
                             "tick": chain.to_int(w[4], 24, True), "fee": fee}
            n += 1
        return n

    def _chunk_loop(self, start: int, end: int, chunk: int, fetch, fold_dex: str) -> int:
        """Read logs in chunks. head/cursor advances after each chunk that succeeds,
        so a rate limit keeps what was already folded and the next poll continues."""
        a, step, n = start, chunk, 0
        while a <= end:
            b = min(end, a + step - 1)
            try:
                logs = fetch(a, b)
            except chain.RpcError as exc:
                msg = str(exc).lower()
                if any(s in msg for s in ("exceed", "limit", "range", "10000", "more than")) and step > 200:
                    step = max(200, step // 2)
                    with self.lock:
                        self.status["retries"] = int(self.status.get("retries") or 0) + 1
                    continue
                self._note_err(exc)
                break
            if fold_dex == "v4":
                new = []
                seen = set()
                for lg in logs:
                    pid = (lg.get("topics") or ["", ""])[1].lower()
                    if pid and pid not in self.keys and pid not in seen:
                        seen.add(pid)
                        new.append(pid)
                if new:
                    self._resolve_v4(new)
            elif fold_dex == "v3":
                traded = list({lg["address"].lower() for lg in logs if lg["address"].lower() in self.v3pools})
                if traded:
                    try:
                        s0 = chain.slot0s_v3(traded)
                    except Exception as exc:
                        self._note_err(exc)
                        s0 = {}
                    for pid, sl in s0.items():
                        key = self.v3pools.get(pid)
                        if key and sl:
                            key["pf"] = sl.get("feeProtocol", 0)
            with self.lock:
                n += self._fold(logs, fold_dex)
                if fold_dex == "v4":
                    self.head = b
                else:
                    self.v3_log_head = b
                self.status["logs"] = int(self.status.get("logs") or 0) + len(logs)
            a = b + 1
        return n

    def poll(self) -> int:
        head = chain.block_number()
        self._calibrate(head)
        with self.lock:
            self.status["chainHead"] = head
            self.status["error"] = None
        if not self.head or (head - self.head) * self.spb > KEEP:
            self.head = max(1, head - int(self.backfill_hours * 3600 / self.spb))
            self.v3_log_head = self.head
            self.started = self.ts_of(self.head)
        start = self.head + 1
        n = 0
        if start <= head:
            with self.lock:
                self.status["phase"] = "backfill" if (head - self.head) * self.spb > 90 else "live"
            n += self._chunk_loop(
                start, head, 1200,
                lambda a, b: chain.get_logs(chain.V4_POOL_MANAGER, [chain.TOPIC["v4Swap"]], a, b, chunk=b - a + 1, pause=0),
                "v4")
        try:
            self._sync_v3(head)
        except Exception as exc:
            self._note_err(exc)
        v3_start = (self.v3_log_head or self.head) + 1
        if v3_start <= head:
            n += self._chunk_loop(
                v3_start, head, 4000,
                lambda a, b: chain.get_logs(None, [chain.TOPIC["v3Swap"]], a, b, chunk=b - a + 1, pause=0),
                "v3")
        with self.lock:
            cut = time.time() - KEEP - BUCKET
            for pid in list(self.pools):
                bs = self.pools[pid]["b"]
                for bkt in [b for b in bs if b < cut]:
                    del bs[bkt]
                if not bs:
                    del self.pools[pid]
            for pid, book in list(self.traders.items()):
                for addr in [a for a, ts in book.items() if ts < cut]:
                    del book[addr]
                if not book:
                    del self.traders[pid]
            lag = max(0, head - self.head)
            self.status.update(state="live" if lag < 30 else "backfill", lastPoll=time.time(), head=self.head,
                               chainHead=head, lagBlocks=lag, phase="live" if lag < 30 else "backfill")
        return n

    def run_forever(self) -> None:
        last_save = 0.0
        while True:
            try:
                self.poll()
                if time.time() - last_save > 120:
                    self.save()
                    last_save = time.time()
            except Exception as exc:
                with self.lock:
                    self.status.update(error=str(exc)[:200], lastPoll=time.time())
            time.sleep(self.poll_sec)

    def start(self) -> None:
        threading.Thread(target=self.run_forever, daemon=True, name="elysium-swap-index").start()

    def hype_usd(self) -> float | None:
        """USDC per HYPE from the busiest USDC/HYPE or USDC/WHYPE pool in the last hour."""
        best, best_n = None, 0
        now = time.time()
        with self.lock:
            items = [(pid, p, self.keys.get(pid)) for pid, p in self.pools.items()]
        for pid, p, k in items:
            if not k or chain.USDC not in (k["currency0"], k["currency1"]):
                continue
            other = k["currency1"] if k["currency0"] == chain.USDC else k["currency0"]
            if other not in (chain.ZERO, chain.WHYPE):
                continue
            n = sum(c[0] for b, c in p["b"].items() if b >= now - 3600)
            if n > best_n and p["last"].get("sqrt"):
                best, best_n = (k, p), n
        if not best:
            return chain.hype_usd_from_slot()
        k, p = best
        sqrt = int(p["last"]["sqrt"])
        p01 = chain.price_of(sqrt, chain.DECIMALS.get(k["currency0"], 18), chain.DECIMALS.get(k["currency1"], 18))
        if k["currency0"] in (chain.ZERO, chain.WHYPE) and k["currency1"] == chain.USDC:
            return p01
        return (1 / p01) if p01 else None

    eth_usd = hype_usd

    def coverage(self) -> float:
        return max(0.0, min(KEEP, time.time() - self.started))

    def _window(self, buckets: dict, sec: float, now: float, usd: float | None) -> dict:
        n = vq = fq = buys = sells = 0
        sqrt_then = None
        oldest = None
        for b, c in buckets.items():
            if b + BUCKET <= now - sec:
                continue
            n += c[0]
            vq += c[1]
            fq += c[2]
            if len(c) > 4:
                buys += c[4]
                sells += c[5]
            if oldest is None or b < oldest:
                oldest = b
                sqrt_then = c[3] if len(c) > 3 else None
        return {"swaps": n, "volQuote": vq, "feesQuote": fq, "buys": buys, "sells": sells, "sqrtThen": sqrt_then,
                "volUsd": vq * usd if usd else None, "feesUsd": fq * usd if usd else None}

    def rows(self, min_swaps_24h: int = 1) -> list[dict]:
        hype = self.hype_usd()
        now = time.time()
        out = []
        with self.lock:
            items = [(pid, p, self.keys.get(pid), len(self.traders.get(pid) or {})) for pid, p in self.pools.items()]
        for pid, p, key, traders in items:
            if not key:
                continue
            q = chain.quote_side(key)
            if q is None:
                continue
            qaddr = key["currency0" if q == 0 else "currency1"]
            usd = 1.0 if qaddr == chain.USDC else hype
            win = {name: self._window(p["b"], sec, now, usd) for name, sec in WINDOWS.items()}
            if win["24h"]["swaps"] < min_swaps_24h:
                continue
            out.append({"pool": pid, "key": key, "quoteSide": q, "quote": qaddr, "quoteUsd": usd,
                        "last": p["last"], "win": win, "traders1h": traders})
        return out

    def pool_ids(self) -> set[str]:
        with self.lock:
            return {pid for pid in self.pools if self.keys.get(pid)}
