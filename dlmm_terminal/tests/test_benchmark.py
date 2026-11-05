import unittest
from unittest import mock

from dlmm_terminal import activity, benchmark, blast, cli
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
        self.assertIn("fees1hUsd $10 ∉ ≥ $20", m["misses"])

    def test_signal_pings_only_on_new_matches(self):
        cfg = cli.load_config(None)
        prof = self._prof()
        snap = {"address": "A", "name": "A-SOL", "tvl": 50_000, "created_at": (T0 - 5 * 3600) * 1000,
                "token_x": {"address": "A", "symbol": "A", "market_cap": 1e6},
                "token_y": {"address": SOL_MINT, "symbol": "SOL", "price": 100},
                "pool_config": {"bin_step": 100}, "fees": {"1h": 200, "24h": 5000}, "volume": {"24h": 1e5},
                "cumulative_metrics": {"fees": 10_000}, "current_price": 1e-5}
        pools = [snap]
        sent = []
        with mock.patch.object(cli, "scan_pools", lambda cfg: pools), \
             mock.patch.object(cli, "candles_1h", lambda p, hours=26: []), \
             mock.patch.object(cli, "append", lambda *a: None), \
             mock.patch.object(cli, "notify", sent.append), \
             mock.patch.object(cli.api, "open_portfolio", lambda w: []), \
             mock.patch.object(cli.api, "chunked", lambda *a, **k: []), \
             mock.patch.object(cli.blast, "token_stats", lambda mints, now: {"A": {"volume24hUsd": 1e5, "ageHours": 5}}):
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
        with mock.patch.object(cli, "blast_once", lambda cfg, prof, state, now, hits=None: sent.append(now) or state.update(lastBlast=now) or []), \
             mock.patch.object(cli, "scan_hits", lambda *a: []), \
             mock.patch.object(cli, "_wallet_entries", lambda *a: []):
            state = {}
            for minute in range(0, 65, 5):
                cli.tick(cfg, {}, state, now=T0 + minute * 60)
        self.assertEqual([(t - T0) // 60 for t in sent], [0, 30, 60])


class RevivalTests(unittest.TestCase):
    CFG = {"minDeadHours": 6, "minTop3Share": 0.7}

    def hours(self, fees):
        return [{"timestamp": i * 3600, "fees": f} for i, f in enumerate(fees)]

    def test_dead_then_alive_is_caught(self):  # DAVID-SOL shape: ~20h of nothing, then a burst
        f = activity.features(self.hours([0] * 18 + [447, 18, 3416, 1189, 36, 235]), age_h=107)
        self.assertEqual(f["deadStreak"], 18)
        self.assertGreater(f["top3Share"], 0.9)
        self.assertTrue(activity.revived(f, self.CFG))

    def test_steady_pool_passes(self):
        f = activity.features(self.hours([200, 150, 300, 250] * 6), age_h=72)
        self.assertEqual(f["deadStreak"], 0)
        self.assertFalse(activity.revived(f, self.CFG))

    def test_new_pool_is_not_a_revival(self):  # 4h old: all fees in few hours, but it was never dead
        f = activity.features(self.hours([0] * 20 + [500, 900, 700, 300]), age_h=4)
        self.assertEqual(f["hours"], 4)
        self.assertFalse(activity.revived(f, self.CFG))

    def test_gate_flag_off(self):
        snap = {"address": "A"}
        base = {"pool": "A", "symbol": "A", "poolAgeHours": 100, "flags": [], "gatesFailed": [], "decision": "propose"}
        dead = [{"timestamp": i, "fees": f} for i, f in enumerate([0] * 18 + [400, 0, 3000, 1000, 0, 200])]
        with mock.patch.object(cli.api, "chunked", lambda *a, **k: dead):
            for mode, keep in (("gate", False), ("flag", True), ("off", True)):
                c, ex = {**base, "flags": [], "gatesFailed": []}, []
                sig = {"revival": {**self.CFG, "mode": mode}}
                self.assertEqual(cli._revival_check(c, snap, sig, T0, ex), keep, mode)
                self.assertEqual(len(ex), 0 if keep else 1)
                if mode == "flag":
                    self.assertIn("dead→alive", c["flags"][0])


class GmgnFilterTests(unittest.TestCase):
    def test_candidate_dims_token_wide(self):
        snap = {"token_y": {"address": SOL_MINT, "price": 100}, "fees": {"1h": 5, "24h": 50},
                "cumulative_metrics": {"fees": 6000}, "volume": {"24h": 20_000}}
        d = benchmark.candidate_dims(snap, {"volume24hUsd": 80_000, "ageHours": 30}, pool_age_h=10)
        self.assertEqual(d["totalFeesSol"], 60)      # $6000 / $100
        self.assertEqual(d["volume24hUsd"], 80_000)  # token-wide beats the pool's 20k
        self.assertEqual(d["tokenAgeHours"], 30)
        d = benchmark.candidate_dims(snap, None, pool_age_h=10)  # DexScreener down: fall back to the pool
        self.assertEqual((d["volume24hUsd"], d["tokenAgeHours"]), (20_000, 10))

    def test_gmgn_bands(self):
        cfg = cli.load_config(None)["signal"]
        prof = {"binSteps": {"100": {"pnlSol": 1}}, "bands": {}}
        ok = {"binStep": 100, "tokenAgeHours": 20, "mcapUsd": 9e5, "volume24hUsd": 60_000, "totalFeesSol": 55}
        self.assertEqual(benchmark.match(ok, prof, cfg["bandOverrides"], cfg["matchDims"])["score"], 1)
        for k, v in (("tokenAgeHours", 0.5), ("tokenAgeHours", 300), ("mcapUsd", 3e5), ("mcapUsd", 1.1e7),
                     ("volume24hUsd", 40_000), ("totalFeesSol", 49)):
            self.assertLess(benchmark.match({**ok, k: v}, prof, cfg["bandOverrides"], cfg["matchDims"])["score"], 1, k)

    def test_token_stats_sums_all_pairs(self):
        pairs = [{"pairAddress": "a", "volume": {"h24": 1000}, "pairCreatedAt": (T0 - 10 * 3600) * 1000},
                 {"pairAddress": "b", "volume": {"h24": 500}, "pairCreatedAt": (T0 - 30 * 3600) * 1000},
                 {"pairAddress": "a", "volume": {"h24": 1000}, "pairCreatedAt": (T0 - 10 * 3600) * 1000}]

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *a): return __import__("json").dumps(pairs).encode()
        with mock.patch.object(blast.api, "urlopen", lambda *a, **k: R()):
            st = blast.token_stats(["M"], T0)
        self.assertEqual(st["M"], {"volume24hUsd": 1500, "ageHours": 30, "pairs": 2})

    def test_new_entry_alert(self):
        cfg = cli.load_config(None)
        prof = {"wallet": "W" * 44, "days": 20}
        def hit(mint, pool, apr=2000):
            c = {"tokenMint": mint, "pool": pool, "name": f"{mint}-SOL", "symbol": mint, "feeApr24h": apr, "feeApr1h": 1,
                 "tvlUsd": 5e4, "poolAgeHours": 20, "mcapUsd": 1e6, "binStep": 100, "fees1hUsd": 1, "fees24hUsd": 1,
                 "priceChg1h": 0, "priceChg6h": 0, "gatesFailed": [], "flags": [], "sizeVsTvlPct": 1,
                 "volume24hUsd": 6e4, "totalFeesSol": 60, "tokenAgeHours": 20,
                 "plan": {"strategy": "Spot", "side": "SOL-only", "sizeSol": 8, "rangeBottomPct": -70}}
            return c, {"hits": ["x"] * 5, "misses": [], "dims": 5, "score": 1}, {}
        sent = []
        with mock.patch.object(cli, "notify", sent.append), mock.patch.object(cli, "append", lambda *a: None):
            state = {}
            self.assertEqual(cli._new_entrants([hit("A", "p1")], cfg, prof, state, T0), [])          # first scan: record only
            out = cli._new_entrants([hit("A", "p1"), hit("B", "p2"), hit("B", "p3", 9000)], cfg, prof, state, T0 + 300)
            self.assertEqual([r["pool"] for r in out], ["p3"])     # B is new; one alert, on its best pool
            self.assertIn("NEW IN DETECTION", sent[-1])
            self.assertIn("+1 other pool", sent[-1])
            self.assertEqual(cli._new_entrants([hit("A", "p1"), hit("B", "p3")], cfg, prof, state, T0 + 600), [])
            cli._new_entrants([hit("A", "p1")], cfg, prof, state, T0 + 900)                         # B drops out
            self.assertEqual(cli._new_entrants([hit("A", "p1"), hit("B", "p3")], cfg, prof, state, T0 + 1200), [])  # cooldown
            cli._new_entrants([hit("A", "p1")], cfg, prof, state, T0 + 7 * 3600)
            self.assertEqual(len(cli._new_entrants([hit("A", "p1"), hit("B", "p3")], cfg, prof, state, T0 + 7 * 3600 + 300)), 1)


if __name__ == "__main__":
    unittest.main()
