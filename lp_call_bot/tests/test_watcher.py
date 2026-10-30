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
TX = "0x" + "ab" * 32
FEE = 25000  # 2.5%


def pad(a):
    return "0x" + "0" * 24 + a.lower().removeprefix("0x")


def transfer(block, li, frm, to, tid, tx=TX):
    return {"address": RH.POSITION_MANAGER, "blockNumber": hex(block), "logIndex": hex(li),
            "transactionHash": tx, "data": "0x",
            "topics": [C.TRANSFER, pad(frm), pad(to), "0x" + format(tid, "064x")]}


def modify(block, li, pool, tl, tu, delta, salt, tx=TX):
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
    """Cukup mirip RPC asli buat watcher: logs terfilter, poolKeys, slot0, symbol."""

    def __init__(self, usdg_token1=True, archive=True):
        self.archive = archive  # False: RPC publik, cuma tau harga "latest"
        self.head = 1000
        self.logs = []
        self.ticks = {POOL: 10000, POOL2: 10000}
        usdg = RH.USDG
        c0, c1 = (COIN, usdg) if usdg_token1 else (usdg, COIN)
        self.keys = {POOL[2:52]: (c0, c1, FEE)}
        self.keys[POOL2[2:52]] = (RH.WETH, COIN, 3000)  # pool tanpa USDG

    def call(self, method, params):
        if method == "eth_blockNumber":
            return hex(self.head + W.CONFIRMATIONS)
        if method == "eth_getTransactionReceipt":
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
            tick = self.ticks["0x" + arg[:64]]
            return "0x" + C.word(1 << 96) + C.word(tick) + C.word(0) + C.word(FEE)
        if tx["to"] == RH.STATE_VIEW and sel == C.SEL_POSITION_INFO:
            w = C.words("0x" + arg)
            pool, tl, tu, salt = "0x" + w[0], C.signed(w[2]), C.signed(w[3]), int(w[4], 16)
            liq = 0
            for lg in self.logs:
                if lg["address"] == RH.POOL_MANAGER and lg["topics"][1] == pool and int(lg["blockNumber"], 16) <= self.head:
                    d = C.decode_modify(lg)
                    if (d["tl"], d["tu"], d["salt"]) == (tl, tu, salt):
                        liq += d["delta"]
            return "0x" + C.word(liq) + C.word(0) + C.word(0)
        if sel == C.SEL_SYMBOL:
            return abi_string("HYDX")
        raise AssertionError(tx)

    def get_logs(self, flt, start, end, chunk, progress=None):
        got = [lg for lg in self.logs if start <= int(lg["blockNumber"], 16) <= end and matches(lg, flt)]
        return sorted(got, key=lambda lg: (int(lg["blockNumber"], 16), int(lg["logIndex"], 16))), chunk


def lower_tick(pct, tick=10000):
    """Tick buat batas bawah `pct`% di bawah harga (-65 -> 65% lebih murah), spacing 60."""
    return math.floor((tick + math.log(1 + pct / 100) / math.log(1.0001)) / 60) * 60


class PureTest(unittest.TestCase):
    def test_checksum_vector(self):
        self.assertEqual(W.checksum("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"),
                         "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")

    def test_fee(self):
        self.assertEqual([W.fmt_fee(f) for f in (25000, 48800, 3000, 50000, 10000)],
                         ["2.5", "4.88", "0.3", "5", "1"])

    def test_range_both_orientations(self):
        tl, tu = lower_tick(-65), 10000
        lo, up = W.range_pcts(tl, tu, 10000, True)
        self.assertEqual((W.fmt_pct(lo), W.fmt_pct(up)), ("-65", "0"))
        # koin = token1: harga dibalik, posisi di atas tick
        lo, up = W.range_pcts(-10000, -lower_tick(-65), -10000, False)
        self.assertEqual(W.fmt_pct(lo), "-65")
        self.assertEqual(W.range_label(-60.4, -1.2), ["-60"])
        self.assertEqual(W.range_label(-65.2, -60.1), ["-60", "-65"])


class WatcherTest(unittest.TestCase):
    def make(self, usdg_token1=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chain = FakeChain(usdg_token1)
        self.store = Store(Path(self.tmp.name) / "state.json")
        self.w = W.Watcher(self.chain, self.chain, [WALLET], self.store, backfill_hours=0.001)
        self.w.poll()  # poll pertama: belajar, nggak ngirim apa-apa
        self.chain.head += 50

    def open_pos(self, tid=7, pool=POOL, tl=None, tu=10000, block=1010):
        tl = lower_tick(-65) if tl is None else tl
        self.chain.logs += [transfer(block, 1, RH.ZERO, WALLET, tid), modify(block, 2, pool, tl, tu, 5_000_000, tid)]

    def test_open_then_close(self):
        self.make()
        self.open_pos()
        evs = self.w.poll()
        self.assertEqual([e["action"] for e in evs], ["open"])
        self.assertEqual(evs[0]["pos"], {"coin": "hydx", "ca": W.checksum(COIN), "fee": "2.5", "range": ["-65"]})
        self.assertEqual(evs[0]["token_ids"], ["7"])

        self.chain.head += 50
        self.chain.logs.append(modify(1070, 3, POOL, lower_tick(-65), 10000, -5_000_000, 7))
        evs = self.w.poll()
        self.assertEqual([(e["action"], e["pos"]["coin"]) for e in evs], [("close", "hydx")])
        self.assertEqual(self.w.poll(), [])  # nggak dobel

    def test_burn_after_close_is_silent(self):
        self.make()
        self.open_pos()
        self.w.poll()
        self.chain.head += 50
        self.chain.logs += [modify(1070, 3, POOL, lower_tick(-65), 10000, -5_000_000, 7),
                            transfer(1070, 4, WALLET, RH.ZERO, 7)]
        self.assertEqual([e["action"] for e in self.w.poll()], ["close"])

    def test_usdg_is_token0(self):
        self.make(usdg_token1=False)
        tl = -9960  # koin = token1: posisi di atas tick (-10000); tu = batas bawah harga koin
        self.chain.ticks[POOL] = -10000
        self.open_pos(tl=tl, tu=-lower_tick(-65))
        ev = self.w.poll()[0]
        self.assertEqual(ev["pos"]["range"], ["-65"])

    def test_two_sided_and_non_usdg_are_skipped(self):
        self.make()
        self.open_pos(tid=8, tl=lower_tick(-65), tu=12000)  # tick di tengah range: ada koinnya
        self.open_pos(tid=9, pool=POOL2, block=1011)
        evs = self.w.poll()
        self.assertEqual([e["action"] for e in evs], ["skip", "skip"])
        self.chain.head += 50
        self.chain.logs.append(modify(1070, 3, POOL, lower_tick(-65), 12000, -5_000_000, 8))
        self.assertEqual(self.w.poll(), [])  # close posisi yang di-skip juga nggak di-blast

    def test_other_wallet_and_partial_changes_ignored(self):
        self.make()
        self.chain.logs += [transfer(1010, 1, RH.ZERO, OTHER, 5), modify(1010, 2, POOL, -100, 10000, 99, 5)]
        self.open_pos()
        self.chain.logs.append(modify(1020, 1, POOL, lower_tick(-65), 10000, 1_000_000, 7))  # nambah liquidity
        self.assertEqual([e["action"] for e in self.w.poll()], ["open"])
        self.chain.head += 50
        self.chain.logs.append(modify(1070, 1, POOL, lower_tick(-65), 10000, -1_000_000, 7))  # sebagian
        self.assertEqual(self.w.poll(), [])

    def test_position_opened_before_bot_started_is_closed_later(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chain = FakeChain()
        self.chain.head = 100_000
        self.open_pos(block=99_990)  # kebuka sebelum bot nyala, masuk jendela backfill
        store = Store(Path(self.tmp.name) / "state.json")
        w = W.Watcher(self.chain, self.chain, [WALLET], store, backfill_hours=1)
        self.assertEqual(w.poll(), [])  # backfill diam-diam
        self.assertEqual(w.status()["open"], 1)
        self.chain.head += 50
        self.chain.logs.append(modify(100_030, 1, POOL, lower_tick(-65), 10000, -5_000_000, 7))
        evs = w.poll()
        self.assertEqual([(e["action"], e["pos"]["coin"]) for e in evs], [("close", "hydx")])

    def test_backfill_ignores_closed_burned_and_sent_positions(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chain = FakeChain()
        self.chain.head = 100_000
        t1, t2, t3, t4 = ("0x" + c * 32 for c in ("a1", "a2", "a3", "a4"))
        tl = lower_tick(-65)
        self.chain.logs += [  # 11 masih kebuka; 12 ditarik tanpa burn; 13 diburn; 14 dikirim ke wallet lain
            transfer(99_900, 1, RH.ZERO, WALLET, 11, t1), modify(99_900, 2, POOL, tl, 10000, 5_000_000, 11, t1),
            transfer(99_910, 1, RH.ZERO, WALLET, 12, t2), modify(99_910, 2, POOL, tl, 10000, 5_000_000, 12, t2),
            modify(99_950, 1, POOL, tl, 10000, -5_000_000, 12, t4),
            transfer(99_920, 1, RH.ZERO, WALLET, 13, t3), modify(99_920, 2, POOL, tl, 10000, 5_000_000, 13, t3),
            transfer(99_960, 1, WALLET, RH.ZERO, 13, t4),
            transfer(99_930, 1, RH.ZERO, WALLET, 14, t4), modify(99_930, 2, POOL, tl, 10000, 5_000_000, 14, t4),
            transfer(99_970, 2, WALLET, OTHER, 14, t4)]
        w = W.Watcher(self.chain, self.chain, [WALLET], Store(Path(self.tmp.name) / "s.json"), backfill_hours=1)
        self.assertEqual(w.poll(), [])
        self.assertEqual(sorted(w.state["tokens"]), ["11"])
        self.assertEqual(w.state["tokens"]["11"]["block"], 99_900)

    def test_state_survives_restart_and_wallet_change_resets(self):
        self.make()
        self.open_pos()
        self.w.poll()
        again = W.Watcher(self.chain, self.chain, [WALLET], Store(self.store.path), backfill_hours=1)
        self.assertEqual(again.status()["open"], 1)
        other = W.Watcher(self.chain, self.chain, [OTHER], Store(self.store.path), backfill_hours=1)
        self.assertEqual((other.status()["open"], other.status()["cursor"]), (0, None))

    # ---- ladder: satu posisi = 5 NFT dalam satu transaksi ----
    def ladder(self, ids, block, tx, delta=5_000_000, pool=POOL, top=10000, step=2100):
        """Rung dari harga turun: [top-step, top], [top-2*step, top-step], ..."""
        logs = []
        for i, tid in enumerate(ids):
            tu, tl = top - i * step, top - (i + 1) * step
            logs.append(modify(block, 10 + i, pool, tl, tu, delta, tid, tx))
        return logs

    def test_ladder_is_one_call_with_overall_range(self):
        self.make()
        tx = "0x" + "a1" * 32
        ids = [101, 102, 103, 104, 105]
        self.chain.logs += [transfer(1010, i, RH.ZERO, WALLET, t, tx) for i, t in enumerate(ids)]
        self.chain.logs += self.ladder(ids, 1010, tx, step=2100)  # 5 x 2100 tick = ~ -65%
        evs = self.w.poll()
        self.assertEqual([e["action"] for e in evs], ["open"])
        self.assertEqual(evs[0]["pos"]["range"], ["-65"])  # bawah rung terakhir, atas nempel harga
        self.assertEqual(len(evs[0]["token_ids"]), 5)

        self.chain.head += 50
        close_tx = "0x" + "b2" * 32
        self.chain.logs += [modify(1070, 10 + i, POOL, 10000 - (i + 1) * 2100, 10000 - i * 2100, -5_000_000, t, close_tx)
                            for i, t in enumerate(ids)]
        evs = self.w.poll()
        self.assertEqual([(e["action"], e["pos"]["coin"]) for e in evs], [("close", "hydx")])
        self.assertEqual(evs[0]["pos"]["range"], ["-65"])  # close bawa fee + range, buat reentry berikutnya

    def test_burn_before_liquidity_removal_still_closes(self):
        """V4 burn: NFT diburn dulu, baru liquidity-nya ditarik (urutan log kebalik)."""
        self.make()
        self.open_pos()
        self.w.poll()
        self.chain.head += 50
        tx = "0x" + "c3" * 32
        self.chain.logs += [transfer(1070, 1, WALLET, RH.ZERO, 7, tx),
                            modify(1070, 2, POOL, lower_tick(-65), 10000, -5_000_000, 7, tx)]
        evs = self.w.poll()
        self.assertEqual([(e["action"], e["pos"]["coin"]) for e in evs], [("close", "hydx")])
        self.assertEqual(self.w.status()["open"], 0)
        self.assertEqual(self.w.state["tokens"], {})  # udah selesai: nggak dilacak lagi

    def test_close_and_open_in_one_tx_is_one_reentry(self):
        """Persis kejadian di chain: 5 NFT lama diburn + 5 NFT baru di tick yang sama, satu transaksi."""
        self.make()
        old, new = [201, 202, 203], [301, 302, 303]
        tx = "0x" + "a1" * 32
        self.chain.logs += [transfer(1010, i, RH.ZERO, WALLET, t, tx) for i, t in enumerate(old)]
        self.chain.logs += self.ladder(old, 1010, tx)
        self.assertEqual([e["action"] for e in self.w.poll()], ["open"])

        self.chain.head += 50
        rtx = "0x" + "d4" * 32
        self.chain.logs += [transfer(1070, i, WALLET, RH.ZERO, t, rtx) for i, t in enumerate(old)]
        self.chain.logs += [transfer(1070, 3 + i, RH.ZERO, WALLET, t, rtx) for i, t in enumerate(new)]
        self.chain.logs += [modify(1070, 10 + i, POOL, 10000 - (i + 1) * 2100, 10000 - i * 2100, -5_000_000, t, rtx)
                            for i, t in enumerate(old)]
        self.chain.logs += self.ladder(new, 1070, rtx)
        evs = self.w.poll()
        self.assertEqual([e["action"] for e in evs], ["reentry"])
        self.assertEqual(evs[0]["pos"]["coin"], "hydx")
        self.assertEqual(sorted(self.w.state["tokens"]), ["301", "302", "303"])

    def test_reentry_of_position_from_before_bot_started(self):
        """Posisi lama dikenal cuma lewat backfill diam-diam: range lamanya diambil dari harga pas dibuka."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chain = FakeChain()
        self.chain.head = 100_000
        tx = "0x" + "a1" * 32
        old, new = [201, 202], [301, 302]
        self.chain.logs += [transfer(99_990, i, RH.ZERO, WALLET, t, tx) for i, t in enumerate(old)]
        self.chain.logs += self.ladder(old, 99_990, tx)
        w = W.Watcher(self.chain, self.chain, [WALLET], Store(Path(self.tmp.name) / "s.json"), backfill_hours=1)
        self.assertEqual(w.poll(), [])
        self.chain.head += 50
        rtx = "0x" + "d4" * 32
        self.chain.logs += [transfer(100_030, i, WALLET, RH.ZERO, t, rtx) for i, t in enumerate(old)]
        self.chain.logs += [transfer(100_030, 3 + i, RH.ZERO, WALLET, t, rtx) for i, t in enumerate(new)]
        self.chain.logs += [modify(100_030, 10 + i, POOL, 10000 - (i + 1) * 2100, 10000 - i * 2100, -5_000_000, t, rtx)
                            for i, t in enumerate(old)]
        self.chain.logs += self.ladder(new, 100_030, rtx)
        self.assertEqual([e["action"] for e in w.poll()], ["reentry"])

    def test_reentry_on_rpc_without_archive_uses_same_ticks(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chain = FakeChain(archive=False)
        self.chain.head = 100_000
        tx = "0x" + "a1" * 32
        old, new = [201, 202], [301, 302]
        self.chain.logs += [transfer(99_990, i, RH.ZERO, WALLET, t, tx) for i, t in enumerate(old)]
        self.chain.logs += self.ladder(old, 99_990, tx)
        w = W.Watcher(self.chain, self.chain, [WALLET], Store(Path(self.tmp.name) / "s.json"), backfill_hours=1)
        w.poll()
        self.chain.head += 50
        rtx = "0x" + "d4" * 32
        self.chain.logs += [transfer(100_030, i, WALLET, RH.ZERO, t, rtx) for i, t in enumerate(old)]
        self.chain.logs += [transfer(100_030, 3 + i, RH.ZERO, WALLET, t, rtx) for i, t in enumerate(new)]
        self.chain.logs += [modify(100_030, 10 + i, POOL, 10000 - (i + 1) * 2100, 10000 - i * 2100, -5_000_000, t, rtx)
                            for i, t in enumerate(old)]
        self.chain.logs += self.ladder(new, 100_030, rtx)
        self.assertEqual([e["action"] for e in w.poll()], ["reentry"])

    def test_reentry_with_different_range_is_close_then_open(self):
        self.make()
        tx = "0x" + "a1" * 32
        self.chain.logs += [transfer(1010, 0, RH.ZERO, WALLET, 201, tx)] + self.ladder([201], 1010, tx, step=2100)
        self.w.poll()
        self.chain.head += 50
        rtx = "0x" + "d4" * 32
        self.chain.logs += [transfer(1070, 0, WALLET, RH.ZERO, 201, rtx), transfer(1070, 1, RH.ZERO, WALLET, 301, rtx),
                            modify(1070, 10, POOL, 10000 - 2100, 10000, -5_000_000, 201, rtx)]
        self.chain.logs += self.ladder([301], 1070, rtx, step=4000)  # range lebih lebar
        self.assertEqual([e["action"] for e in self.w.poll()], ["close", "open"])


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

    def test_open_close_reentry_blasted(self):
        self.bot.auto_call({"action": "open", "pos": dict(self.pos)})
        self.assertIn("OPEN LP — HYDX", self.tg.to(FAN)[-1])
        self.assertIn(W.checksum(COIN), self.tg.to(FAN)[-1])
        self.assertIn("terkirim ke 1", self.tg.to(OWNER)[-1])

        self.bot.auto_call({"action": "close", "pos": {"coin": "hydx", "ca": self.pos["ca"]}})
        self.assertIn("CLOSE LP — HYDX", self.tg.to(FAN)[-1])
        self.assertEqual(self.store.get("hydx")["status"], "closed")

        again = dict(self.pos, range=["-64"])  # tick spacing geser 1%: tetep reentry
        self.bot.auto_call({"action": "open", "pos": again})
        self.assertIn("REENTRY — HYDX", self.tg.to(FAN)[-1])
        self.assertIn("-65%", self.tg.to(FAN)[-1])

    def test_different_range_is_a_new_open(self):
        self.bot.auto_call({"action": "open", "pos": dict(self.pos)})
        self.bot.auto_call({"action": "close", "pos": {"coin": "hydx", "ca": self.pos["ca"]}})
        self.bot.auto_call({"action": "open", "pos": dict(self.pos, range=["-75"])})
        self.assertIn("OPEN LP — HYDX", self.tg.to(FAN)[-1])
        self.assertEqual(self.store.get("hydx")["range"], ["-75"])

    def test_close_of_unknown_coin_still_blasted(self):
        self.bot.auto_call({"action": "close", "pos": {"coin": "worm", "ca": self.pos["ca"]}})
        self.assertIn("CLOSE LP — WORM", self.tg.to(FAN)[-1])

    def test_reentry_event_and_close_remembers_range(self):
        self.bot.auto_call({"action": "close", "pos": dict(self.pos)})  # close pertama bawa fee + range
        self.assertEqual(self.store.get("hydx")["status"], "closed")
        self.assertEqual(self.store.get("hydx")["range"], ["-65"])
        self.bot.auto_call({"action": "open", "pos": dict(self.pos, range=["-64"])})
        self.assertIn("REENTRY — HYDX", self.tg.to(FAN)[-1])
        self.bot.auto_call({"action": "reentry", "pos": dict(self.pos)})
        self.assertEqual(self.tg.to(FAN)[-1].count("REENTRY"), 1)

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
