"""Elysium-only behaviour: v3 amounts, launch pools, early LPs, two position managers."""
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chain as C  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import indexer  # noqa: E402


class V3MathTest(unittest.TestCase):
    def test_split_pays_principal_before_fees(self):
        # decrease 100/40, then a collect of 100/55: 15 of token1 is fees
        evs = [
            {"kind": "inc", "amount0": 1000, "amount1": 0},
            {"kind": "dec", "amount0": 100, "amount1": 40},
            {"kind": "col", "amount0": 100, "amount1": 55},
            {"kind": "col", "amount0": 0, "amount1": 5},  # a later fee-only collect
        ]
        out = C.split_v3(evs)
        self.assertEqual(out[0], {"kind": "inc", "a0": 1000, "a1": 0, "f0": 0, "f1": 0})
        self.assertEqual(out[1], {"kind": "dec", "a0": 0, "a1": 0, "f0": 0, "f1": 0})
        self.assertEqual(out[2], {"kind": "col", "a0": 100, "a1": 40, "f0": 0, "f1": 15})
        self.assertEqual(out[3], {"kind": "col", "a0": 0, "a1": 0, "f0": 0, "f1": 5})

    def test_collect_without_a_decrease_is_all_fees(self):
        out = C.split_v3([{"kind": "col", "amount0": 3, "amount1": 4}])
        self.assertEqual((out[0]["f0"], out[0]["f1"], out[0]["a0"]), (3, 4, 0))

    def test_decode_v3_increase(self):
        log = {"topics": [C.INCREASE_LIQUIDITY, "0x" + C.word(7)],
               "data": "0x" + C.word(500) + C.word(10 ** 18) + C.word(10 ** 6),
               "blockNumber": "0x10", "logIndex": "0x1", "transactionHash": "0xab",
               "blockTimestamp": "0x64"}
        d = C.decode_v3_liq(log)
        self.assertEqual((d["token_id"], d["liq"], d["amount0"], d["amount1"], d["ts"]),
                         (7, 500, 10 ** 18, 10 ** 6, 100))

    def test_hype_usdc_pool_address(self):
        v = config.V3_VENUES[0]
        self.assertEqual(C.v3_pool_address(v["factory"], v["init_hash"], config.USDC, config.WHYPE, 10000),
                         "0xec2e846b66eee17bc856129aa9e9b1baee4d521d")

    def test_two_managers_do_not_share_a_position(self):
        with tempfile.TemporaryDirectory() as d:
            con = db.connect(Path(d) / "t.db")
            for venue in ("0x" + "11" * 20, "0x" + "22" * 20):
                con.execute("""INSERT INTO events(block, log_index, tx, ts, token_id, pool, tl, tu, delta, venue)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (10, 1 if venue.endswith("11") else 2, "0x", 10, 1, "0x" + "ab" * 32, -10, 10, "100", venue))
                con.execute("""INSERT INTO transfers(block, log_index, ts, token_id, from_addr, to_addr, venue)
                               VALUES(?,?,?,?,?,?,?)""",
                            (10, 1 if venue.endswith("11") else 2, 10, 1, config.ZERO, "0x" + "aa" * 20, venue))
            con.commit()
            indexer.rebuild_positions(con, ["0x" + "11" * 20 + ":1", "0x" + "22" * 20 + ":1"])
            rows = con.execute("SELECT venue, token_id, owner, minted FROM positions ORDER BY venue").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({r[0] for r in rows}, {"0x" + "11" * 20, "0x" + "22" * 20})
            self.assertTrue(all(r[2] == "0x" + "aa" * 20 and r[3] == 1 for r in rows))
            con.close()


class LaunchTest(unittest.TestCase):
    def test_locker_position_flags_the_pool_and_an_early_lp(self):
        with tempfile.TemporaryDirectory() as d:
            con = db.connect(Path(d) / "t.db")
            pool = "0x" + "cd" * 20
            con.execute("""INSERT INTO pools(id, c0, c1, fee, spacing, hooks) VALUES(?,?,?,10000,200,?)""",
                        (pool, config.WHYPE, "0x" + "33" * 20, config.ZERO))
            # locker NFT at t=1000, a wallet 10 minutes later, another an hour later
            for owner, ts, tid in ((config.LOCKER, 1000, 1), ("0x" + "ab" * 20, 1000 + 600, 2),
                                   ("0x" + "cd" * 20, 1000 + 7200, 3)):
                con.execute("""INSERT INTO positions(token_id, owner, pool, open_ts, venue, minted)
                               VALUES(?,?,?,?, 'v3', 1)""", (tid, owner, pool, ts))
            con.commit()
            indexer.mark_launches(con, 30)
            flags = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT owner, launch, early FROM positions")}
            self.assertEqual(flags[config.LOCKER], (1, 0))
            self.assertEqual(flags["0x" + "ab" * 20], (0, 1))  # inside 30 min
            self.assertEqual(flags["0x" + "cd" * 20], (0, 0))  # too late
            self.assertEqual(con.execute("SELECT creator_fee FROM pools").fetchone()[0], 1)
            con.close()


if __name__ == "__main__":
    unittest.main()
