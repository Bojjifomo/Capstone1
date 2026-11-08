"""Offline tests: python3 -m unittest discover -s lp_call_bot/tests"""
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot as B  # noqa: E402
import watcher as W  # noqa: E402
from store import Store  # noqa: E402

C, RH = W.C, W.RH
WALLET = "0x" + "d1" * 20
OTHER = "0x" + "e2" * 20
COIN = "0x127cdc74e44e3c60422b7e792ebe480c0f873ed5"
POOL = "0x" + "11" * 32
POOL2 = "0x" + "22" * 32
FEE = 25000  # 2.5%
STEP = 2100  # tick per rung: 5 rung = ~ -65%


def tx_hash(n):
    return "0x" + format(n, "02x") * 32


def pad(a):
    return "0x" + "0" * 24 + a.lower().removeprefix("0x")


def transfer(block, li, frm, to, tid, tx):
    return {"address": RH.POSITION_MANAGER, "blockNumber": hex(block), "logIndex": hex(li),
            "transactionHash": tx, "data": "0x",
            "topics": [C.TRANSFER, pad(frm), pad(to), "0x" + format(tid, "064x")]}


def modify(block, li, pool, tl, tu, delta, salt, tx):
    return {"address": RH.POOL_MANAGER, "blockNumber": hex(block), "logIndex": hex(li),
            "transactionHash": tx, "topics": [C.MODIFY_LIQUIDITY, pool, pad(RH.POSITION_MANAGER)],
            "data": "0x" + C.word(tl) + C.word(tu) + C.word(delta) + C.word(salt)}


def matches(log, flt):
    if log["address"].lower() not in [a.lower() for a in flt["address"]]:
        return False
    for want, got in zip(flt["topics"], log["topics"]):
        if want is None:
            continue
        want = [want] if isinstance(want, str) else want
        if got.lower() not in [w.lower() for w in want]:
            return False
    return True


def abi_string(s):
    raw = s.encode()
    return "0x" + C.word(32) + C.word(len(raw)) + raw.hex().ljust(64, "0")


class FakeChain:
    """Cukup mirip RPC asli buat watcher: logs terfilter, receipt, poolKeys, slot0, symbol."""

    def __init__(self, usdg_token1=True, archive=True):
        self.archive = archive  # False: RPC publik, cuma tau harga "latest"
        self.head = 1000
        self.logs = []
        self.ticks = {POOL: 10000, POOL2: 10000}
        usdg = RH.USDG
        c0, c1 = (COIN, usdg) if usdg_token1 else (usdg, COIN)
        self.keys = {POOL[2:52]: (c0, c1, FEE), POOL2[2:52]: (RH.WETH, COIN, 3000)}  # POOL2: tanpa USDG
        self.missing_receipts = set()
        self.burned, self.owners, self.down = set(), {}, False
        self.calls = []

    def call(self, method, params):
        self.calls.append(method)
        if method == "eth_blockNumber":
            return hex(self.head + W.CONFIRMATIONS)
        if method == "eth_getTransactionReceipt":
            if params[0] in self.missing_receipts:
                return None
            return {"logs": [lg for lg in self.logs
                             if lg["transactionHash"] == params[0] and int(lg["blockNumber"], 16) <= self.head]}
        assert method == "eth_call"
        tx = params[0]
        sel, arg = tx["data"][2:10], tx["data"][10:]
        if tx["to"] == RH.POSITION_MANAGER and sel == C.SEL_POOL_KEYS:
            c0, c1, fee = self.keys[arg[:50]]
            return "0x" + C.addr_word(c0) + C.addr_word(c1) + C.word(fee) + C.word(60) + C.addr_word(RH.ZERO)
        if tx["to"] == RH.STATE_VIEW and sel == C.SEL_GET_SLOT0:
            if not self.archive and params[1] != "latest":
                raise W.R.RPCError("missing trie node")
            return "0x" + C.word(1 << 96) + C.word(self.ticks["0x" + arg[:64]]) + C.word(0) + C.word(FEE)
        if tx["to"] == RH.POSITION_MANAGER and sel == W.SEL_OWNER_OF:
            tid = int(arg, 16)
            if tid in self.burned:
                raise W.R.RPCError("eth_call: execution reverted")
            if self.down:
                raise W.R.TransportError("rpc down")
            return "0x" + C.addr_word(self.owners.get(tid, WALLET))
        if sel == C.SEL_SYMBOL:
            return abi_string("HYDX")
        raise AssertionError(tx)

    def get_logs(self, flt, start, end, chunk, progress=None):
        got = [lg for lg in self.logs if start <= int(lg["blockNumber"], 16) <= end and matches(lg, flt)]
        return sorted(got, key=lambda lg: (int(lg["blockNumber"], 16), int(lg["logIndex"], 16))), chunk


def lower_tick(pct, tick=10000):
    """Tick batas bawah `pct`% di bawah harga (-65 -> 65% lebih murah), spacing 60."""
    return math.floor((tick + math.log(1 + pct / 100) / math.log(1.0001)) / 60) * 60


def ladder(ids, tx, block, delta=5_000_000, pool=POOL, top=10000, step=STEP):
    """Rung dari harga turun: [top-step, top], [top-2*step, top-step], ... (USDG = token1)."""
    return [modify(block, 10 + i, pool, top - (i + 1) * step, top - i * step, delta, t, tx)
            for i, t in enumerate(ids)]


def mint(ids, tx, block, wallet=WALLET):
    return [transfer(block, i, RH.ZERO, wallet, t, tx) for i, t in enumerate(ids)]


def burn(ids, tx, block, wallet=WALLET):
    return [transfer(block, i, wallet, RH.ZERO, t, tx) for i, t in enumerate(ids)]


def actions(evs):
    return [(e["action"], e["pos"]["coin"]) if "pos" in e else (e["action"],) for e in evs]


class PureTest(unittest.TestCase):
    def test_checksum_vector(self):
        self.assertEqual(W.checksum("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"),
                         "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")

    def test_fee(self):
        self.assertEqual([W.fmt_fee(f) for f in (25000, 48800, 3000, 50000, 10000)],
                         ["2.5", "4.88", "0.3", "5", "1"])

    def test_ladder_depth_and_rounding(self):
        self.assertAlmostEqual(W.ladder_depth(10000 - 5 * 2100, 10000), -65.0, places=1)
        self.assertAlmostEqual(W.ladder_depth(337000, 349500), -71.35, places=1)  # FOREVER asli: "-70"
        self.assertEqual([W.fmt_step(x) for x in (-63.2, -66.7, -65.0, -71.3, -59.3)],
                         ["-65", "-65", "-65", "-70", "-60"])  # hydx, grift, boomer, forever, rebate
        self.assertEqual(W.fmt_step(-71.3, 1), "-71")

class WatcherTest(unittest.TestCase):
    def make(self, usdg_token1=True, archive=True, catchup_minutes=30):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chain = FakeChain(usdg_token1, archive)
        self.store = Store(Path(self.tmp.name) / "state.json")
        self.w = W.Watcher(self.chain, self.chain, [WALLET], self.store, catchup_minutes=catchup_minutes)
        return self.w

    def start(self, **kw):
        """Nyalain watcher: poll pertama cuma nyatet 'sekarang', terus 50 block lewat."""
        self.make(**kw)
        self.assertEqual(self.w.poll(), [])
        self.chain.head += 50

    def test_first_poll_ignores_history_and_does_no_scan(self):
        self.make()
        self.chain.logs += mint([1], tx_hash(1), 900) + ladder([1], tx_hash(1), 900)
        self.assertEqual(self.w.poll(), [])
        self.assertNotIn("eth_getTransactionReceipt", self.chain.calls)
        self.assertEqual(self.w.status()["cursor"], 1000)

    def test_open_then_close(self):
        self.start()
        tx = tx_hash(2)
        self.chain.logs += mint([7], tx, 1010) + [modify(1010, 5, POOL, lower_tick(-65), 10000, 5_000_000, 7, tx)]
        evs = self.w.poll()
        self.assertEqual(actions(evs), [("open", "hydx")])
        self.assertEqual(evs[0]["pos"], {"coin": "hydx", "ca": W.checksum(COIN), "fee": "2.5", "range": ["-65"]})
        self.assertEqual(self.w.poll(), [])  # nggak dobel

        self.chain.head += 50
        tx2 = tx_hash(3)
        self.chain.logs += burn([7], tx2, 1070) + [modify(1070, 5, POOL, lower_tick(-65), 10000, -5_000_000, 7, tx2)]
        evs = self.w.poll()
        self.assertEqual(actions(evs), [("close", "hydx")])
        self.assertEqual(evs[0]["pos"], {"coin": "hydx", "ca": W.checksum(COIN)})

    def test_ladder_is_one_call_with_overall_range(self):
        self.start()
        ids, tx = [101, 102, 103, 104, 105], tx_hash(2)
        self.chain.logs += mint(ids, tx, 1010) + ladder(ids, tx, 1010)
        evs = self.w.poll()
        self.assertEqual(actions(evs), [("open", "hydx")])
        self.assertEqual(evs[0]["pos"]["range"], ["-65"])
        self.assertEqual(evs[0]["token_ids"], ids)

        self.chain.head += 50
        tx2 = tx_hash(3)
        self.chain.logs += burn(ids, tx2, 1070) + ladder(ids, tx2, 1070, delta=-5_000_000)
        evs = self.w.poll()
        self.assertEqual(actions(evs), [("close", "hydx")])
        self.assertEqual(evs[0]["token_ids"], ids)

    def test_reentry_in_one_tx_is_close_then_open(self):
        """Persis kejadian di chain: NFT lama diburn + NFT baru dimint di transaksi yang sama."""
        self.start()
        old, new, tx = [201, 202, 203], [301, 302, 303], tx_hash(4)
        self.chain.logs += (burn(old, tx, 1010) + [transfer(1010, 3 + i, RH.ZERO, WALLET, t, tx) for i, t in enumerate(new)]
                            + ladder(old, tx, 1010, delta=-5_000_000) + ladder(new, tx, 1010))
        evs = self.w.poll()
        self.assertEqual(actions(evs), [("close", "hydx"), ("open", "hydx")])
        self.assertEqual(evs[1]["token_ids"], new)

    def test_range_ignores_price_move_between_mint_and_poll(self):
        """Persis FOREVER jam 14.38: harga udah naik ~5% di atas ladder, call tetep -70."""
        self.start(usdg_token1=False)
        self.chain.ticks[POOL] = 336490
        ids, tx = [1, 2, 3, 4, 5], tx_hash(2)
        rungs = [modify(1010, 10 + i, POOL, 337000 + i * 2500, 339500 + i * 2500, 5_000_000, t, tx) for i, t in enumerate(ids)]
        self.chain.logs += mint(ids, tx, 1010) + rungs
        self.assertEqual(self.w.poll()[0]["pos"]["range"], ["-70"])

    def test_usdg_is_token0(self):
        self.start(usdg_token1=False)
        self.chain.ticks[POOL] = -10000  # koin = token1: posisi di atas tick, tu = batas bawah harga koin
        tx = tx_hash(2)
        self.chain.logs += mint([7], tx, 1010) + [modify(1010, 5, POOL, -9960, -lower_tick(-65), 5_000_000, 7, tx)]
        self.assertEqual(self.w.poll()[0]["pos"]["range"], ["-65"])

    def test_open_is_copied_wherever_the_price_is(self):
        """Harga udah masuk ke ladder (atau jauh di atasnya): call tetep salinan posisi, nggak di-skip."""
        self.start()
        tx = tx_hash(2)
        self.chain.ticks[POOL] = 10000 - 3000  # harga turun masuk rung kedua
        self.chain.logs += mint([1, 2, 3, 4, 5], tx, 1010) + ladder([1, 2, 3, 4, 5], tx, 1010)
        evs = self.w.poll()
        self.assertEqual(actions(evs), [("open", "hydx")])
        self.assertEqual(evs[0]["pos"]["range"], ["-65"])

    def test_non_usdg_open_is_skipped(self):
        self.start()
        tx = tx_hash(3)
        self.chain.logs += mint([9], tx, 1011) + [modify(1011, 5, POOL2, lower_tick(-65), 10000, 5_000_000, 9, tx)]
        self.assertEqual([e["action"] for e in self.w.poll()], ["skip"])

    def test_non_usdg_close_is_silent(self):
        self.start()
        tx = tx_hash(2)
        self.chain.logs += burn([9], tx, 1010) + [modify(1010, 5, POOL2, 0, 100, -5_000_000, 9, tx)]
        self.assertEqual(self.w.poll(), [])

    def test_rpc_without_archive_still_opens(self):
        self.start(archive=False)
        tx = tx_hash(2)
        self.chain.logs += mint([7], tx, 1010) + [modify(1010, 5, POOL, lower_tick(-65), 10000, 5_000_000, 7, tx)]
        self.assertEqual(actions(self.w.poll()), [("open", "hydx")])

    def test_other_wallets_and_liquidity_changes_without_mint_or_burn_are_ignored(self):
        self.start()
        tx, tx2 = tx_hash(2), tx_hash(3)
        self.chain.logs += mint([5], tx, 1010, wallet=OTHER) + [modify(1010, 5, POOL, -100, 10000, 99, 5, tx)]
        self.chain.logs += [modify(1020, 1, POOL, lower_tick(-65), 10000, 1_000_000, 7, tx2),   # top up NFT lama
                            modify(1021, 1, POOL, lower_tick(-65), 10000, -1_000_000, 7, tx2)]  # tarik sebagian
        self.assertEqual(self.w.poll(), [])

    def test_nft_sent_to_another_wallet_is_not_a_call(self):
        self.start()
        self.chain.logs += [transfer(1010, 1, WALLET, OTHER, 7, tx_hash(2))]
        self.assertEqual(self.w.poll(), [])

    def test_missing_receipt_is_retried_not_lost(self):
        self.start()
        tx = tx_hash(2)
        self.chain.logs += mint([7], tx, 1010) + [modify(1010, 5, POOL, lower_tick(-65), 10000, 5_000_000, 7, tx)]
        self.chain.missing_receipts.add(tx)
        with self.assertRaises(RuntimeError):
            self.w.poll()
        self.assertEqual(self.w.status()["cursor"], 1000)  # nggak maju
        self.chain.missing_receipts.clear()
        self.assertEqual(actions(self.w.poll()), [("open", "hydx")])

    def test_long_downtime_only_announces_recent_activity(self):
        self.start(catchup_minutes=1)  # 1 menit = 600 block
        self.chain.head += 5000
        old, recent = tx_hash(2), tx_hash(3)
        self.chain.logs += mint([7], old, 1100) + [modify(1100, 5, POOL, lower_tick(-65), 10000, 5_000_000, 7, old)]
        self.chain.logs += mint([8], recent, 5900) + [modify(5900, 5, POOL, lower_tick(-65), 10000, 5_000_000, 8, recent)]
        evs = self.w.poll()
        self.assertEqual([e["action"] for e in evs], ["skip", "open"])
        self.assertEqual(evs[1]["token_ids"], [8])

    def test_state_survives_restart_and_wallet_change_keeps_cursor(self):
        self.start()
        again = W.Watcher(self.chain, self.chain, [WALLET], Store(self.store.path))
        self.assertEqual(again.status()["cursor"], 1000)
        other = W.Watcher(self.chain, self.chain, [OTHER], Store(self.store.path))
        self.assertEqual(other.status()["cursor"], 1000)  # wallet ditambah / diganti: lanjut dari block terakhir

    def test_calls_carry_their_wallet_and_wallets_can_be_added_while_running(self):
        self.start()
        tx, tx2 = tx_hash(2), tx_hash(3)
        self.chain.logs += mint([7], tx, 1010) + [modify(1010, 5, POOL, lower_tick(-65), 10000, 5_000_000, 7, tx)]
        self.chain.logs += mint([8], tx2, 1020, wallet=OTHER) + [modify(1020, 5, POOL, lower_tick(-65), 10000, 5_000_000, 8, tx2)]
        evs = self.w.poll()
        self.assertEqual([(e["action"], e["wallet"]) for e in evs], [("open", WALLET)])  # OTHER belum dipantau

        self.w.set_wallets([WALLET, OTHER])
        self.chain.head += 50
        tx3 = tx_hash(4)
        self.chain.logs += burn([8], tx3, 1070, wallet=OTHER) + [modify(1070, 5, POOL, lower_tick(-65), 10000, -5_000_000, 8, tx3)]
        self.assertEqual([(e["action"], e["wallet"]) for e in self.w.poll()], [("close", OTHER)])

    def test_one_log_call_per_poll_whatever_the_wallet_count(self):
        self.start()
        self.w.set_wallets([WALLET, OTHER, "0x" + "f3" * 20])
        tx = tx_hash(2)
        self.chain.logs += mint([7], tx, 1010) + [modify(1010, 5, POOL, lower_tick(-65), 10000, 5_000_000, 7, tx)]
        self.chain.logs += [transfer(1011, 1, OTHER, "0x" + "a9" * 20, 9, tx_hash(3))]  # dikirim ke luar: bukan call
        self.chain.calls.clear()
        with mock.patch.object(self.chain, "get_logs", wraps=self.chain.get_logs) as logs:
            evs = self.w.poll()
        self.assertEqual(logs.call_count, 1)
        self.assertEqual([e["action"] for e in evs], ["open"])
        self.assertEqual(self.chain.calls.count("eth_blockNumber"), 1)

    def test_snapshot_finds_positions_still_open(self):
        self.make()
        self.w.set_wallets([WALLET, OTHER])
        self.chain.head = 5000
        t1, t2, t3, t4 = tx_hash(1), tx_hash(2), tx_hash(3), tx_hash(4)
        self.chain.logs += mint([1, 2], t1, 4000) + ladder([1, 2], t1, 4000)             # masih kebuka
        self.chain.logs += mint([3], t2, 4100) + ladder([3], t2, 4100)                   # ditutup lagi
        self.chain.logs += burn([3], t3, 4200) + ladder([3], t3, 4200, delta=-5_000_000)
        self.chain.logs += mint([4], t4, 4300, wallet=OTHER) + ladder([4], t4, 4300)     # wallet pantauan, kebuka
        self.chain.logs += mint([5], tx_hash(5), 4400) + ladder([5], tx_hash(5), 4400)   # dikirim ke luar
        self.chain.owners.update({4: OTHER, 5: "0x" + "a9" * 20})
        evs = self.w.snapshot(days=1)
        self.assertEqual([(e["token_ids"], e["wallet"]) for e in evs], [([1, 2], WALLET), ([4], OTHER)])
        self.assertEqual(evs[0]["pos"]["range"], ["-35"])  # 2 rung x 2100 tick

    def test_no_wallets_reads_only_the_block_number(self):
        self.make()
        self.w.set_wallets([])
        self.w.poll()
        self.chain.head += 50
        with mock.patch.object(self.chain, "get_logs", side_effect=AssertionError("nggak boleh scan")):
            self.assertEqual(self.w.poll(), [])

class BuildRpcsTest(unittest.TestCase):
    def rpcs(self, **env):
        keys = ("RPC_URL", "BACKUP_RPC_URLS", "CALL_RPC_URL")
        clean = {k: v for k, v in B.os.environ.items() if k not in keys}
        with mock.patch.dict(B.os.environ, dict(clean, **env), clear=True), \
                mock.patch.object(W.RH, "load_env", side_effect=AssertionError("jangan baca .env tool lain")):
            return B.build_rpcs(W)

    def test_default_is_public_rpc_only(self):
        logs, calls = self.rpcs()
        self.assertIsInstance(logs, W.R.RPC)
        self.assertIs(logs, calls)

    def test_backups_in_order_after_the_public_rpc(self):
        solid = "https://rpc.solidrpc.io/ak_123/evm/4663"
        logs, calls = self.rpcs(BACKUP_RPC_URLS=solid + ", abcdefghijklmnopqrstuvwxyz123456")
        self.assertIsInstance(logs, W.R.RPCPool)
        self.assertEqual(logs.prefer, 0)  # RPC publik dulu
        self.assertEqual([m.url for m in logs.members],
                         [W.PUBLIC_RPC, solid, W.RH.ALCHEMY_RPC.format(key="abcdefghijklmnopqrstuvwxyz123456")])

    def test_dead_key_answer_moves_the_pool_on(self):
        """SolidRPC 'Invalid API key or inactive subscription' -> RPC itu diistirahatin, bukan error ke bot."""
        for msg in ("Invalid API key or inactive subscription", "Must be authenticated!"):
            with self.assertRaises(W.R.QuotaError):
                W.R._result({"error": {"code": -32000, "message": msg}}, "eth_blockNumber")
        dead = W.R.RPC("https://dead.example", workers=1)
        dead._post = lambda payload: {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000,
                                                                          "message": "Invalid API key or inactive subscription"}}
        ok = W.R.RPC("https://ok.example", workers=1)
        ok._post = lambda payload: {"jsonrpc": "2.0", "id": 1, "result": "0x10"}
        pool = W.R.RPCPool([dead, ok], prefer=0)
        self.assertEqual(pool.call("eth_blockNumber", []), "0x10")

    def test_daily_quota_answer_is_a_quota_error(self):
        """SolidRPC: 'Daily response quota exceeded' -> pool pindah ke cadangan, bukan error ke bot."""
        with self.assertRaises(W.R.QuotaError):
            W.R._result({"error": {"code": -32005, "message": "Daily response quota exceeded"}}, "eth_blockNumber")


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True, None

    def to(self, chat_id):
        return [t for c, t in self.sent if c == chat_id]


OWNER, FAN = 111, 222


class AutoCallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tg = FakeTelegram()
        self.store = Store(Path(self.tmp.name) / "state.json")
        self.store.subscribe(FAN)
        patcher = mock.patch.object(B.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bot = B.Bot(self.tg, self.store, [OWNER], [])
        self.pos = {"coin": "hydx", "ca": W.checksum(COIN), "fee": "2.5", "range": ["-65"]}

    def test_open_and_close_are_blasted(self):
        self.bot.auto_call({"action": "open", "pos": dict(self.pos)})
        self.assertIn("OPEN LP — HYDX", self.tg.to(FAN)[-1])
        self.assertIn(W.checksum(COIN), self.tg.to(FAN)[-1])
        self.assertIn("terkirim ke 1", self.tg.to(OWNER)[-1])
        self.bot.auto_call({"action": "close", "pos": {"coin": "hydx", "ca": self.pos["ca"]}})
        self.assertIn("CLOSE LP — HYDX", self.tg.to(FAN)[-1])
        self.assertEqual(self.store.get("hydx")["status"], "closed")

    def test_open_again_is_a_plain_open(self):
        self.bot.auto_call({"action": "open", "pos": dict(self.pos)})
        self.bot.auto_call({"action": "close", "pos": {"coin": "hydx", "ca": self.pos["ca"]}})
        self.bot.auto_call({"action": "open", "pos": dict(self.pos, range=["-75"])})
        self.assertIn("OPEN LP — HYDX", self.tg.to(FAN)[-1])
        self.assertNotIn("REENTRY", self.tg.to(FAN)[-1])
        self.assertEqual(self.store.get("hydx")["range"], ["-75"])
        self.assertEqual(self.store.get("hydx")["status"], "open")

    def test_close_of_unknown_coin_still_blasted(self):
        self.bot.auto_call({"action": "close", "pos": {"coin": "worm", "ca": self.pos["ca"]}})
        self.assertIn("CLOSE LP — WORM", self.tg.to(FAN)[-1])

    def test_recap_drops_positions_closed_while_the_bot_missed_it(self):
        chain = FakeChain()
        self.bot.watcher = W.Watcher(chain, chain, [WALLET], self.store)
        self.bot.auto_call({"action": "open", "wallet": WALLET, "token_ids": [11, 12], "pos": dict(self.pos)})
        self.bot.auto_call({"action": "open", "wallet": WALLET, "token_ids": [21],
                            "pos": dict(self.pos, coin="worm", range=["-60"])})
        self.bot.auto_call({"action": "open", "wallet": WALLET, "token_ids": [31],
                            "pos": dict(self.pos, coin="raeto")})
        chain.burned.add(11)            # HYDX ditutup, CLOSE-nya kelewat
        chain.owners[31] = OTHER        # RAETO dipindah ke wallet lain
        self.assertTrue(self.bot.send_recap())
        text = self.tg.to(FAN)[-1]
        self.assertIn("(1)", text)
        self.assertIn("WORM", text)
        self.assertNotIn("HYDX", text)
        self.assertNotIn("RAETO", text)
        self.assertEqual(self.store.get("hydx")["status"], "closed")

    def test_recap_keeps_positions_when_rpc_is_down_and_drops_stale_manual_calls(self):
        chain = FakeChain()
        self.bot.watcher = W.Watcher(chain, chain, [WALLET], self.store)
        self.bot.auto_call({"action": "open", "wallet": WALLET, "token_ids": [11], "pos": dict(self.pos)})
        self.store.open_position({"coin": "dark", "ca": self.pos["ca"], "fee": "3", "range": ["-65"]})  # call manual
        chain.down = True
        self.bot.prune_closed(now=B.time.time() + 25 * 3600)
        self.assertEqual(self.store.get("hydx")["status"], "open")   # RPC error: dibiarin
        self.assertEqual(self.store.get("dark")["status"], "closed")  # manual > 24 jam: basi

    def test_bersih_clears_old_entries_without_nft_data(self):
        self.store.open_position({"coin": "wofl", "ca": self.pos["ca"], "fee": "4.7915", "range": ["-60"]})
        self.bot.auto_call({"action": "open", "wallet": WALLET, "token_ids": [11], "pos": dict(self.pos)})
        self.assertEqual(self.bot.clear_unverifiable(), 1)
        self.assertEqual(self.store.get("wofl")["status"], "closed")
        self.assertEqual(self.store.get("hydx")["status"], "open")

    def test_sync_fills_the_recap_from_chain_without_blasting(self):
        chain = FakeChain()
        self.bot.owner_wallets = [WALLET]
        self.store.add_wallet(OTHER, "degen")
        self.bot.watcher = W.Watcher(chain, chain, [WALLET, OTHER], self.store)
        self.store.open_position({"coin": "wofl", "ca": self.pos["ca"], "fee": "4.7915", "range": ["-60"]})  # basi
        chain.head = 5000
        chain.logs += mint([1], tx_hash(1), 4000) + ladder([1], tx_hash(1), 4000)
        chain.logs += mint([4], tx_hash(4), 4300, wallet=OTHER) + ladder([4], tx_hash(4), 4300)
        chain.owners[4] = OTHER
        sent = len(self.tg.sent)
        self.assertEqual(self.bot.sync_positions(), 2)
        self.assertEqual(len(self.tg.sent), sent)  # nggak nge-blast
        self.assertEqual(self.store.get("wofl")["status"], "closed")
        self.assertEqual(self.store.get("hydx")["token_ids"], [1])
        self.assertEqual(self.store.get("hydx@degen")["by"], "degen")
        self.assertTrue(self.bot.send_recap())
        text = self.tg.to(FAN)[-1]
        self.assertIn("(2)", text)
        self.assertIn("HYDX · 👀 degen", text)

    def test_hourly_recap(self):
        self.bot.recap_seconds = 3600
        self.assertFalse(self.bot.maybe_recap(now=1000))  # baru nyala: cuma mulai ngitung
        self.bot.auto_call({"action": "open", "pos": dict(self.pos)})
        self.bot.auto_call({"action": "open", "pos": dict(self.pos, coin="worm", range=["-60"])})
        self.bot.auto_call({"action": "close", "pos": {"coin": "worm", "ca": self.pos["ca"]}})
        self.assertFalse(self.bot.maybe_recap(now=1000 + 1800))  # belum sejam
        self.assertTrue(self.bot.maybe_recap(now=1000 + 3600))
        text = self.tg.to(FAN)[-1]
        self.assertIn("Rekap posisi LP yang masih kebuka (1)", text)
        self.assertIn("HYDX", text)
        self.assertNotIn("WORM", text)  # udah close
        self.assertFalse(self.bot.maybe_recap(now=1000 + 3700))  # nggak dobel

    def test_recap_skipped_when_nothing_open(self):
        self.bot.recap_seconds = 3600
        self.bot.maybe_recap(now=0)
        sent = len(self.tg.sent)
        self.assertFalse(self.bot.maybe_recap(now=3600))
        self.assertEqual(len(self.tg.sent), sent)

    def test_wallet_command_add_list_remove(self):
        self.assertIn("Belum ada", self.bot.wallet_command(""))
        self.assertIn("Format", self.bot.wallet_command("add 0x123 aan"))
        self.assertIn("Mantau wallet <b>Aan</b>", self.bot.wallet_command(f"add {OTHER.upper().replace('0X', '0x')} Aan"))
        self.assertEqual(self.store.wallets(), {OTHER: "Aan"})
        self.assertIn("udah dipakai", self.bot.wallet_command(f"add {WALLET} aan"))
        self.assertIn("Aan", self.bot.wallet_command(""))
        self.assertIn("Berhenti mantau <b>Aan</b>", self.bot.wallet_command("hapus aan"))
        self.assertEqual(self.store.wallets(), {})
        self.assertIn("nggak ada", self.bot.wallet_command("hapus aan"))

    def test_owner_and_added_wallet_calls(self):
        self.bot.owner_wallets = [WALLET]
        self.store.add_wallet(OTHER, "Aan")
        self.bot.auto_call({"action": "open", "wallet": WALLET, "pos": dict(self.pos)})
        self.assertTrue(self.tg.to(FAN)[-1].startswith("🟢 <b>OPEN LP — HYDX</b>"))  # punya lu: kayak biasa
        self.bot.auto_call({"action": "open", "wallet": OTHER, "pos": dict(self.pos, range=["-70"])})
        text = self.tg.to(FAN)[-1]
        self.assertTrue(text.startswith("👀 <b>PANTAUAN WALLET: AAN</b>"))
        self.assertIn("🔵 <b>OPEN LP — HYDX</b>", text)
        self.assertNotIn("🟢", text)
        self.assertEqual(self.store.get("hydx")["range"], ["-65"])  # nggak ketimpa
        self.assertEqual(self.store.get("hydx@aan")["range"], ["-70"])

        self.bot.auto_call({"action": "close", "wallet": OTHER, "pos": {"coin": "hydx", "ca": self.pos["ca"]}})
        text = self.tg.to(FAN)[-1]
        self.assertIn("PANTAUAN WALLET: AAN", text)
        self.assertIn("🟠 <b>CLOSE LP — HYDX</b>", text)
        self.assertNotIn("🔴", text)
        self.assertEqual(self.store.get("hydx")["status"], "open")
        self.assertEqual(self.store.get("hydx@aan")["status"], "closed")

        self.bot.auto_call({"action": "skip", "wallet": OTHER, "text": "Posisi #9 di pool tanpa USDG"})
        self.assertIn("[Aan] Posisi #9", self.tg.to(OWNER)[-1])

    def test_recap_shows_whose_position(self):
        self.bot.owner_wallets = [WALLET]
        self.store.add_wallet(OTHER, "Aan")
        self.bot.auto_call({"action": "open", "wallet": WALLET, "pos": dict(self.pos)})
        self.bot.auto_call({"action": "open", "wallet": OTHER, "pos": dict(self.pos)})
        self.assertTrue(self.bot.send_recap())
        text = self.tg.to(FAN)[-1]
        self.assertIn("(2)", text)
        self.assertIn("HYDX · 👀 Aan", text)

    def test_second_bot_instance_warns_once(self):
        self.tg.call = mock.Mock(return_value={"ok": False, "error_code": 409, "description": "Conflict"})
        with mock.patch("builtins.print") as out:
            self.bot.poll_once()
            self.bot.poll_once()
        warnings = [c for c in out.call_args_list if "bot lain" in str(c)]
        self.assertEqual(len(warnings), 1)

    def test_dry_run_only_tells_owner(self):
        self.bot.dry_run = True
        self.bot.auto_call({"action": "open", "pos": dict(self.pos)})
        self.assertEqual(self.tg.to(FAN), [])
        self.assertIn("DRY RUN", self.tg.to(OWNER)[-1])

    def test_skip_goes_to_owner_only(self):
        self.bot.auto_call({"action": "skip", "text": "Posisi #9 bukan single sided USDG"})
        self.assertEqual(self.tg.to(FAN), [])
        self.assertIn("#9", self.tg.to(OWNER)[-1])


if __name__ == "__main__":
    unittest.main()
