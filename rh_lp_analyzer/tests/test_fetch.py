"""The chain loader against a fake RPC: python3 -m unittest discover -s rh_lp_analyzer/tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chain as C  # noqa: E402
import config  # noqa: E402
import fetch as F  # noqa: E402

W = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
POOL = "0x" + "11" * 32
T0 = config.CHAIN_LAUNCH_TS  # block n is at T0 + n seconds
HEAD = 5030


def w(a):
    return "0x" + C.addr_word(a)


def transfer(block, tid, frm, to, tx):
    return {"address": config.POSITION_MANAGER, "blockNumber": hex(block), "logIndex": hex(1),
            "transactionHash": tx, "data": "0x", "topics": [C.TRANSFER, w(frm), w(to), "0x" + C.word(tid)]}


def modify(block, tid, delta, tx, idx=0):
    return {"address": config.POOL_MANAGER, "blockNumber": hex(block), "logIndex": hex(idx), "transactionHash": tx,
            "topics": [C.MODIFY_LIQUIDITY, POOL, w(config.POSITION_MANAGER)],
            "data": "0x" + C.word(-600) + C.word(600) + C.word(delta) + C.word(tid)}


class FakeRPC:
    def __init__(self):
        self.logs = [transfer(1000, 7, config.ZERO, W, "0xa"), modify(1000, 7, 10 ** 18, "0xa"),
                     modify(1500, 8, 10 ** 18, "0xc"),  # someone else's position in the same pool
                     modify(1800, 7, -(10 ** 17), "0xd"),  # a partial withdrawal in between
                     modify(2000, 7, -(9 * 10 ** 17), "0xb"), transfer(2000, 7, W, config.ZERO, "0xb"),
                     transfer(3000, 9, config.ZERO, W, "0xe"), modify(3000, 9, 10 ** 18, "0xe", 2),
                     transfer(3500, 10, config.ZERO, OTHER, "0xf")]
        self.log_calls = 0

    def call(self, method, params):
        if method == "eth_blockNumber":
            return hex(HEAD + F.CONFIRMATIONS)
        raise AssertionError(method)

    def batch(self, calls, size=40):
        out = []
        for m, p in calls:
            if m == "eth_getBlockByNumber":
                out.append({"timestamp": hex(T0 + int(p[0], 16))})
            elif m == "eth_getTransactionReceipt":
                out.append({"logs": [lg for lg in self.logs if lg["transactionHash"] == p[0]]})
            else:
                raise AssertionError(m)
        return out

    def get_logs(self, flt, lo, hi, chunk, progress=None, ceiling=None):
        self.log_calls += 1
        out = []
        for lg in self.logs:
            if lg["address"] != flt["address"] or not lo <= int(lg["blockNumber"], 16) <= hi:
                continue
            if all(t is None or (i < len(lg["topics"]) and lg["topics"][i] == t) for i, t in enumerate(flt["topics"])):
                out.append(lg)
        return out, chunk


class NoPricer:
    def __init__(self, *a, **k):
        pass

    def value_events(self, ids):
        return 0

    def value_open(self, ids, block):
        return {}

    def aggregate(self, ids, vals):
        return 0

    def refresh_tokens(self, pools):
        return None


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.con = F.connect(Path(self.d.name) / "c.db")
        self._pricer, F.Pricer = F.Pricer, NoPricer
        self._rpt, F.Loader.refresh_pools_tokens = F.Loader.refresh_pools_tokens, lambda self: None

    def tearDown(self):
        F.Pricer, F.Loader.refresh_pools_tokens = self._pricer, self._rpt
        self.con.close()
        self.d.cleanup()

    def test_finds_the_wallets_positions_and_events(self):
        rpc = FakeRPC()
        cfg = config.load_env()
        res = F.Loader(self.con, rpc, rpc, {**cfg, "LOOKBACK_DAYS": "0"}).load([W], T0 + 10)
        self.assertEqual(res["positions"], 2)
        pos = {r["token_id"]: dict(r) for r in self.con.execute("SELECT * FROM positions")}
        self.assertEqual(set(pos), {7, 9})
        self.assertEqual((pos[7]["owner"], pos[7]["open_block"], pos[7]["close_block"], pos[7]["n_events"]),
                         (W, 1000, 2000, 3))
        self.assertEqual(pos[7]["close_ts"], T0 + 2000)
        self.assertIsNone(pos[9]["close_ts"])
        self.assertEqual({r[0] for r in self.con.execute("SELECT DISTINCT token_id FROM events")}, {7, 9})
        # again at the same head: everything is cached, nothing read
        n = rpc.log_calls
        F.Loader(self.con, rpc, rpc, {**cfg, "LOOKBACK_DAYS": "0"}).load([W], T0 + 10)
        self.assertEqual(rpc.log_calls, n)

    def test_block_at(self):
        self.assertEqual(F.Loader(self.con, FakeRPC(), FakeRPC(), {}).block_at(T0 + 2345, HEAD), 2345)


class RangesTest(unittest.TestCase):
    def test_missing_and_mark(self):
        with tempfile.TemporaryDirectory() as d:
            con = F.connect(Path(d) / "c.db")
            self.assertEqual(F.missing(con, "k", 1, 100), [(1, 100)])
            F.mark(con, "k", 10, 20)
            F.mark(con, "k", 40, 50)
            self.assertEqual(F.missing(con, "k", 1, 100), [(1, 9), (21, 39), (51, 100)])
            F.mark(con, "k", 21, 39)
            self.assertEqual([tuple(r) for r in con.execute("SELECT lo, hi FROM scans WHERE key='k'")], [(10, 50)])
            con.close()
        self.assertEqual(F.merge([(5, 9), (1, 3), (4, 4), (20, 30)]), [(1, 9), (20, 30)])


if __name__ == "__main__":
    unittest.main()
