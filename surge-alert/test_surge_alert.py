import unittest
from unittest import mock

import surge_alert as sa

NOW = 1_800_000_000.0
CFG = sa.Config()
TOKEN = "0x" + "ab" * 20


def pair(price=1.0, changes=None, liq=50_000, v1=20_000, v6=30_000, buys=70, sells=30,
         symbol="IGNITION", addr=TOKEN, chain="robinhood"):
    return {
        "chainId": chain, "url": f"https://dexscreener.com/{chain}/0xpair",
        "baseToken": {"address": addr, "symbol": symbol},
        "priceUsd": str(price), "priceChange": changes or {},
        "liquidity": {"usd": liq}, "volume": {"h1": v1, "h6": v6},
        "txns": {"h1": {"buys": buys, "sells": sells}}, "fdv": 1_200_000,
    }


def series(*points_min_ago_price):
    return [[NOW - m * 60, p] for m, p in points_min_ago_price]


class DetectTest(unittest.TestCase):
    def test_dip_then_rebound(self):
        hist = series((600, 1.0), (300, 0.7), (60, 0.4), (30, 0.45), (0, 0.6))
        sig = sa.detect(hist, NOW, CFG)
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.drop, 60)
        self.assertAlmostEqual(sig.rebound, 50)
        self.assertEqual(sig.low_ts, NOW - 3600)

    def test_pump_without_prior_drop(self):
        hist = series((600, 0.5), (300, 0.6), (60, 0.7), (0, 1.0))
        self.assertIsNone(sa.detect(hist, NOW, CFG))

    def test_still_dumping(self):
        hist = series((600, 1.0), (60, 0.5), (0, 0.4))
        self.assertIsNone(sa.detect(hist, NOW, CFG))

    def test_small_bounce_ignored(self):
        hist = series((600, 1.0), (60, 0.4), (0, 0.44))
        self.assertIsNone(sa.detect(hist, NOW, CFG))

    def test_old_low_ignored(self):
        # dasar 10 jam lalu, udah lama mantul — bukan sinyal baru
        hist = series((1200, 1.0), (600, 0.3), (120, 0.55), (0, 0.6))
        self.assertIsNone(sa.detect(hist, NOW, CFG))

    def test_fast_crash_inside_recent_window(self):
        hist = series((150, 1.0), (90, 0.35), (0, 0.6))
        sig = sa.detect(hist, NOW, CFG)
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.drop, 65)


class SnapshotSeedTest(unittest.TestCase):
    def test_implied_past_prices(self):
        pts = sa.implied_past_prices(pair(1.0, {"h1": 100, "h24": -50}), NOW)
        self.assertEqual(pts, [(NOW - 86400, 2.0), (NOW - 3600, 0.5)])

    def test_ignores_minus_100(self):
        self.assertEqual(sa.implied_past_prices(pair(1.0, {"h24": -100}), NOW), [])

    def test_first_sighting_can_alert_immediately(self):
        # 24j lalu 2.5, 6j lalu 2.0, 1j lalu 0.71, 5m lalu 0.95, sekarang 1.0
        p = pair(1.0, {"m5": 5, "h1": 40, "h6": -50, "h24": -60})
        state = sa.load_state("/nonexistent")
        sent = []
        sig = sa.process(state, TOKEN, p, NOW, CFG, sent.append)
        self.assertIsNotNone(sig)
        self.assertGreater(sig.drop, 70)
        self.assertAlmostEqual(sig.rebound, 40)
        self.assertEqual(len(sent), 1)
        self.assertIn("IGNITION bangkit abis jatoh", sent[0])


class ConfirmTest(unittest.TestCase):
    def test_passes(self):
        self.assertEqual(sa.confirm(pair(), CFG), [])

    def test_fails(self):
        for kw, word in [({"liq": 1000}, "liq"), ({"v1": 100}, "vol 1j"),
                         ({"v1": 6000, "v6": 60_000}, "spike"), ({"buys": 40, "sells": 60}, "buy"),
                         ({"buys": 0, "sells": 0}, "buy")]:
            fails = sa.confirm(pair(**kw), CFG)
            self.assertTrue(any(word in f for f in fails), (kw, fails))


class CooldownTest(unittest.TestCase):
    sig = sa.Signal(peak=1, low=0.4, low_ts=NOW - 3600, price=0.6, drop=60, rebound=50)

    def test_first_alert(self):
        self.assertTrue(sa.should_alert(None, self.sig, NOW, CFG))

    def test_same_episode_suppressed_even_after_cooldown(self):
        last = {"ts": NOW - 7200, "price": 0.6, "low_ts": NOW - 3600}
        self.assertFalse(sa.should_alert(last, self.sig, NOW, CFG))

    def test_new_episode_respects_cooldown(self):
        last = {"ts": NOW - 600, "price": 0.6, "low_ts": NOW - 9000}
        self.assertFalse(sa.should_alert(last, self.sig, NOW, CFG))
        last["ts"] = NOW - 3700
        self.assertTrue(sa.should_alert(last, self.sig, NOW, CFG))

    def test_runner_re_alerts(self):
        last = {"ts": NOW - 60, "price": 0.39, "low_ts": NOW - 3600}
        self.assertTrue(sa.should_alert(last, self.sig, NOW, CFG))


class ProcessTest(unittest.TestCase):
    def test_polling_flow_alerts_once(self):
        state, sent = sa.load_state("/nonexistent"), []
        prices = [1.0, 0.9, 0.7, 0.5, 0.4, 0.42, 0.5, 0.55, 0.58, 0.6]
        for i, price in enumerate(prices):
            now = NOW + i * 600
            sa.process(state, TOKEN, pair(price), now, CFG, sent.append)
        self.assertEqual(len(sent), 1)
        self.assertIn("Turun 60%", sent[0])

    def test_history_pruned_to_lookback(self):
        state = sa.load_state("/nonexistent")
        sa.process(state, TOKEN, pair(1.0, {"h24": -50}), NOW, CFG, lambda m: None)
        sa.process(state, TOKEN, pair(1.0), NOW + 3 * 86400, CFG, lambda m: None)
        self.assertTrue(all(NOW + 3 * 86400 - t <= 86400 for t, _ in state["history"][TOKEN]))


class ResolveTest(unittest.TestCase):
    def test_picks_most_liquid_on_chain(self):
        a, b, c = ("0x" + x * 40 for x in "123")
        res = {"pairs": [pair(symbol="AGEM", addr=a, liq=5_000),
                         pair(symbol="AGEM", addr=b, liq=90_000),
                         pair(symbol="AGEM", addr=c, liq=999_999, chain="base")]}
        with mock.patch.object(sa, "get_json", return_value=res), mock.patch.object(sa, "warn"):
            self.assertEqual(sa.resolve("$agem", "robinhood"), b)

    def test_not_on_chain(self):
        res = {"pairs": [pair(symbol="AGEM", chain="base")]}
        with mock.patch.object(sa, "get_json", return_value=res), mock.patch.object(sa, "warn") as w:
            self.assertIsNone(sa.resolve("AGEM", "robinhood"))
            self.assertIn("base", w.call_args[0][0])

    def test_address_passthrough(self):
        self.assertEqual(sa.resolve(TOKEN.upper().replace("0X", "0x"), "robinhood"), TOKEN)


class PumpTest(unittest.TestCase):
    def test_pump_without_prior_dump(self):
        hist = series((600, 1.0), (60, 1.0), (30, 1.1), (0, 1.4))
        self.assertIsNone(sa.detect(hist, NOW, CFG))
        sig = sa.detect_pump(hist, NOW, CFG)
        self.assertEqual(sig.kind, "pump")
        self.assertAlmostEqual(sig.rebound, 40)
        self.assertEqual(sig.peak, 1.4)

    def test_slow_climb_ignored(self):
        hist = series((600, 0.5), (60, 1.0), (30, 1.1), (0, 1.2))
        self.assertIsNone(sa.detect_pump(hist, NOW, CFG))

    def test_rise_outside_window_ignored(self):
        hist = series((180, 1.0), (90, 1.5), (0, 1.55))
        self.assertIsNone(sa.detect_pump(hist, NOW, CFG))

    def test_pump_again_below_old_peak(self):
        # kayak ZERO: puncak udah > 24 jam lalu, ngebleed pelan, sekarang pump lagi
        hist = series((1800, 3.0), (600, 1.6), (60, 1.0), (0, 1.5))
        self.assertIsNone(sa.detect(hist, NOW, CFG))
        sig = sa.detect_pump(hist, NOW, CFG)
        msg = sa.format_alert(pair(1.5, symbol="ZERO"), sig, CFG)
        self.assertIn("ZERO lagi pump", msg)
        self.assertIn("Naik +50%", msg)
        self.assertIn("masih -6% dari puncak 24j", msg)

    def test_pump_only_alerts_for_watchlist(self):
        p = pair(1.0, {"m5": 3, "h1": 40, "h6": 30, "h24": 20})
        for pump, expected in ((False, 0), (True, 1)):
            state, sent = sa.load_state("/nonexistent"), []
            sa.process(state, TOKEN, p, NOW, CFG, sent.append, pump=pump)
            self.assertEqual(len(sent), expected)
        self.assertIn("lagi pump", sent[0])
        self.assertIn("puncak baru 24j", sent[0])

    def test_dip_signal_wins_no_duplicate(self):
        state, sent = sa.load_state("/nonexistent"), []
        p = pair(1.0, {"m5": 5, "h1": 40, "h6": -50, "h24": -60})
        sa.process(state, TOKEN, p, NOW, CFG, sent.append, pump=True)
        sa.process(state, TOKEN, p, NOW + 60, CFG, sent.append, pump=True)
        self.assertEqual(len(sent), 1)
        self.assertIn("bangkit abis jatoh", sent[0])


class WatchlistTest(unittest.TestCase):
    def test_read_watchlist(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# komentar\n0xABC  # ZERO\n\n  IGNITION\n")
        try:
            self.assertEqual(sa.read_watchlist(f.name), ["0xABC", "IGNITION"])
        finally:
            os.unlink(f.name)
        self.assertEqual(sa.read_watchlist("/nonexistent"), [])

    def test_bundled_watchlist_has_zero(self):
        self.assertIn("0x316fa3AB9A8FD8d7567a823DedecF28d9FEE2894", sa.read_watchlist(sa.WATCHLIST))


if __name__ == "__main__":
    unittest.main()
