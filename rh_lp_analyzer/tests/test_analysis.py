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
        self.assertEqual(a["verdict"], "ELITE")
        self.assertEqual(a["play"], "Intraday Swinger")  # $1M+ tokens held for hours

    def test_real_pnl_charges_cost(self):
        raw = A.analyze(self.ps, NOW, NOW - 30 * DAY)
        real = A.analyze(self.ps, NOW, NOW - 30 * DAY, real=True)
        self.assertAlmostEqual(raw["net"] - real["net"], 16)

    def test_losing_wallet_rejected(self):
        ps = [pos(i, (10 - i) * DAY, 3600 * 5, 1000, -30) for i in range(10)]
        self.assertEqual(A.analyze(ps, NOW, NOW - 30 * DAY)["verdict"], "REJECT")

    def test_few_trades_only_watch(self):
        a = A.analyze(self.ps[:5], NOW, NOW - 30 * DAY)
        self.assertEqual(a["verdict"], "MIXED")
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


class ClassifyTest(unittest.TestCase):
    def test_archetypes(self):
        def t(hold, mcap, width):
            p = pos(1, 10 * DAY, hold, 100, 1, mcap=mcap)
            p["tu"] = width
            return A.M.trades([p])
        self.assertEqual(A.archetype(t(1800, 5e4, 600), NOW)[0], "Spike Hunter")
        self.assertEqual(A.archetype(t(1800, 5e6, 600), NOW)[0], "Liquid Scalper")
        self.assertEqual(A.archetype(t(5 * 3600, 5e4, 600), NOW)[0], "Micro Degen")
        self.assertEqual(A.archetype(t(3 * DAY, 5e4, 600), NOW)[0], "Conviction Holder")  # ~6% wide
        self.assertEqual(A.archetype(t(3 * DAY, 5e6, 9000), NOW)[0], "Patient Big-Cap LP")
        self.assertEqual(A.archetype(t(3 * DAY, 5e4, 9000), NOW)[0], "Range Farmer")
        mixed = A.M.trades([pos(i, 10 * DAY, h, 100, 1, mcap=m) for i, (h, m) in
                            enumerate([(1800, 5e4), (5 * 3600, 5e6), (3 * DAY, 5e4)])])
        self.assertEqual(A.archetype(mixed, NOW)[0], "Adaptive")

    def test_trader_types(self):
        slow = A.M.trades([pos(i, 10 * DAY, 8 * 3600, 100, 1, mcap=5e6) for i in range(4)])
        fast = A.M.trades([pos(i, 10 * DAY, 600, 100, 1, mcap=5e4) for i in range(4)])
        self.assertEqual(A.trader_type(slow, NOW, -5)[0], "multiday")
        self.assertEqual(A.trader_type(fast, NOW, -5)[0], "degen")
        self.assertEqual(A.trader_type(slow[:2] + fast[:2], NOW, -5)[0], "hybrid")

    def test_filter_positions(self):
        ps = [pos(1, DAY, 600, 100, 1, mcap=5e4, side="bid"), pos(2, DAY, 600, 100, 1, mcap=5e6, side="ask"),
              pos(3, DAY, 600, 100, 1, mcap=None, side="both")]
        self.assertEqual([p["token_id"] for p in A.filter_positions(ps, mcap_min=1e6)], [2])
        self.assertEqual([p["token_id"] for p in A.filter_positions(ps, sides=("bid", "both"))], [1, 3])
        self.assertEqual(len(A.filter_positions(ps)), 3)


class AllocateTest(unittest.TestCase):
    def wallets(self):
        steady = [pos(i, (25 - i) * DAY, 8 * 3600, 1000, 30 if i % 5 else -10) for i in range(20)]
        wild = [pos(100 + i, (25 - i) * DAY, 8 * 3600, 1000, 300 if i % 3 else -450) for i in range(20)]
        loser = [pos(200 + i, (25 - i) * DAY, 8 * 3600, 1000, -20) for i in range(20)]
        return {k: A.analyze(v, NOW, NOW - 30 * DAY) for k, v in
                (("steady", steady), ("wild", wild), ("loser", loser))}

    def test_deploys_the_chosen_share_over_every_followed_wallet(self):
        res = self.wallets()
        self.assertEqual(res["wild"]["verdict"], "MIXED")
        self.assertEqual(res["loser"]["verdict"], "REJECT")
        for name in A.PROFILES:
            r = A.allocate(res, 2500, name, deploy_pct=92)
            self.assertAlmostEqual(r["deployed"], 2300)
            self.assertAlmostEqual(r["reserve"], 200)
            self.assertEqual({x["addr"] for x in r["rows"]}, {"steady", "wild"})  # REJECT left out by default
            self.assertEqual(r["skipped"], ["loser"])
            with_rej = A.allocate(res, 10000, name, deploy_pct=92, include_reject=True)
            self.assertEqual(len(with_rej["rows"]), 3)
            self.assertAlmostEqual(with_rej["deployed"], 9200)
        # at $2,500 Aggressive's 2% floor for the REJECT wallet is $46: under the $50 ticket, dropped
        self.assertEqual(A.allocate(res, 2500, "Aggressive", include_reject=True)["dropped"], ["loser"])

    def test_profiles_lean_differently(self):
        res = self.wallets()
        safe = {x["addr"]: x["usd"] for x in A.allocate(res, 10000, "Safety")["rows"]}
        aggr = {x["addr"]: x["usd"] for x in A.allocate(res, 10000, "Aggressive")["rows"]}
        self.assertGreater(safe["steady"], safe["wild"])  # steadier daily PnL
        self.assertGreater(aggr["wild"] / aggr["steady"], safe["wild"] / safe["steady"])  # chases ROI

    def test_cap_and_floor(self):
        many = {f"w{i}": A.analyze([pos(1000 * i + j, (25 - j) * DAY, 8 * 3600, 1000, 30 + i * 20 if j % 5 else -10)
                                    for j in range(20)], NOW, NOW - 30 * DAY) for i in range(8)}
        r = A.allocate(many, 100000, "Aggressive")
        shares = [x["usd"] / r["deployed"] for x in r["rows"]]
        self.assertLessEqual(max(shares), A.PROFILES["Aggressive"]["cap"] + 1e-9)
        self.assertGreaterEqual(min(shares), A.PROFILES["Aggressive"]["floor"] - 1e-9)
        self.assertAlmostEqual(r["deployed"], 95000)

    def test_tiny_capital_drops_small_tickets(self):
        r = A.allocate(self.wallets(), 60, "Balanced")
        self.assertEqual(len(r["rows"]), 1)  # $57 can't be split into two $50+ tickets
        self.assertEqual(A.allocate(self.wallets(), 40, "Balanced")["rows"], [])

    def test_simulation(self):
        res = self.wallets()
        r = A.allocate(res, 2500, "Balanced")
        sim = r["sim"]
        self.assertTrue(sim["daily"])
        # each wallet's daily PnL scaled to your share of it, added up
        want = sum(x["usd"] / res[x["addr"]]["capital"] * res[x["addr"]]["net"] for x in r["rows"])
        self.assertAlmostEqual(sim["total"], want, places=6)
        self.assertLessEqual(sim["max_dd"], 0)
        self.assertEqual(len(sim["pairs"]), 1)

    def test_overlaps(self):
        res = self.wallets()
        self.assertTrue(A.overlaps(res))  # all three play pools p0..p2


class RiskTest(unittest.TestCase):
    def curve(self, pnls):
        cum, out = 0.0, []
        for i, v in enumerate(pnls):
            cum += v
            out.append({"day": i * DAY, "pnl": v, "cum": cum})
        return out

    def test_drawdown_and_recovery(self):
        d = A.drawdown_stats(self.curve([10, 10, -5, -10, 3, 20, 5]), 100)
        self.assertAlmostEqual(d["max_dd"], -15.0)  # 20 -> 5
        self.assertEqual((d["fall_days"], d["recover_days"], d["underwater_days"]), (2, 2, 4))
        self.assertTrue(d["recovered"])
        self.assertEqual(d["current_dd"], 0)

    def test_still_under_water(self):
        d = A.drawdown_stats(self.curve([10, -4, -4, 2]), 100)
        self.assertFalse(d["recovered"])
        self.assertIsNone(d["recover_days"])
        self.assertAlmostEqual(d["current_dd"], -6.0)
        self.assertEqual(d["days_since_peak"], 3)

    def test_loss_recovery_and_streaks(self):
        avg, longest, open_ = A.loss_recovery(self.curve([5, -3, 1, 4, -10, 2]))
        self.assertEqual((avg, longest, open_), (2, 2, 1))  # -3 won back 2 days later; -10 not yet
        self.assertEqual(A.streak([1, -1, -2, 3, -1, -1, -1]), 3)

    def test_in_analyze(self):
        ps = [pos(i, (20 - i) * DAY, 6 * 3600, 1000, 50 if i % 4 else -40) for i in range(16)]
        r = A.analyze(ps, NOW, NOW - 30 * DAY)["risk"]
        self.assertEqual(r["loss_streak"], 1)
        self.assertEqual((r["best"], r["worst"]), (50, -40))
        self.assertGreater(r["consistency"], 0)


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
