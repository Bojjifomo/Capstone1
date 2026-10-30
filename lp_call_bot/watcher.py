"""Auto call: baca transaksi wallet lu di Uniswap V4 (Robinhood Chain), bikin call OPEN / CLOSE sendiri.

Real time, nggak ada baca-mundur: tiap poll cuma baca block baru sejak poll sebelumnya.
Yang diliat (pakai decoder punya rh_wallet_finder):
  - PositionManager Transfer dari 0x0 ke wallet lu  = posisi dibuka (mint)  -> OPEN
  - PositionManager Transfer dari wallet lu ke 0x0  = posisi ditutup (burn) -> CLOSE
  - dari receipt transaksi itu: ModifyLiquidity NFT-nya, buat pool + tick.
Dicek di 72 jam wallet asli: 35 dari 35 close ikut nge-burn NFT, jadi nggak perlu tau posisi lama.
Dari pool dapet: CA koin (sisi yang bukan USDG), simbol, fee pool. Range = kedalaman ladder dari ujung atas
sampai ujung bawahnya, dalam % harga koin, dibulatin ke kelipatan `range_step` (5): tool LP lu nyetel tick per
rung dengan tick spacing, jadi -70 yang lu ketik jadi -71.3% di chain. Nggak tergantung harga pool, jadi nggak
geser walau harga gerak antara mint dan poll. Plus batas harga ladder (USDG per koin, atas -> bawah): itu yang
bikin range follower sama persis dengan lu walau mereka buka di jam yang beda.

Satu "posisi" lu biasanya ladder: beberapa NFT (rung) sekaligus dalam satu transaksi di pool yang sama. Jadi
call-nya digabung per (transaksi, pool): satu OPEN dengan range dari rung teratas sampai terbawah, satu CLOSE.
Reentry = CLOSE lalu OPEN lagi (dua pesan), nggak ada logika khusus.

Dites offline: lihat tests/test_watcher.py.
"""
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rh_wallet_finder"))

import chain as C  # noqa: E402
import config as RH  # noqa: E402
import rpc as R  # noqa: E402
from keccak import keccak256  # noqa: E402

PUBLIC_RPC = "https://rpc.mainnet.chain.robinhood.com"
CONFIRMATIONS = 2          # jangan baca block yang paling baru, biar nggak kena reorg
BLOCKS_PER_MINUTE = 600    # Robinhood Chain ~0.1 detik per block
DYNAMIC_FEE_FLAG = 0x800000
TICK_SLACK = 100           # ~1%: harga bisa gerak dikit antara block mint dan poll
STATE_VERSION = 4          # naik kalau bentuk state berubah: watcher mulai dari "sekarang" lagi


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


def ladder_depth(tl, tu):
    """Kedalaman range [tl, tu] dalam % harga koin, dari ujung atas ke ujung bawah (sama buat dua urutan token)."""
    return _price_pct(-(tu - tl))


def single_sided_usdg(tl, tu, tick, usdg_is_token1):
    """Posisi cuma megang USDG: seluruh range ada di bawah harga koin (toleransi TICK_SLACK)."""
    return tu <= tick + TICK_SLACK if usdg_is_token1 else tick - TICK_SLACK < tl


def coin_price(tick, usdg_is_token1, dec_coin, dec_usdg):
    """Harga koin dalam USDG di sebuah tick."""
    return math.pow(1.0001, tick if usdg_is_token1 else -tick) * 10 ** (dec_coin - dec_usdg)


def fmt_price(x):
    """4 angka penting, tanpa notasi e: 0.00003530, 0.002535, 1.234, 1234."""
    if x <= 0:
        return "0"
    decimals = max(0, 3 - math.floor(math.log10(x)))
    return f"{x:.{decimals}f}"


def fmt_step(x, step=5):
    """-71.3 -> '-70' (step 5); step 1 = angka persis dibulatin."""
    return str(int(step * round(x / step)))


def _pad(addr):
    return "0x" + "0" * 24 + addr.lower().removeprefix("0x")


# ---- watcher ---------------------------------------------------------------
class Watcher:
    """poll() -> call baru sejak poll sebelumnya:
        {"action": "open",  "pos": {coin, ca, fee, range}, "token_ids", "tx"}
        {"action": "close", "pos": {coin, ca},             "token_ids", "tx"}
        {"action": "skip",  "text": alasan}    (buat dikabarin ke owner, nggak di-blast)
    Poll pertama cuma nyatet block sekarang. Kalau bot mati lebih lama dari `catchup_minutes`, aktivitas yang
    lebih lama dari itu dilewatin (daripada ngirim call basi ke follower) dan owner dikabarin."""

    def __init__(self, logs_rpc, calls_rpc, wallets, store, catchup_minutes=30, range_step=5, chunk=20000):
        self.rpc = logs_rpc
        self.calls = calls_rpc
        self.wallets = [w.lower() for w in wallets]
        self.store = store
        self.catchup_blocks = int(catchup_minutes * BLOCKS_PER_MINUTE)
        self.range_step = range_step
        self.chunk = chunk
        with store.lock:
            st = store.data.setdefault("watch", {})
            if st.get("wallets") != self.wallets or st.get("v") != STATE_VERSION:  # ganti wallet / versi = dari awal
                st.clear()
                st.update(v=STATE_VERSION, wallets=self.wallets, cursor=None, pools={})
                store.save()

    @property
    def state(self):
        return self.store.data["watch"]

    def status(self):
        return {"wallets": self.wallets, "cursor": self.state["cursor"]}

    def poll(self):
        head = int(self.calls.call("eth_blockNumber", []), 16) - CONFIRMATIONS
        cursor = self.state["cursor"]
        out = []
        if cursor is not None and head > cursor:
            start = cursor + 1
            if head - cursor > self.catchup_blocks:
                start = head - self.catchup_blocks + 1
                out.append({"action": "skip", "text": "Bot sempat mati lebih dari "
                            f"{self.catchup_blocks // BLOCKS_PER_MINUTE} menit: aktivitas wallet sebelum itu dilewatin, "
                            "nggak di-blast."})
            out += self._scan(start, head)
        with self.store.lock:
            self.state["cursor"] = max(head, cursor or 0)
            self.store.save()
        return out

    def _scan(self, start, end):
        pads = [_pad(w) for w in self.wallets]
        transfers, seen = [], set()
        for topics in ([C.TRANSFER, pads, None], [C.TRANSFER, None, pads]):
            logs, self.chunk = self.rpc.get_logs(
                {"address": [RH.POSITION_MANAGER], "topics": topics}, start, end, self.chunk)
            for lg in logs:
                if len(lg["topics"]) != 4:
                    continue
                d = C.decode_nft_transfer(lg)
                if (d["block"], d["log_index"]) not in seen:
                    seen.add((d["block"], d["log_index"]))
                    transfers.append(d)

        txs = {}  # tx -> {"block", "li", "mint": {id}, "burn": {id}}
        for d in sorted(transfers, key=lambda d: (d["block"], d["log_index"])):
            kind = ("mint" if d["from"] == RH.ZERO and d["to"] in self.wallets
                    else "burn" if d["to"] == RH.ZERO and d["from"] in self.wallets else None)
            if kind:
                t = txs.setdefault(d["tx"], {"block": d["block"], "li": d["log_index"], "mint": set(), "burn": set()})
                t[kind].add(d["token_id"])
        out = []
        for tx, t in sorted(txs.items(), key=lambda kv: (kv[1]["block"], kv[1]["li"])):
            out += self._tx(tx, t)
        return out

    def _tx(self, tx, t):
        """Satu transaksi: CLOSE per pool (NFT diburn) dulu, baru OPEN per pool (NFT dimint)."""
        receipt = self.calls.call("eth_getTransactionReceipt", [tx])
        if not receipt:
            raise RuntimeError(f"receipt {tx} belum ada")
        closes, opens = {}, {}  # pool -> [(token_id, tl, tu)]
        for lg in receipt["logs"]:
            if lg["address"].lower() != RH.POOL_MANAGER or not lg["topics"] or lg["topics"][0] != C.MODIFY_LIQUIDITY:
                continue
            d = C.decode_modify(lg)
            if d["sender"] != RH.POSITION_MANAGER:
                continue
            if d["delta"] < 0 and d["salt"] in t["burn"]:
                closes.setdefault(d["pool"], []).append((d["salt"], d["tl"], d["tu"]))
            elif d["delta"] > 0 and d["salt"] in t["mint"]:
                opens.setdefault(d["pool"], []).append((d["salt"], d["tl"], d["tu"]))
        out = []
        for pool, rungs in closes.items():
            out += self._close(tx, pool, rungs)
        for pool, rungs in opens.items():
            out += self._open(tx, pool, t["block"], rungs)
        return out

    def _close(self, tx, pool, rungs):
        info = self._pool(pool)
        if "coin" not in info:
            return []  # pool tanpa USDG: bukan strategi lu
        return [{"action": "close", "token_ids": [r[0] for r in rungs], "tx": tx,
                 "pos": {"coin": info["coin"], "ca": info["ca"]}}]

    def _open(self, tx, pool, block, rungs):
        ids = [r[0] for r in rungs]
        info = self._pool(pool)
        label = f"#{ids[0]}" + (f" (+{len(ids) - 1} lainnya)" if len(ids) > 1 else "")
        if "coin" not in info:
            return [{"action": "skip", "text": f"Posisi {label} dibuka di pool tanpa USDG, nggak di-blast."}]
        slot = self._slot0(pool, block)
        if not all(single_sided_usdg(tl, tu, slot["tick"], info["usdg_token1"]) for _, tl, tu in rungs):
            return [{"action": "skip", "text": f"Posisi {label} ({info['coin'].upper()}) bukan single sided USDG, nggak di-blast."}]
        tl, tu = min(r[1] for r in rungs), max(r[2] for r in rungs)
        prices = sorted(coin_price(t, info["usdg_token1"], info["dec_coin"], info["dec_usdg"]) for t in (tl, tu))
        fee = slot["lp_fee"] if info["dynamic"] else info["fee"]
        return [{"action": "open", "token_ids": ids, "tx": tx,
                 "pos": {"coin": info["coin"], "ca": info["ca"], "fee": fmt_fee(fee),
                         "range": [fmt_step(ladder_depth(tl, tu), self.range_step)],
                         "price_hi": fmt_price(prices[1]), "price_lo": fmt_price(prices[0]), "rungs": len(rungs)}}]

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
                        ca=checksum(coin), usdg_token1=(c1 == RH.USDG),
                        dec_coin=self._decimals(coin), dec_usdg=self._decimals(RH.USDG))
        with self.store.lock:
            pools[pool] = info
        return info

    def _decimals(self, token):
        return int(self.calls.call("eth_call", [{"to": token, "data": C.cd_simple(C.SEL_DECIMALS)}, "latest"]), 16)

    def _slot0(self, pool, block):
        for tag in (hex(block), "latest"):  # RPC tanpa arsip nolak block lama
            try:
                slot = C.decode_slot0(self.calls.call(
                    "eth_call", [{"to": RH.STATE_VIEW, "data": C.cd_slot0(pool)}, tag]))
            except R.RPCError:
                continue
            if slot:
                return slot
        raise RuntimeError(f"slot0 pool {pool} nggak kebaca")
