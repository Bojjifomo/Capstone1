import unittest
from unittest import mock

from dlmm_terminal import benchmark, cli
from dlmm_terminal.api import SOL_MINT

T0 = 1_793_700_000  # entry time, unix s


def pos(created, pnl=0.1, dep=8.0, closed=None):
    return {"createdAt": created, "closedAt": closed or created + 3600, "pnlSol": str(pnl),
            "allTimeDeposits": {"total": {"sol": str(dep)}}}


SNAP = {"address": "P", "name": "MEME-SOL", "created_at": (T0 - 10 * 3600) * 1000, "current_price": 2e-5,
        "token_x": {"address": "MEME", "symbol": "MEME", "market_cap": 2_000_000},
        "token_y": {"address": SOL_MINT, "symbol": "SOL", "price": 100},
        "pool_config": {"bin_step": 100}}


class BenchmarkTests(unittest.TestCase):
    def test_slices_opened_together_are_one_entry(self):
        g = benchmark.group_entries([pos(T0), pos(T0 + 120), pos(T0 + 600), pos(T0 + 7200)])
        self.assertEqual([len(x) for x in g], [3, 1])

    def test_entry_features_at_entry_time(self):
        hour = T0 - T0 % 3600
        vol = [{"timestamp": hour - h * 3600, "fees": 100} for h in range(1, 30)]
        candles = [{"timestamp": T0 - 3600, "close": 1e-5}, {"timestamp": T0 - 60, "close": 1e-5 * 1.3}]
        e = benchmark.entry_features([pos(T0, 0.2, 5), pos(T0 + 60, -0.1, 5)], SNAP, vol, candles, "y")
        self.assertEqual(e["poolAgeHours"], 10)
        self.assertEqual(e["fees1hUsd"], 100)
        self.assertEqual(e["fees24hUsd"], 2400)
        self.assertEqual(e["priceChg1h"], 30)
        self.assertEqual(e["mcapUsd"], 1_300_000)  # 2M now x (1.3e-5 then / 2e-5 now)
        self.assertEqual(e["capitalSol"], 10)
        self.assertAlmostEqual(e["pnlSol"], 0.1)
        self.assertTrue(e["win"])
        self.assertEqual(e["slices"], 2)

    def _prof(self):
        entries = [{"pool": f"P{i}", "binStep": 100 if i < 8 else 20, "poolAgeHours": i + 1, "mcapUsd": 1e6,
                    "fees24hUsd": 5000, "fees1hUsd": 200, "priceChg1h": 0, "priceChg6h": 0, "holdMin": 60,
                    "pnlPct": 1, "capitalSol": 8, "pnlSol": 0.1 if i < 8 else -1, "win": i < 8} for i in range(10)]
        return benchmark.profile(entries, "W", 20)

    def test_profile_and_match(self):
        p = self._prof()
        self.assertEqual(p["entries"], 10)
        self.assertEqual(p["binSteps"]["20"]["pnlSol"], -2)
        ok = {"binStep": 100, "poolAgeHours": 5, "mcapUsd": 1e6, "fees24hUsd": 5000, "fees1hUsd": 200}
        self.assertEqual(benchmark.match(ok, p)["score"], 1)
        bad = {**ok, "binStep": 20, "poolAgeHours": 500}
        m = benchmark.match(bad, p)
        self.assertEqual(len(m["misses"]), 2)  # bin step 20 lost SOL, age outside band
        self.assertEqual(m["score"], 0.6)

    def test_signal_pings_only_on_new_matches(self):
        cfg = cli.load_config(None)
        prof = self._prof()
        snap = {"address": "A", "name": "A-SOL", "tvl": 50_000, "created_at": (T0 - 5 * 3600) * 1000,
                "token_x": {"address": "A", "symbol": "A", "market_cap": 1e6},
                "token_y": {"address": SOL_MINT, "symbol": "SOL", "price": 100},
                "pool_config": {"bin_step": 100}, "fees": {"1h": 200, "24h": 5000}, "volume": {"24h": 1e5},
                "current_price": 1e-5}
        pools = [snap]
        sent = []
        with mock.patch.object(cli, "scan_pools", lambda cfg: pools), \
             mock.patch.object(cli, "candles_1h", lambda p, hours=26: []), \
             mock.patch.object(cli, "append", lambda *a: None), \
             mock.patch.object(cli, "notify", sent.append), \
             mock.patch.object(cli.api, "open_portfolio", lambda w: []):
            state = {}
            self.assertEqual(len(cli.signal_once(cfg, prof, state, now=T0)), 1)   # first scan: one digest
            self.assertIn("watcher on", sent[-1])
            self.assertEqual(cli.signal_once(cfg, prof, state, now=T0 + 300), [])  # still matching: quiet
            pools[:] = []
            cli.signal_once(cfg, prof, state, now=T0 + 600)                       # drops out
            pools[:] = [snap]
            self.assertEqual(cli.signal_once(cfg, prof, state, now=T0 + 900), [])  # back, but within cooldown
            self.assertEqual(len(cli.signal_once(cfg, prof, {**state, "matching": [], "pinged": {}}, now=T0 + 900)), 1)
            self.assertIn("ENTRY SIGNAL", sent[-1])


if __name__ == "__main__":
    unittest.main()
