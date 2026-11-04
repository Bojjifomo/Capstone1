import unittest
from unittest import mock

from dlmm_terminal import benchmark, blast, cli
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

    def test_owner_band_overrides(self):
        p = self._prof()  # benchmark band: mcap exactly 1M, fees24h 5000, fees1h 200
        ov = {"mcapUsd": {"lo": 300_000, "hi": 10_000_000}, "fees24hUsd": {"lo": 200, "hi": None},
              "fees1hUsd": {"lo": 20, "hi": None}}
        c = {"binStep": 100, "poolAgeHours": 5, "mcapUsd": 8e6, "fees24hUsd": 90_000, "fees1hUsd": 25}
        self.assertLess(benchmark.match(c, p)["score"], 1)          # outside the benchmark's own bands
        self.assertEqual(benchmark.match(c, p, ov)["score"], 1)     # inside the owner's bands
        m = benchmark.match({**c, "mcapUsd": 12e6, "fees1hUsd": 10}, p, ov)
        self.assertIn("mcapUsd $12.00M ∉ $300.0k–$10.00M", m["misses"])
        self.assertIn("fees1hUsd $10 ∉ > $20", m["misses"])

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


class BlastTests(unittest.TestCase):
    def _row(self, **kw):
        r = {"pool": "P" * 44, "name": "A&B-SOL", "symbol": "AB", "feeTvl24h": 10, "fdv": 5e5, "tvl": 3e4,
             "vol5m": 100, "chg5m": -1.0, "binStep": 100, "volTvl1h": 50, "prorated5m": 40, "score": 1, "dims": 5,
             "hits": 5, "boost": 0, "buys5m": 3, "sells5m": 2, "txn5m": 5, "ageH": 30, "fees1hUsd": 50,
             "flags": [], "dsUrl": "https://dexscreener.com/solana/x"}
        r.update(kw)
        return r

    def test_row_from_snapshot_and_dexscreener(self):
        c = {"pool": "P", "name": "M-SOL", "symbol": "M", "fees24hUsd": 3000, "fees1hUsd": 100, "binStep": 100,
             "mcapUsd": 1e6, "flags": []}
        snap = {"tvl": 10_000, "volume": {"1h": 20_000}, "created_at": (T0 - 72 * 3600) * 1000}
        r = blast.row(c, snap, {"score": 1, "dims": 5, "hits": ["x"] * 5},
                      {"vol5m": 500, "vol1h": 12_000, "chg5m": 2, "buys5m": 4, "sells5m": 6, "boost": 0, "fdv": 9e5}, T0)
        self.assertEqual(r["feeTvl24h"], 30)       # fees 24h / TVL
        self.assertEqual(r["volTvl1h"], 200)       # 1h vol / TVL
        self.assertEqual(r["prorated5m"], 50)      # 5m vol x 12 / 1h vol
        self.assertEqual(r["txn5m"], 10)
        self.assertEqual(blast.ago(r["ageH"]), "3 days ago")

    def test_top_n_and_html_escaping(self):
        rows = [self._row(volTvl1h=v) for v in range(15)]
        best = blast.top(rows, 10, "volTvl1h")
        self.assertEqual([r["volTvl1h"] for r in best], list(range(14, 4, -1)))
        cfg = cli.load_config(None)
        prof = {"wallet": "W" * 44, "days": 20}
        msg = blast.message(best, 15, cfg, prof, html_mode=True)
        self.assertIn("A&amp;B-SOL", msg)
        self.assertIn("Top 10 of 15", msg)
        self.assertTrue(all(len(c.encode("utf-16-le")) // 2 <= 4000 for c in cli._tg_chunks(msg * 2)))

    def test_blast_every_30_min_then_wallet_checks(self):
        cfg = cli.load_config(None)
        sent = []
        with mock.patch.object(cli, "blast_once", lambda cfg, prof, state, now: sent.append(now) or state.update(lastBlast=now) or []), \
             mock.patch.object(cli, "_wallet_entries", lambda *a: []):
            state = {}
            for minute in range(0, 65, 5):
                cli.tick(cfg, {}, state, now=T0 + minute * 60)
        self.assertEqual([(t - T0) // 60 for t in sent], [0, 30, 60])


if __name__ == "__main__":
    unittest.main()
