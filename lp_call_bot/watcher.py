"""Auto call: baca transaksi wallet lu di Uniswap V4 (Robinhood Chain), bikin call OPEN / CLOSE sendiri.

Yang diliat dari chain (pakai decoder punya rh_wallet_finder):
  - PositionManager Transfer ke / dari wallet lu: posisi (NFT) dimint / diburn / dikirim.
  - PoolManager ModifyLiquidity punya posisi itu: liquidity naik dari 0 = OPEN, balik ke 0 = CLOSE.
Dari pool-nya dapet: CA koin (sisi yang bukan USDG), simbol, fee pool. Range dihitung dari tick posisi
dibanding harga pool pas dibuka, dalam % harga koin (single sided USDG: -65 = batas bawah 65% di bawah harga).

Satu "posisi" lu biasanya ladder: beberapa NFT (rung) sekaligus dalam satu transaksi di pool yang sama. Jadi
call-nya digabung per (transaksi, pool): satu OPEN dengan range dari rung teratas sampai terbawah, satu CLOSE.
Close + open di transaksi yang sama, pool sama, range sama = satu REENTRY. Catatan: pas burn, NFT diburn dulu
baru liquidity-nya ditarik, jadi NFT yang diburn tetap dilacak sampai liquidity-nya habis.

Pure di luar RPC, jadi bisa dites offline: lihat tests/test_watcher.py.
"""
import copy
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rh_wallet_finder"))

import calls as CALLS  # noqa: E402
import chain as C  # noqa: E402
import config as RH  # noqa: E402
import rpc as R  # noqa: E402
from keccak import keccak256  # noqa: E402

CONFIRMATIONS = 2          # jangan baca block yang paling baru, biar nggak kena reorg
BLOCKS_PER_HOUR = 36_000   # Robinhood Chain ~0.1 detik per block
DYNAMIC_FEE_FLAG = 0x800000
NEAR_ZERO = 1.5            # batas atas range segini dekat harga = nggak usah ditulis
STATE_VERSION = 2          # naik kalau bentuk state berubah: watcher mulai dari awal (backfill diam-diam lagi)


# ---- helper murni ----------------------------------------------------------
def checksum(addr):
    """EIP-55, biar CA-nya persis kayak yang biasa lu kirim (campur huruf besar kecil)."""
    a = addr.lower().removeprefix("0x")
    h = keccak256(a.encode()).hex()
    return "0x" + "".join(c.upper() if c.isalpha() and int(h[i], 16) >= 8 else c for i, c in enumerate(a))


def fmt_fee(fee):
    """Fee V4 (per sejuta) jadi teks %: 25000 -> '2.5', 48800 -> '4.88'."""
    return f"{fee / 10000:.4f}".rstrip("0").rstrip(".")


def _price_pct(ticks):
    return (math.pow(1.0001, ticks) - 1) * 100


def range_pcts(tl, tu, tick, usdg_is_token1):
    """(batas bawah, batas atas) harga koin dalam % dari harga sekarang."""
    if usdg_is_token1:  # koin = token0, harga koin = 1.0001^tick USDG
        return _price_pct(tl - tick), _price_pct(tu - tick)
    return _price_pct(tick - tu), _price_pct(tick - tl)  # koin = token1, harga dibalik


def single_sided_usdg(tl, tu, tick, usdg_is_token1):
    """Posisi cuma megang USDG: seluruh range ada di bawah harga koin."""
    return tu <= tick if usdg_is_token1 else tick < tl


def fmt_pct(x):
    return str(round(x))


def range_label(lo, up):
    """['-65'] normalnya; dua angka ['-60', '-65'] kalau batas atas nggak nempel harga."""
    return [fmt_pct(lo)] if abs(up) <= NEAR_ZERO else [fmt_pct(up), fmt_pct(lo)]


def _pad(addr):
    return "0x" + "0" * 24 + addr.lower().removeprefix("0x")


# ---- watcher ---------------------------------------------------------------
class Watcher:
    """poll() -> call baru sejak poll sebelumnya:
        {"action": "open",    "pos": {coin, ca, fee, range}, "token_ids", "tx"}
        {"action": "reentry", "pos": {coin, ca, fee, range}, "token_ids", "tx"}   (close + open sama, satu transaksi)
        {"action": "close",   "pos": {coin, ca[, fee, range]}, "token_ids", "tx"}
        {"action": "skip",    "text": alasan}    (posisi yang nggak layak di-blast, buat dikabarin ke owner)
    Poll pertama (belum ada cursor) cuma belajar: scan mundur `backfill_hours` tanpa ngirim apa-apa,
    jadi posisi yang udah kebuka dari sebelum bot nyala tetep ketangkep pas ditutup."""

    def __init__(self, logs_rpc, calls_rpc, wallets, store, backfill_hours=72, chunk=20000):
        self.rpc = logs_rpc
        self.calls = calls_rpc
        self.wallets = [w.lower() for w in wallets]
        self.store = store
        self.backfill_blocks = int(backfill_hours * BLOCKS_PER_HOUR)
        self.chunk = chunk
        with store.lock:
            st = store.data.setdefault("watch", {})
            if st.get("wallets") != self.wallets or st.get("v") != STATE_VERSION:  # ganti wallet / versi = dari awal
                st.clear()
                st.update(v=STATE_VERSION, wallets=self.wallets, cursor=None, tokens={}, pools={})
                store.save()

    @property
    def state(self):
        return self.store.data["watch"]

    def status(self):
        st = self.state
        live = [t for t in st["tokens"].values() if t["liq"] > 0]
        return {"wallets": self.wallets, "cursor": st["cursor"], "open": len(live)}

    def poll(self):
        head = int(self.calls.call("eth_blockNumber", []), 16) - CONFIRMATIONS
        silent = self.state["cursor"] is None
        start = max(0, head - self.backfill_blocks) if silent else self.state["cursor"] + 1
        if head < start:
            return []
        tokens, out = self._scan(start, head, silent)
        with self.store.lock:
            self.state["tokens"] = tokens
            self.state["cursor"] = head
            self.store.save()
        return out

    # scan
    def _scan(self, start, end, silent):
        tokens = copy.deepcopy(self.state["tokens"])
        evs, seen = [], set()
        pads = [_pad(w) for w in self.wallets]
        for topics in ([C.TRANSFER, pads, None], [C.TRANSFER, None, pads]):
            logs, self.chunk = self.rpc.get_logs(
                {"address": [RH.POSITION_MANAGER], "topics": topics}, start, end, self.chunk)
            for lg in logs:
                if len(lg["topics"]) != 4:
                    continue
                d = C.decode_nft_transfer(lg)
                if (d["block"], d["log_index"]) not in seen:
                    seen.add((d["block"], d["log_index"]))
                    evs.append((d["block"], d["log_index"], "transfer", d))

        ids = set(tokens) | {str(e[3]["token_id"]) for e in evs if e[3]["to"] in self.wallets}
        if ids:
            logs, self.chunk = self.rpc.get_logs(
                {"address": [RH.POOL_MANAGER], "topics": [C.MODIFY_LIQUIDITY, None, _pad(RH.POSITION_MANAGER)]},
                start, end, self.chunk)
            for lg in logs:
                d = C.decode_modify(lg)
                if str(d["salt"]) in ids:
                    evs.append((d["block"], d["log_index"], "modify", d))

        groups, index = [], {}
        for _, _, kind, d in sorted(evs, key=lambda e: (e[0], e[1])):
            if kind == "transfer":
                self._on_transfer(tokens, d)
            else:
                self._on_modify(tokens, d, groups, index)

        out = [] if silent else self._announce(groups, tokens)
        for tid in [k for k, t in tokens.items() if t.get("burned") and t["liq"] <= 0]:
            del tokens[tid]  # diburn dan liquidity-nya udah habis: selesai
        return tokens, out

    def _on_transfer(self, tokens, d):
        tid = str(d["token_id"])
        if d["to"] in self.wallets:
            tokens.setdefault(tid, {"liq": 0, "blast": None})
        elif d["from"] in self.wallets and tid in tokens:
            if d["to"] == RH.ZERO:
                tokens[tid]["burned"] = True  # liquidity-nya ditarik SETELAH burn: tetap dilacak
            else:
                del tokens[tid]  # dikirim ke wallet lain

    def _on_modify(self, tokens, d, groups, index):
        t = tokens.get(str(d["salt"]))
        if t is None:
            return
        before = t["liq"]
        t["liq"] = before + d["delta"]
        t.update(pool=d["pool"], tl=d["tl"], tu=d["tu"])
        if before <= 0 < t["liq"]:
            kind = "open"
            t.update(block=d["block"], blast=None)
            t.pop("pos", None)
        elif before > 0 >= t["liq"]:
            kind = "close"
        else:
            return
        key = (kind, d["tx"], d["pool"])
        if key not in index:
            index[key] = {"kind": kind, "tx": d["tx"], "pool": d["pool"], "block": d["block"],
                          "li": d["log_index"], "ids": []}
            groups.append(index[key])
        index[key]["ids"].append(str(d["salt"]))

    # call
    def _announce(self, groups, tokens):
        out = []
        pending = sorted(groups, key=lambda g: (g["block"], g["kind"] != "close", g["li"]))
        used = set()
        for g in pending:
            if id(g) in used:
                continue
            if g["kind"] == "close":
                mate = next((o for o in pending if o["kind"] == "open" and o["tx"] == g["tx"]
                             and o["pool"] == g["pool"] and id(o) not in used), None)
                out += self._close(g, tokens, mate, used)
            else:
                out += self._open(g, tokens)
        return out

    def _ladder(self, info, ids, tokens):
        """Gabungan rung: ((lo, up), tick-set)."""
        los, ups = [], []
        for tid in ids:
            t = tokens[tid]
            lo, up = range_pcts(t["tl"], t["tu"], t["tick"], info["usdg_token1"])
            los.append(lo)
            ups.append(up)
        return min(los), max(ups)

    def _open(self, g, tokens):
        """-> [event] ; event open, atau skip kalau posisinya nggak layak."""
        ids = g["ids"]
        info = self._pool(g["pool"])
        name = info.get("coin", "?").upper()
        label = f"#{ids[0]}" + (f" (+{len(ids) - 1} lainnya)" if len(ids) > 1 else "")
        for tid in ids:
            tokens[tid]["blast"] = False
        if "coin" not in info:
            return [{"action": "skip", "text": f"Posisi {label} dibuka di pool tanpa USDG, nggak di-blast."}]
        slot = self._slot0(g["pool"], g["block"])
        for tid in ids:
            tokens[tid]["tick"] = slot["tick"]
        if not all(single_sided_usdg(tokens[t]["tl"], tokens[t]["tu"], slot["tick"], info["usdg_token1"]) for t in ids):
            return [{"action": "skip", "text": f"Posisi {label} ({name}) bukan single sided USDG, nggak di-blast."}]
        lo, up = self._ladder(info, ids, tokens)
        fee = slot["lp_fee"] if info["dynamic"] else info["fee"]
        pos = {"coin": info["coin"], "ca": info["ca"], "fee": fmt_fee(fee), "range": range_label(lo, up)}
        for tid in ids:
            tokens[tid].update(blast=True, pos=pos)
        return [{"action": "open", "token_ids": ids, "tx": g["tx"], "pos": pos}]

    def _close(self, g, tokens, mate, used):
        ids = g["ids"]
        info = self._pool(g["pool"])
        used.add(id(g))
        live = [t for t in ids if tokens[t].get("blast") is not False]
        if "coin" not in info or not live:
            if not mate:
                return []
            used.add(id(mate))
            return self._open(mate, tokens)
        old = next((tokens[t]["pos"] for t in live if tokens[t].get("pos")), None) or self._old_pos(info, g, live, tokens)
        close_ev = {"action": "close", "token_ids": live, "tx": g["tx"],
                    "pos": old or {"coin": info["coin"], "ca": info["ca"]}}
        for t in live:
            tokens[t]["blast"] = False
        if not mate:
            return [close_ev]
        used.add(id(mate))
        evs = self._open(mate, tokens)
        new = evs[0]
        ticks = lambda ids: sorted((tokens[t]["tl"], tokens[t]["tu"]) for t in ids)  # noqa: E731
        same = ticks(live) == ticks(mate["ids"]) or bool(old and calls_same(old, new["pos"]))
        if new["action"] == "open" and same:
            return [dict(new, action="reentry")]
        return [close_ev] + evs

    def _old_pos(self, info, g, ids, tokens):
        """Fee + range posisi yang kebuka dari sebelum bot nyala: harga pool pas dibuka (butuh RPC arsip)."""
        t0 = tokens[ids[0]]
        if not t0.get("block"):
            return None
        try:
            slot = self._slot0(g["pool"], t0["block"], exact=True)
        except (R.RPCError, RuntimeError):
            return None
        for tid in ids:
            tokens[tid]["tick"] = slot["tick"]
        lo, up = self._ladder(info, ids, tokens)
        fee = slot["lp_fee"] if info["dynamic"] else info["fee"]
        return {"coin": info["coin"], "ca": info["ca"], "fee": fmt_fee(fee), "range": range_label(lo, up)}

    # baca chain
    def _pool(self, pool):
        pools = self.state["pools"]
        if pool in pools:
            return pools[pool]
        key = C.decode_pool_key(self.calls.call(
            "eth_call", [{"to": RH.POSITION_MANAGER, "data": C.cd_pool_keys(pool)}, "latest"]))
        if key is None:
            raise RuntimeError(f"poolKeys kosong buat pool {pool}")
        c0, c1 = key["c0"].lower(), key["c1"].lower()
        info = {"fee": key["fee"], "dynamic": bool(key["fee"] & DYNAMIC_FEE_FLAG)}
        coin = c1 if c0 == RH.USDG else c0
        if RH.USDG in (c0, c1) and coin != RH.ZERO:
            sym = C.decode_string(self.calls.call(
                "eth_call", [{"to": coin, "data": C.cd_simple(C.SEL_SYMBOL)}, "latest"]))
            info.update(coin=sym.strip().lower().replace(" ", "-") or coin[2:8],
                        ca=checksum(coin), usdg_token1=(c1 == RH.USDG))
        with self.store.lock:
            pools[pool] = info
        return info

    def _slot0(self, pool, block, exact=False):
        for tag in (hex(block),) if exact else (hex(block), "latest"):  # RPC tanpa arsip nolak block lama
            try:
                slot = C.decode_slot0(self.calls.call(
                    "eth_call", [{"to": RH.STATE_VIEW, "data": C.cd_slot0(pool)}, tag]))
            except R.RPCError:
                continue
            if slot:
                return slot
        raise RuntimeError(f"slot0 pool {pool} nggak kebaca")


def calls_same(old, new):
    return old["ca"].lower() == new["ca"].lower() and CALLS.same_range(old["range"], new["range"])
