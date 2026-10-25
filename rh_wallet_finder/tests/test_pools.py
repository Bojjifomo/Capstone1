"""Offline tests: python3 -m unittest discover -s rh_wallet_finder/tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import db  # noqa: E402
import pools  # noqa: E402
import publish  # noqa: E402

NOW = 1_800_000_000
DAY = 86400
TOKEN = "0x" + "11" * 20


class BestPoolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "w.db")
        self.con.execute("INSERT INTO tokens(addr, symbol, mcap) VALUES(?, 'VRAX', 2e6), (?, 'USDG', NULL)",
                         (TOKEN, config.USDG))
        self.con.execute("INSERT INTO pools(id, c0, c1, fee) VALUES('0xhot', ?, ?, 10000), ('0xcalm', ?, ?, 500)",
                         (TOKEN, config.USDG, TOKEN, config.USDG))
        self.id = 0

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def pos(self, pool, owner, dep, fee, pnl, hold_days, closed_ago=DAY, **kw):
        self.id += 1
        close = NOW - closed_ago
        row = {"token_id": self.id, "owner": owner, "pool": pool, "tl": -600, "tu": 600, "open_ts": close - hold_days * DAY,
               "close_ts": close, "minted": 1, "valued": 1, "dep_usd": dep, "fee_usd": fee, "pnl_usd": pnl, **kw}
        self.con.execute(f"INSERT INTO positions({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))

    def test_fee_and_net_apr_per_capital_day(self):
        for w in ("0xa", "0xb", "0xc"):  # $1,000 for 1 day earning $10 fee, keeping $4 = 365% / 146% a year
            self.pos("0xhot", w, 1000, 10, 4, 1)
        for w in ("0xa", "0xb", "0xc"):  # $1,000 for 2 days earning $2: 36.5% fee APR, IL took more than that
            self.pos("0xcalm", w, 1000, 2, -6, 2)
        out = pools.best_pools(self.con, min_positions=3, now=NOW)
        self.assertEqual([p["pool"] for p in out], ["0xhot", "0xcalm"])
        hot, calm = out
        self.assertAlmostEqual(hot["fee_apr"], 365.0)
        self.assertAlmostEqual(hot["net_apr"], 146.0)
        self.assertAlmostEqual(hot["fee_per_1k"], 10.0)
        self.assertAlmostEqual(calm["fee_apr"], 36.5)
        self.assertLess(calm["net_apr"], 0)
        self.assertEqual((hot["pair"], hot["fee_tier"], hot["wallets"], hot["mcap"]), ("VRAX/USDG", 1.0, 3, 2e6))
        self.assertAlmostEqual(hot["top_share"], 100 / 3)
        self.assertEqual(hot["win"], 100.0)
        self.assertEqual(calm["win"], 0.0)
        self.assertEqual(pools.best_pools(self.con, min_positions=3, sort="net_apr", now=NOW)[0]["pool"], "0xhot")

    def test_thin_old_unpriced_and_launch_positions_dont_count(self):
        self.pos("0xhot", "0xa", 1000, 500, 400, 1)  # one wallet only: a fluke, not a pool
        self.pos("0xhot", "0xa", 1000, 500, 400, 1)
        self.pos("0xhot", "0xa", 1000, 500, 400, 1)
        self.pos("0xcalm", "0xb", 1000, 9, 9, 1, closed_ago=30 * DAY)  # outside the 7-day window
        self.pos("0xcalm", "0xc", 1000, 9, 9, 1, valued=0)
        self.pos("0xcalm", "0xd", 1000, 9, 9, 1, launch=1)
        self.assertEqual(pools.best_pools(self.con, min_positions=1, min_wallets=2, now=NOW), [])
        self.assertEqual(len(pools.best_pools(self.con, min_positions=1, min_wallets=1, now=NOW)), 1)

    def test_snapshot_carries_pools(self):
        for w in ("0xa", "0xb", "0xc"):
            for _ in range(4):
                self.pos("0xhot", w, 1000, 10, 4, 1, closed_ago=3600)
        import time
        real = time.time
        time.time = lambda: NOW  # noqa: E731 - snapshot and best_pools both read the clock
        try:
            snap = publish.snapshot(self.con)
        finally:
            time.time = real
        self.assertEqual(snap["pools"][0]["pair"], "VRAX/USDG")
        self.assertEqual(len(snap["pools"][0]["top_wallets"]), 3)
        json.dumps(snap)  # all plain JSON


if __name__ == "__main__":
    unittest.main()
