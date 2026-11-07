"""On-chain swap index: every Uniswap V4 pool quoted in USDG or ETH that is trading now.

The PoolManager emits one Swap event per swap with the pool id, both amounts, the price
after the swap and the fee charged. Reading those events straight from the chain gives:

  * every pool with trades, however small: no top-200 ranking to fall out of,
  * the fee each pool actually charged (dynamic-fee pools included),
  * volume and fees per 5 minutes, summed to 5m / 1h / 6h / 24h windows.

Robinhood Chain runs ~10 blocks a second and ~25k swaps an hour; one poll of the blocks
since the last one is a single eth_getLogs of a couple of MB on the free public node.
The index keeps 24 hours of 5-minute buckets on disk (data/activity.json), so a restart
picks up where it left off. On a fresh start it backfills `backfillHours` (default 1).

Volume is the quote side of each swap (USDG, or ETH/WETH priced in USDG from the most
active ETH/USDG pool). Fee = |quote amount| x the swap's fee: exact when the quote is the
input, within the fee itself when the token is.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import chain

BUCKET = 300                     # seconds
KEEP = 24 * 3600
DYNAMIC_FEE = 0x800000
WINDOWS = {"5m": 300, "1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}


def lp_fee(swap_fee: int, protocol_fee: int, zero_for_one: bool) -> int:
    """The LP's part of a swap's fee, in hundredths of a bip. V4 charges
    swapFee = pf + lp - pf*lp/1e6, with pf per direction (low 12 bits: 0->1, high: 1->0).
    A swapper paying currency0 (amount0 < 0) is a 0->1 swap."""
    pf = (protocol_fee & 0xFFF) if zero_for_one else (protocol_fee >> 12) & 0xFFF
    if pf <= 0 or swap_fee <= pf:
        return max(0, swap_fee - pf)
    return round((swap_fee - pf) * 1e6 / (1e6 - pf))


class Index:
    def __init__(self, data_dir: Path, backfill_hours: float = 1.0, poll_sec: float = 30.0):
        self.dir = Path(data_dir)
        self.backfill_hours = backfill_hours
        self.poll_sec = poll_sec
        self.lock = threading.Lock()
        self.head = 0                 # last block folded in
        self.ref = None               # (block, ts) for the block -> time estimate
        self.spb = 0.104              # seconds per block, re-measured
        self.started = time.time()    # first second the index has data for
        self.pools: dict[str, dict] = {}     # id -> {"b": {bucket: [n, vq, fq]}, "last": {...}}
        self.keys: dict[str, dict | None] = {}   # id -> pool key, None = not a quote pool / unknown
        self.status = {"state": "starting", "error": None, "lastPoll": 0, "logs": 0}
        self._load()

    # ------------------------------------------------------------ disk ----
    def _load(self) -> None:
        try:
            k = json.loads((self.dir / "poolkeys.json").read_text())
            self.keys = {i: v for i, v in k.items()}
        except (OSError, ValueError):
            pass
        try:
            d = json.loads((self.dir / "activity.json").read_text())
            if time.time() - d.get("savedAt", 0) < KEEP:
                self.head, self.started = d["head"], d["started"]
                self.ref, self.spb = tuple(d["ref"]) if d.get("ref") else None, d.get("spb", self.spb)
                self.pools = {p: {"b": {int(b): v for b, v in x["b"].items()}, "last": x.get("last") or {}}
                              for p, x in d["pools"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            snap = {"savedAt": time.time(), "head": self.head, "started": self.started, "ref": self.ref,
                    "spb": self.spb, "pools": self.pools}
            keys = dict(self.keys)
        tmp = self.dir / "activity.json.tmp"
        tmp.write_text(json.dumps(snap, separators=(",", ":")))
        tmp.replace(self.dir / "activity.json")
        (self.dir / "poolkeys.json").write_text(json.dumps(keys, separators=(",", ":")))

    # ------------------------------------------------------------ time ----
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

    # ------------------------------------------------------------ poll ----
    def poll(self) -> int:
        """Fold every swap since the last poll into the buckets. Returns swaps folded."""
        head = chain.block_number()
        self._calibrate(head)
        if not self.head or (head - self.head) * self.spb > KEEP:
            self.head = head - int(self.backfill_hours * 3600 / self.spb)
            self.started = self.ts_of(self.head)
        start = self.head + 1
        if start > head:
            return 0
        logs = chain.get_logs(chain.POOL_MANAGER, [chain.TOPIC["Swap"]], start, head)
        new = list({l["topics"][1] for l in logs if l["topics"][1] not in self.keys})
        if new:
            for i in range(0, len(new), 300):
                part = new[i:i + 300]
                got = chain.pool_keys(part)
                quoted = [pid for pid, key in got.items() if key and chain.quote_side(key) is not None]
                # The protocol's cut is inside the fee a Swap event reports; slot0 says how big it is.
                s0 = chain.slot0s(quoted) if quoted else {}
                with self.lock:
                    for pid, key in got.items():
                        if pid in quoted:
                            key["pf"] = (s0.get(pid) or {}).get("protocolFee", 0)
                        self.keys[pid] = key if pid in quoted else None
        with self.lock:
            for l in logs:
                pid = l["topics"][1]
                key = self.keys.get(pid)
                if not key:
                    continue
                w = chain.words(l["data"])
                if len(w) < 6:
                    continue
                q = chain.quote_side(key)
                amt = abs(chain.to_int(w[q], 128, True)) / 10 ** chain.DECIMALS[key["currency0" if q == 0 else "currency1"]]
                # A static fee tier is exactly what LPs earn; only a dynamic one (0x800000 flag) is
                # read from the event, less the protocol's cut.
                fee = key["fee"] if not key["fee"] & DYNAMIC_FEE else \
                    lp_fee(chain.to_int(w[5], 24), key.get("pf", 0), chain.to_int(w[0], 128, True) < 0)
                blk = int(l["blockNumber"], 16)
                b = int(self.ts_of(blk) // BUCKET * BUCKET)
                p = self.pools.setdefault(pid, {"b": {}, "last": {}})
                cell = p["b"].setdefault(b, [0, 0.0, 0.0])
                cell[0] += 1
                cell[1] += amt
                cell[2] += amt * fee / 1e6
                if blk >= p["last"].get("block", 0):
                    p["last"] = {"block": blk, "sqrt": str(chain.to_int(w[2], 160)), "liq": str(chain.to_int(w[3], 128)),
                                 "tick": chain.to_int(w[4], 24, True), "fee": fee}
            self.head = head
            cut = time.time() - KEEP - BUCKET
            for pid in list(self.pools):
                bs = self.pools[pid]["b"]
                for b in [b for b in bs if b < cut]:
                    del bs[b]
                if not bs:
                    del self.pools[pid]
            self.status.update(state="live", error=None, lastPoll=time.time(), logs=len(logs), head=head)
        return len(logs)

    def run_forever(self) -> None:
        last_save = 0.0
        while True:
            try:
                self.poll()
                if time.time() - last_save > 120:
                    self.save()
                    last_save = time.time()
            except Exception as exc:          # keep serving the last good numbers
                with self.lock:
                    self.status.update(error=str(exc)[:200], lastPoll=time.time())
            time.sleep(self.poll_sec)

    def start(self) -> None:
        threading.Thread(target=self.run_forever, daemon=True, name="swap-index").start()

    # ------------------------------------------------------------ read ----
    def eth_usd(self) -> float | None:
        """USDG per ETH from the busiest ETH (or WETH) / USDG pool in the last hour."""
        best, best_n = None, 0
        now = time.time()
        with self.lock:
            for pid, p in self.pools.items():
                k = self.keys.get(pid)
                if not k or k["currency1"] != chain.USDG or k["currency0"] not in (chain.ZERO, chain.WETH):
                    continue
                n = sum(c[0] for b, c in p["b"].items() if b >= now - 3600)
                if n > best_n and p["last"].get("sqrt"):
                    best, best_n = p, n
        if not best:
            return None
        return chain.price_of(int(best["last"]["sqrt"]), 18, 6)

    def coverage(self) -> float:
        """Seconds of history the index holds (capped at 24 h)."""
        return max(0.0, min(KEEP, time.time() - self.started))

    def rows(self, min_swaps_24h: int = 1) -> list[dict]:
        """One dict per quote pool with trades: key, last price, and per-window counts/volume/fees
        in USD (ETH converted at eth_usd)."""
        eth = self.eth_usd()
        now = time.time()
        out = []
        with self.lock:
            items = [(pid, p, self.keys.get(pid)) for pid, p in self.pools.items()]
        for pid, p, key in items:
            if not key:
                continue
            q = chain.quote_side(key)
            qaddr = key["currency0" if q == 0 else "currency1"]
            usd = 1.0 if qaddr == chain.USDG else eth
            win = {}
            for name, sec in WINDOWS.items():
                n = vq = fq = 0
                for b, c in p["b"].items():
                    if b + BUCKET > now - sec:
                        n += c[0]
                        vq += c[1]
                        fq += c[2]
                win[name] = {"swaps": n, "volQuote": vq, "feesQuote": fq,
                             "volUsd": vq * usd if usd else None, "feesUsd": fq * usd if usd else None}
            if win["24h"]["swaps"] < min_swaps_24h:
                continue
            out.append({"pool": pid, "key": key, "quoteSide": q, "quote": qaddr, "quoteUsd": usd,
                        "last": p["last"], "win": win})
        return out

    def pool_ids(self) -> set[str]:
        with self.lock:
            return {pid for pid in self.pools if self.keys.get(pid)}
