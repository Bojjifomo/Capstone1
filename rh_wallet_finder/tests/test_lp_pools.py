"""Offline tests: python3 -m unittest discover -s rh_wallet_finder/tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import db  # noqa: E402
import lp_pools as pools  # noqa: E402
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
        self.assertEqual(snap["pool_results"][0]["pair"], "VRAX/USDG")
        self.assertEqual(len(snap["pool_results"][0]["top_wallets"]), 3)
        self.assertEqual(snap["pools"], {"at": None, "rows": []})  # no pool scan yet
        json.dumps(snap)  # all plain JSON

    def test_snapshot_carries_the_last_pool_scan(self):
        import time
        import pools as P
        rows = [{"pool": "0xp1", "pair": "MEME / USDG", "dex": "uniswap-v4", "fee_pct": 1.0, "fee_src": "slot0",
                 "tvl": 10_000.0, "vol_1h": 1000.0, "vol_6h": 0.0, "vol_24h": 12_000.0, "mcap": 900_000.0,
                 "asset": TOKEN, "base": "MEME", "quote": "USDG", "traders_24h": 45, "created_ts": None,
                 "name": "MEME / USDG 1%", "gt": "https://gt/p1", "secret_field": "not published"},
                {**{"pool": "0xp2", "pair": "DUST / USDG", "fee_pct": 1.0, "tvl": 10_000.0, "vol_1h": 0.0,
                    "vol_6h": 0.0, "vol_24h": 10.0, "mcap": 1000.0, "asset": TOKEN, "base": "DUST", "quote": "USDG",
                    "traders_24h": 45, "created_ts": None}}]  # under the $500k market cap floor
        for r in rows:
            P.compute(r)
        cache = Path(self.tmp.name) / "pools.json"
        cache.write_text(json.dumps({"at": time.time() - 600, "pools": rows, "errors": []}))
        from unittest import mock
        with mock.patch.object(P, "CACHE", cache):
            got = publish.snapshot(self.con)["pools"]
            with mock.patch.object(P, "scan") as scan:
                publish.refresh_pools({}, log=lambda *a: None)  # 10 minutes old: no rescan
                scan.assert_not_called()
            cache.write_text(json.dumps({"at": time.time() - 2 * 3600, "pools": rows, "errors": []}))
            with mock.patch.object(P, "scan", side_effect=RuntimeError("offline")) as scan:
                publish.refresh_pools({}, log=lambda *a: None)  # 2 hours old: rescans, a failure is only logged
                scan.assert_called_once()
        self.assertEqual([r["pool"] for r in got["rows"]], ["0xp1"])
        self.assertAlmostEqual(got["rows"][0]["apr_24h"], 120 * 365 / 10_000 * 100)
        self.assertNotIn("secret_field", got["rows"][0])


if __name__ == "__main__":
    unittest.main()
