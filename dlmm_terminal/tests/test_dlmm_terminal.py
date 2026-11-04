import unittest
from datetime import datetime, timezone

from dlmm_terminal import exits, filters
from dlmm_terminal.api import SOL_MINT

CFG = {
    "universe": {"binStepMin": 20, "binStepMax": 250},
    "gates": {"minPoolAgeMinutes": 60, "minFeeApr24hPct": 1000, "maxPositionPctOfTvl": 3},
    "position": {"strategy": "Spot", "side": "SOL-only", "rangeBottomPct": -70,
                 "bankrollSol": 160, "lossBudgetPct": 5, "defaultSizeSol": 8},
    "flags": {"pump1hPct": 25, "drop6hPct": -17, "minTvlUsd": 25000, "nightEntryLocalHours": [0, 9], "utcOffsetHours": 7},
}
NOW = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)  # 15:00 UTC+7


def snap(**kw):
    s = {"address": "P", "name": "MEME-SOL", "tvl": 100_000, "created_at": (NOW.timestamp() - 48 * 3600) * 1000,
         "token_x": {"address": "MEME", "symbol": "MEME", "market_cap": 1e6, "holders": 900},
         "token_y": {"address": SOL_MINT, "symbol": "SOL", "price": 100},
         "pool_config": {"bin_step": 100, "base_fee_pct": 1},
         "fees": {"1h": 200, "24h": 4000}, "volume": {"24h": 500_000}, "current_price": 1e-5}
    s.update(kw)
    return s


def candles(closes):
    return [{"timestamp": i, "open": c, "high": c * 1.1, "low": c * 0.9, "close": c} for i, c in enumerate(closes)]


class FilterTests(unittest.TestCase):
    def test_passes_all_gates(self):
        c = filters.evaluate(snap(), candles([1e-5] * 26), CFG, NOW)
        self.assertEqual(c["decision"], "propose")
        self.assertAlmostEqual(c["feeApr24h"], 4000 / 100_000 * 8760 / 24 * 100, places=0)  # 1460%
        self.assertEqual(c["sizeVsTvlPct"], 0.8)
        self.assertEqual(c["entryHourLocal"], 15)
        self.assertEqual(c["flags"], [])

    def test_loss_budget_caps_size(self):
        self.assertAlmostEqual(filters.max_position_sol(CFG), 8.8889, places=3)
        self.assertEqual(filters.planned_size_sol(CFG, 20), 8.8889)

    def test_gates_fail_with_reasons(self):
        s = snap(tvl=10_000, created_at=(NOW.timestamp() - 1800) * 1000, fees={"1h": 0, "24h": 1})
        s["pool_config"]["bin_step"] = 10
        c = filters.evaluate(s, candles([1e-5] * 26), CFG, NOW)
        self.assertEqual(c["decision"], "skip")
        joined = " ".join(c["gatesFailed"])
        for g in ("binStep", "poolAge", "feeApr24h", "sizeVsTvl"):
            self.assertIn(g, joined)

    def test_size_vs_tvl_gate_off_when_null(self):
        cfg = {**CFG, "gates": {**CFG["gates"], "maxPositionPctOfTvl": None}}
        c = filters.evaluate(snap(tvl=10_000, fees={"1h": 0, "24h": 400}), candles([1e-5] * 26), cfg, NOW)
        self.assertGreater(c["sizeVsTvlPct"], 3)          # still computed and shown
        self.assertFalse(any(g.startswith("sizeVsTvl") for g in c["gatesFailed"]))

    def test_young_pool_fee_apr_uses_hours_available(self):
        s = snap(created_at=(NOW.timestamp() - 2 * 3600) * 1000, fees={"1h": 0, "24h": 100})
        f = filters.features(s, [], 8, NOW)
        self.assertAlmostEqual(f["feeApr24h"], 100 / 100_000 * 8760 / 2 * 100, places=0)

    def test_flags(self):
        closes = [1.0] * 19 + [1.3, 1.3, 1.3, 1.3, 1.3, 1.0, 1.3]  # +30% in last hour
        c = filters.evaluate(snap(tvl=20_000, fees={"1h": 0, "24h": 4000}), candles(closes), CFG,
                             datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc))  # 03:00 local
        self.assertTrue(any(f.startswith("pump1h") for f in c["flags"]))
        self.assertTrue(any(f.startswith("tvl") for f in c["flags"]))
        self.assertTrue(any(f.startswith("night") for f in c["flags"]))

    def test_sol_as_token_x_inverts_price(self):
        s = snap(token_x={"address": SOL_MINT, "symbol": "SOL", "price": 100},
                 token_y={"address": "MEME", "symbol": "MEME"}, current_price=1e5)
        f = filters.features(s, candles([1e5, 5e4]), 8, NOW)
        self.assertEqual(f["symbol"], "MEME")
        self.assertAlmostEqual(f["priceChg1h"], 100.0)  # SOL buys half as much MEME → MEME doubled


EX_DEFAULT = {"alertAtPnlPct": -10, "alertBottomThirdOfRange": True, "closeBelowRangeAfterMin": 10,
              "priceStopBelowEntryPct": None, "takeProfitPct": None, "stopLossPct": None,
              "trailingPts": None, "trailingArmPct": 1, "maxHoldMin": None}


def tick(now=0, pnl=0.0, price=1.0, lo=0.3, hi=1.0):
    return exits.Tick("pos", "pool", "MEME", now, 0, pnl, None, price, lo, hi)


class ExitTests(unittest.TestCase):
    def test_defaults_never_close_a_winner(self):
        st = exits.State()
        for pnl in (1, 5, 20, 3):
            self.assertEqual([e for e in exits.evaluate(tick(pnl=pnl), st, EX_DEFAULT) if e["kind"] == "close"], [])

    def test_pnl_alert_once_and_rearms(self):
        st = exits.State()
        self.assertEqual([e["rule"] for e in exits.evaluate(tick(pnl=-11, price=0.9), st, EX_DEFAULT)], ["pnlAlert"])
        self.assertEqual(exits.evaluate(tick(pnl=-12, price=0.9), st, EX_DEFAULT), [])
        exits.evaluate(tick(pnl=-2, price=0.9), st, EX_DEFAULT)
        self.assertEqual([e["rule"] for e in exits.evaluate(tick(pnl=-11, price=0.9), st, EX_DEFAULT)], ["pnlAlert"])

    def test_bottom_third_alert(self):
        ev = exits.evaluate(tick(price=0.5), exits.State(), EX_DEFAULT)  # bottom third = below 0.5333
        self.assertIn("bottomThird", [e["rule"] for e in ev])

    def test_close_after_10_min_below_range(self):
        st = exits.State()
        exits.evaluate(tick(now=0, price=0.25), st, EX_DEFAULT)
        self.assertFalse([e for e in exits.evaluate(tick(now=300, price=0.25), st, EX_DEFAULT) if e["kind"] == "close"])
        ev = exits.evaluate(tick(now=600, price=0.25), st, EX_DEFAULT)
        self.assertEqual(ev[0]["rule"], "belowRange")
        # back in range resets the timer
        exits.evaluate(tick(now=700, price=0.5), st, EX_DEFAULT)
        self.assertIsNone(st.below_since)

    def test_optional_tp_trailing_sl(self):
        ex = dict(EX_DEFAULT, takeProfitPct=5, trailingPts=2, stopLossPct=-15)
        st = exits.State()
        self.assertEqual(exits.evaluate(tick(pnl=6), st, ex)[0]["rule"], "takeProfit")
        st = exits.State()
        exits.evaluate(tick(pnl=4), st, dict(ex, takeProfitPct=None))
        self.assertEqual(exits.evaluate(tick(pnl=1.5), st, dict(ex, takeProfitPct=None))[0]["rule"], "trailing")
        self.assertEqual(exits.evaluate(tick(pnl=-16, price=0.9), exits.State(), ex)[0]["rule"], "stopLoss")

    def test_price_stop(self):
        ex = dict(EX_DEFAULT, priceStopBelowEntryPct=-40)
        self.assertEqual(exits.evaluate(tick(price=0.59), exits.State(), ex)[0]["rule"], "priceStop")

    def test_close_sent_suppresses_repeat(self):
        ex = dict(EX_DEFAULT, takeProfitPct=1)
        st = exits.State(close_sent=True)
        self.assertFalse([e for e in exits.evaluate(tick(pnl=5), st, ex) if e["kind"] == "close"])

    def test_token_side_position_skips_range_rules(self):
        st, t = exits.State(), tick(now=0, price=0.25)
        t.sol_only = False
        exits.evaluate(t, st, EX_DEFAULT)
        t.now = 900
        self.assertEqual(exits.evaluate(t, st, EX_DEFAULT), [])
        t.pnl_pct = -12
        self.assertEqual([e["rule"] for e in exits.evaluate(t, st, EX_DEFAULT)], ["pnlAlert"])

    def test_state_roundtrip(self):
        st = exits.State()
        exits.evaluate(tick(pnl=3, price=0.25), st, EX_DEFAULT)
        st2 = exits.State.from_dict(st.to_dict())
        self.assertEqual((st2.peak_pnl, st2.below_since, st2.alerted), (st.peak_pnl, st.below_since, st.alerted))


class WebVerdictTests(unittest.TestCase):
    def test_levels(self):
        from dlmm_terminal import web
        base = {"gatesFailed": [], "flags": [], "feeApr24h": 2000, "sizeVsTvlPct": 1.0, "poolAgeHours": 5}
        self.assertEqual(web.verdict(base, None)["level"], "OPEN")
        self.assertEqual(web.verdict({**base, "flags": ["pump1h +30%"]}, None)["level"], "CAREFUL")
        self.assertEqual(web.verdict({**base, "gatesFailed": ["poolAge: 30m < 60m"]}, None)["level"], "WAIT")
        self.assertEqual(web.verdict({**base, "gatesFailed": ["binStep: 10 outside 20-250"]}, None)["level"], "SKIP")

    def test_market_info_never_changes_level(self):
        from dlmm_terminal import web
        base = {"gatesFailed": [], "flags": [], "feeApr24h": 2000, "sizeVsTvlPct": 1.0, "poolAgeHours": 5}
        v = web.verdict(base, {"vol1h": 1, "vol24": 1000, "buys24": 10, "sells24": 90})
        self.assertEqual(v["level"], "OPEN")
        self.assertEqual(len([x for x in v["info"] if not x["ok"]]), 2)


if __name__ == "__main__":
    unittest.main()
