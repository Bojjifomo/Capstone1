"""Offline tests of the analysis and the capital split: python3 -m unittest discover -s rh_lp_analyzer/tests"""
import calendar
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analysis as A  # noqa: E402

DAY = 86400
NOW = 1_800_000_000


def pos(i, open_ago, hold, dep, pnl, side="bid", mcap=2e6, eth=1, oor=None, fee=1.0):
    o = NOW - open_ago
    return {"token_id": i, "pool": f"p{i % 3}", "pair": f"T{i % 3}/ETH", "tl": 0, "tu": 1000, "open_block": i,
            "open_ts": o, "close_ts": o + hold if hold else None, "dep": dep, "wd": dep + pnl, "fee": fee,
            "now": dep + pnl if not hold else 0.0, "pnl": pnl, "cost": 1.0, "eth_pair": eth, "single": 1,
            "sell_fee": 0.01, "side": side, "mcap": mcap, "oor": oor}


class DeployedTest(unittest.TestCase):
    def test_peak_and_median(self):
        ps = [pos(1, 10 * DAY, 4 * DAY, 100, 0), pos(2, 8 * DAY, 1 * DAY, 300, 0)]
        peak, med = A.deployed(ps, NOW - 10 * DAY, NOW)
        self.assertEqual(peak, 400)  # both open on day 2
        self.assertEqual(med, 100)  # 3 of the 4 busy days held 100


class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        self.ps = [pos(i, (20 - i) * DAY, 6 * 3600, 1000, 50 if i % 4 else -40, oor="below" if i % 4 == 0 else None)
                   for i in range(16)] + [pos(99, DAY, None, 500, 10)]

    def test_core_figures(self):
        a = A.analyze(self.ps, NOW, NOW - 30 * DAY)
        self.assertEqual(a["closed"], 16)
        self.assertEqual(a["open"], 1)
        self.assertEqual(a["wins"], 12)
        self.assertEqual(a["losses"], 4)
        self.assertAlmostEqual(a["net"], 12 * 50 - 4 * 40)
        self.assertAlmostEqual(a["float"], 10)
        self.assertAlmostEqual(a["pf"], 600 / 160)
        self.assertEqual(a["win"], 75)
        self.assertEqual(a["oor_n"], 4)
        self.assertEqual(a["oor_rate"], 25)
        self.assertEqual(a["kind"], "multiday")
        self.assertEqual(a["follow"], "✓")
        self.assertEqual(a["verdict"], "COPY")

    def test_real_pnl_charges_cost(self):
        raw = A.analyze(self.ps, NOW, NOW - 30 * DAY)
        real = A.analyze(self.ps, NOW, NOW - 30 * DAY, real=True)
        self.assertAlmostEqual(raw["net"] - real["net"], 16)

    def test_losing_wallet_rejected(self):
        ps = [pos(i, (10 - i) * DAY, 3600 * 5, 1000, -30) for i in range(10)]
        self.assertEqual(A.analyze(ps, NOW, NOW - 30 * DAY)["verdict"], "REJECT")

    def test_few_trades_only_watch(self):
        a = A.analyze(self.ps[:5], NOW, NOW - 30 * DAY)
        self.assertEqual(a["verdict"], "WATCH")
        self.assertTrue(any("closed positions" in w for w in a["why"]))

    def test_past_period_has_no_open_positions(self):
        a = A.analyze(self.ps, NOW, NOW - 30 * DAY, NOW - 10 * DAY)
        self.assertEqual(a["open"], 0)
        self.assertEqual(a["closed"], sum(1 for p in self.ps if p["close_ts"] and p["close_ts"] <= NOW - 10 * DAY))

    def test_nothing_in_period(self):
        self.assertIsNone(A.analyze([pos(1, 60 * DAY, DAY, 100, 5)], NOW, NOW - 7 * DAY))

    def test_filters_beat_everything(self):
        ps = [pos(i, (20 - i) * DAY, 6 * 3600, 1000, 50 if i % 2 else -60, side="bid" if i % 2 else "ask")
              for i in range(20)]
        f = A.recommend_filters(ps, lambda p: p["pnl"])
        self.assertTrue(f["best"])
        self.assertTrue(all(r["pnl"] > f["all"]["pnl"] for r in f["best"]))
        self.assertEqual(f["best"][0]["name"], "ETH/USDG only")

    def test_sizing(self):
        a = A.analyze(self.ps, NOW, NOW - 30 * DAY)
        s = A.sizing(a, a["capital"] / 10)
        self.assertAlmostEqual(s["ratio"], 0.1)
        self.assertAlmostEqual(s["per_trade"], a["size"] / 10)


class AllocateTest(unittest.TestCase):
    def wallets(self):
        steady = [pos(i, (25 - i) * DAY, 8 * 3600, 1000, 30 if i % 5 else -10) for i in range(20)]
        wild = [pos(100 + i, (25 - i) * DAY, 8 * 3600, 1000, 300 if i % 3 else -250) for i in range(20)]
        loser = [pos(200 + i, (25 - i) * DAY, 8 * 3600, 1000, -20) for i in range(20)]
        return {k: A.analyze(v, NOW, NOW - 30 * DAY) for k, v in
                (("steady", steady), ("wild", wild), ("loser", loser))}

    def test_profiles(self):
        res = self.wallets()
        for name, p in A.PROFILES.items():
            r = A.allocate(res, 2500, name)
            self.assertAlmostEqual(r["deployed"] + r["reserve"], 2500)
            self.assertLessEqual(r["deployed"], 2500 * (1 - p["reserve"]) + 1e-6)
            self.assertNotIn("loser", [x["addr"] for x in r["rows"]])
            for x in r["rows"]:
                self.assertLessEqual(x["usd"], 2500 * (1 - p["reserve"]) * p["cap"] + 1e-6)
                self.assertGreaterEqual(x["usd"], A.MIN_TICKET)
        safe = {x["addr"]: x["usd"] for x in A.allocate(res, 2500, "Safety")["rows"]}
        aggr = {x["addr"]: x["usd"] for x in A.allocate(res, 2500, "Aggressive")["rows"]}
        self.assertIn("steady", safe)
        self.assertNotIn("wild", safe)  # its drawdown is too deep for Safety
        self.assertGreaterEqual(aggr.get("wild", 0), aggr.get("steady", 0))  # Aggressive chases ROI...
        self.assertGreater(A._weight("Aggressive", res["wild"]), 2 * A._weight("Aggressive", res["steady"]))
        self.assertEqual(sum(1 for _ in aggr), 2)  # ...each capped at half of what goes in

    def test_cap_leaves_cash_when_one_wallet(self):
        res = self.wallets()
        r = A.allocate({"steady": res["steady"]}, 2500, "Balanced")
        self.assertAlmostEqual(r["deployed"], 2500 * 0.85 * 0.35)

    def test_tiny_capital_drops_small_tickets(self):
        self.assertEqual(A.allocate(self.wallets(), 40, "Balanced")["rows"], [])

    def test_overlaps(self):
        res = self.wallets()
        self.assertTrue(A.overlaps(res))  # all three play pools p0..p2


class PeriodTest(unittest.TestCase):
    def test_periods(self):
        now = calendar.timegm((2026, 10, 22, 12, 0, 0))
        self.assertEqual(A.period("24 hours", now), (now - 3600 * 24, None))
        self.assertEqual(A.period("7 days", now), (now - 7 * DAY, None))
        self.assertEqual(A.period("This month", now), (calendar.timegm((2026, 10, 1, 0, 0, 0)), None))
        self.assertEqual(A.period("Last month", now), (calendar.timegm((2026, 9, 1, 0, 0, 0)),
                                                       calendar.timegm((2026, 10, 1, 0, 0, 0)) - 1))
        jan = calendar.timegm((2026, 1, 5, 0, 0, 0))
        self.assertEqual(A.period("Last month", jan)[0], calendar.timegm((2025, 12, 1, 0, 0, 0)))


if __name__ == "__main__":
    unittest.main()
