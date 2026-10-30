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

    def test_single_sided_has_small_slack(self):
        self.assertTrue(W.single_sided_usdg(0, 10000, 10000, True))
        self.assertTrue(W.single_sided_usdg(0, 10050, 10000, True))   # harga gerak dikit
        self.assertFalse(W.single_sided_usdg(0, 12000, 10000, True))  # koinnya ikut masuk
        self.assertTrue(W.single_sided_usdg(10000, 12000, 9950, False))
        self.assertFalse(W.single_sided_usdg(8000, 12000, 10000, False))


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

    def test_two_sided_and_non_usdg_opens_are_skipped(self):
        self.start()
        tx, tx2 = tx_hash(2), tx_hash(3)
        self.chain.logs += mint([8], tx, 1010) + [modify(1010, 5, POOL, lower_tick(-65), 12000, 5_000_000, 8, tx)]
        self.chain.logs += mint([9], tx2, 1011) + [modify(1011, 5, POOL2, lower_tick(-65), 10000, 5_000_000, 9, tx2)]
        self.assertEqual([e["action"] for e in self.w.poll()], ["skip", "skip"])

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

    def test_state_survives_restart_and_wallet_change_resets(self):
        self.start()
        again = W.Watcher(self.chain, self.chain, [WALLET], Store(self.store.path))
        self.assertEqual(again.status()["cursor"], 1000)
        other = W.Watcher(self.chain, self.chain, [OTHER], Store(self.store.path))
        self.assertIsNone(other.status()["cursor"])


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
