import io
import json
import os
import socket
import unittest
from unittest import mock

import surge_alert as sa

sa.NOTIFY_ENV = "/nonexistent"  # tes gak boleh baca notify.env (dan key) asli
os.environ.pop("GMGN_API_KEY", None)

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
        small = dict(pair(), fdv=300_000)
        self.assertTrue(any("mcap $300.0k < $400.0k" in f for f in sa.confirm(small, CFG)))
        self.assertTrue(any("mcap" in f for f in sa.confirm(dict(pair(), fdv=None), CFG)))
        self.assertEqual(sa.confirm(dict(pair(liq=6000), fdv=None, marketCap=450_000), CFG), [])
        for kw, word in [({"liq": 4000}, "liq"), ({"v1": 100}, "vol 1j"),
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
        prices = [1.0, 0.9, 0.7, 0.5, 0.4, 0.42, 0.5, 0.55, 0.58, 0.6]
        # pump=True (default): ping paling awal di +25%; dasar sama -> sinyal dip nanti gak dobel
        for pump, first in ((False, "Turun 60%"), (True, "lagi pump")):
            state, sent = sa.load_state("/nonexistent"), []
            for i, price in enumerate(prices):
                sa.process(state, TOKEN, pair(price), NOW + i * 600, CFG, sent.append, pump=pump)
            self.assertEqual(len(sent), 1, pump)
            self.assertIn(first, sent[0])

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

    def test_search_error_skips_token(self):
        err = sa.urllib.error.HTTPError("u", 400, "Bad Request", None, None)
        with mock.patch.object(sa, "get_json", side_effect=err), mock.patch.object(sa, "warn") as w:
            self.assertIsNone(sa.resolve("#", "robinhood"))
            self.assertIn("400", w.call_args[0][0])

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

    def test_pump_flag(self):
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


def zero_like(dead=(0.18, 0.24), peak=3.0, surge_vol=5000.0, now_price=1.2, dead_every=1, hours=480):
    """Candle 1 jam kayak chart ZERO: pump -> dump -> mati ~12 hari -> bangun 6 jam terakhir."""
    out = []
    for h in range(hours, -1, -1):
        if h > 400:
            p, v = 0.5, 500.0
        elif h > 379:
            p, v = peak, 20_000.0
        elif h >= 300:
            p, v = 0.2 + (h - 300) / 79 * (peak - 0.2), 3000.0
        elif h > 6:
            if h % dead_every:
                continue  # pool sepi: jam tanpa trade gak ada candle
            p, v = dead[0] + (dead[1] - dead[0]) * ((h * 7) % 10) / 9, 50.0
        else:
            p, v = now_price - h * 0.15, surge_vol
        out.append((NOW - h * 3600, p, v))
    return out


class RevivalTest(unittest.TestCase):
    def test_zero_like_chart(self):
        sig = sa.detect_revival(zero_like(), 1.2, NOW, CFG)
        self.assertEqual(sig.kind, "revival")
        self.assertEqual(sig.peak, 3.0)
        self.assertGreater(sig.drop, 90)
        self.assertGreater(sig.rebound, 400)
        self.assertAlmostEqual(sig.dead_h, 299)
        self.assertEqual(sig.low_ts, NOW - 6 * 3600)

    def test_sparse_dead_candles_are_filled(self):
        self.assertIsNotNone(sa.detect_revival(zero_like(dead_every=10), 1.2, NOW, CFG))

    def test_rejections(self):
        cases = [
            ({"dead": (0.1, 0.4)}, 1.2, "gak flat"),
            ({"peak": 0.3}, 1.2, "level mati"),
            ({"now_price": 0.3}, 0.3, "atap range mati"),
            ({"surge_vol": 100.0}, 1.2, "volume surge"),
            ({"hours": 20}, 1.2, "history"),
        ]
        for kw, price, word in cases:
            why = []
            self.assertIsNone(sa.detect_revival(zero_like(**kw), price, NOW, CFG, why), kw)
            self.assertIn(word, why[0], kw)

    def test_hourly_fill(self):
        filled = sa.hourly([(NOW - 3 * 3600, 1.0, 10.0), (NOW - 3600, 2.0, 5.0)], NOW)
        self.assertEqual(filled, [(NOW - 3 * 3600, 1.0, 10.0), (NOW - 2 * 3600, 1.0, 0.0),
                                  (NOW - 3600, 2.0, 5.0), (NOW, 2.0, 0.0)])

    def test_process_sends_revival_with_mcap(self):
        state, sent = sa.load_state("/nonexistent"), []
        p = pair(1.2, {"m5": 5, "h1": 30, "h6": 300, "h24": 400}, symbol="ZERO")
        sig = sa.process(state, TOKEN, p, NOW, CFG, sent.append, candles=zero_like())
        self.assertEqual(sig.kind, "revival")
        self.assertIn("ZERO bangkit dari mati", sent[0])
        self.assertIn("mati ~12.5 hari", sent[0])
        self.assertIn("(mcap $3.0M)", sent[0])  # fdv $1.2M x 3.0 / 1.2
        # surge lanjut tapi belum 1.5x: sinyal masih nyala, tapi gak dikirim lagi walau lewat cooldown
        later = NOW + 7200
        self.assertIsNotNone(sa.detect_revival(zero_like(now_price=1.3), 1.3, later, CFG))
        self.assertIsNone(sa.process(state, TOKEN, pair(1.3, p["priceChange"]), later, CFG,
                                     sent.append, candles=zero_like(now_price=1.3)))
        self.assertEqual(len(sent), 1)
        # udah 1.5x dari alert terakhir: kirim lagi (masih terbang)
        sa.process(state, TOKEN, pair(1.8, p["priceChange"]), later, CFG, sent.append,
                   candles=zero_like(now_price=1.8))
        self.assertEqual(len(sent), 2)


class GeckoTerminalTest(unittest.TestCase):
    def test_fetch_candles_falls_back_to_top_pool(self):
        calls = []

        def fake(path, params=None):
            calls.append(path)
            if "/pools/0xdexpool/" in path:
                raise sa.urllib.error.HTTPError(path, 404, "Not Found", None, None)
            if path.endswith(f"/tokens/{TOKEN}/pools"):
                return {"data": [{"attributes": {"address": "0xgtpool"}}]}
            return {"data": {"attributes": {"ohlcv_list": [
                [NOW, 1, 2, 0.5, 1.5, 900], [NOW - 3600, 1, 1, 1, 1.0, 100]]}}}
        p = dict(pair(), pairAddress="0xdexpool")
        cache = {}
        with mock.patch.object(sa, "gt_get", side_effect=fake):
            candles = sa.fetch_candles("robinhood", p, 30, cache)
        self.assertEqual(candles, [(NOW - 3600, 1.0, 100.0), (NOW, 1.5, 900.0)])
        self.assertEqual(cache, {TOKEN: "0xgtpool"})
        self.assertIn("/networks/robinhood/pools/0xgtpool/ohlcv/hour", calls)

    def test_fetch_candles_failure_returns_empty(self):
        with mock.patch.object(sa, "gt_get", side_effect=OSError("blocked")), \
                mock.patch.object(sa, "warn"):
            self.assertEqual(sa.fetch_candles("robinhood", dict(pair(), pairAddress="0x1"), 30, {}), [])

    def test_discover_all_active_pools_with_rough_filter(self):
        prof = [{"chainId": "robinhood", "tokenAddress": "0xAAA"}, {"chainId": "base", "tokenAddress": "0xB"}]

        def gt_pool(addr, mc=None, tvl=None):
            return {"attributes": {"market_cap_usd": mc, "fdv_usd": None, "reserve_in_usd": tvl},
                    "relationships": {"base_token": {"data": {"id": f"robinhood_{addr}"}}}}
        pages = {"trending_pools": [gt_pool("0xCCC", "900000", "40000"),
                                    gt_pool("0xSMALLCAP", "150000", "40000"),
                                    gt_pool("0xTHINLP", "900000", "1000")],
                 "new_pools": [gt_pool("0xNEW", "250000", "3000")],  # di atas setengah ambang: kepantau
                 "pools": [gt_pool("0xCCC", "900000", "40000"), gt_pool("0xNOINFO")]}
        calls = []

        def fake_gt(path, params=None):
            calls.append(path)
            return {"data": pages[path.rsplit("/", 1)[1]]}
        with mock.patch.object(sa, "get_json", side_effect=lambda p, q=None: prof if "profiles" in p else []), \
                mock.patch.object(sa, "gt_get", side_effect=fake_gt), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(sa, "_gt_top_cursor", 0):
            found = sa.discover(CFG)
            self.assertEqual(len(calls), 5)  # trending 5m/1j + pool baru + 2 halaman volume terbesar
            sa.discover(CFG)
        self.assertEqual(found, ["0xaaa", "0xccc", "0xnew", "0xnoinfo"])
        self.assertEqual(calls.count("/networks/robinhood/trending_pools"), 4)


class HistoryCompactionTest(unittest.TestCase):
    def test_old_points_thinned_recent_kept(self):
        state = sa.load_state("/nonexistent")
        for m in range(600, -1, -1):  # 10 jam, 1 titik/menit
            sa.update_history(state, TOKEN, pair(1.0), NOW - m * 60, CFG)
        hist = state["history"][TOKEN]
        recent = [t for t, _ in hist if NOW - t <= 3 * 3600]
        old = [t for t, _ in hist if NOW - t > 3 * 3600]
        self.assertEqual(len(recent), 181)
        self.assertLessEqual(len(old), 43)
        self.assertEqual(len(old), len({int(t // 600) for t in old}))


class NotifyTest(unittest.TestCase):
    def test_env_file(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("# komentar\nNTFY_TOPIC=rahasia-123\nTELEGRAM_CHAT_ID=\"42\"\nDISCORD_WEBHOOK_URL=\n")
        try:
            with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "dari-shell"}, clear=True):
                sa.load_env_file(f.name)
                self.assertEqual(os.environ["NTFY_TOPIC"], "rahasia-123")
                self.assertEqual(os.environ["TELEGRAM_CHAT_ID"], "dari-shell")
                self.assertEqual(os.environ["DISCORD_WEBHOOK_URL"], "")
                with mock.patch.object(sa.sys, "platform", "linux"):
                    self.assertEqual(sa.make_notifier().channels, ["ntfy"])
        finally:
            os.unlink(f.name)

    def test_mac_notification(self):
        import os
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(sa.sys, "platform", "darwin"), \
                mock.patch.object(sa.subprocess, "run") as run, mock.patch("builtins.print"):
            notify = sa.make_notifier()
            self.assertEqual(notify.channels, ["notif Mac"])
            notify('🚀 ZERO "lagi" pump — robinhood\nNaik +40%')
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "osascript")
        self.assertEqual(argv[-2:], ['🚀 ZERO "lagi" pump — robinhood', "Naik +40%"])

    def test_test_notify_flag(self):
        sent = []
        with mock.patch.object(sa, "make_notifier", return_value=mock.Mock(side_effect=sent.append, channels=[])), \
                mock.patch.object(sa, "load_env_file"), mock.patch("builtins.print"):
            sa.main(["--test-notify"])
        self.assertIn("Tes notif", sent[0])


class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.status, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, payload=None, headers=None):
    return sa.urllib.error.HTTPError("https://x", code, "err", headers or {},
                                     io.BytesIO(json.dumps(payload or {}).encode()))


class GmgnTest(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.dict(os.environ, {"GMGN_API_KEY": "k-123"}),
                   mock.patch.object(sa, "GMGN_MIN_GAP_S", 0), mock.patch.object(sa, "_gmgn_blocked_until", 0.0),
                   mock.patch.object(sa, "warn")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_get_sends_key_and_auth_params(self):
        resp = FakeResponse({"code": 0, "data": {"rank": []}})
        with mock.patch.object(sa.urllib.request, "urlopen", return_value=resp) as op:
            self.assertEqual(sa.gmgn_get("/v1/market/rank", {"chain": "robinhood", "filters": ["not_honeypot"]}),
                             {"rank": []})
        req = op.call_args[0][0]
        self.assertEqual(req.get_header("X-apikey"), "k-123")
        q = sa.urllib.parse.parse_qs(sa.urllib.parse.urlparse(req.full_url).query)
        self.assertEqual(q["chain"], ["robinhood"])
        self.assertEqual(q["filters"], ["not_honeypot"])
        self.assertTrue(q["timestamp"][0].isdigit())
        self.assertEqual(len(q["client_id"][0]), 36)

    def test_rate_limit_backs_off_until_reset(self):
        reset = NOW + 120
        err = http_error(429, {"code": 429, "error": "RATE_LIMIT_BANNED", "reset_at": reset})
        with mock.patch.object(sa.urllib.request, "urlopen", side_effect=err) as op, \
                mock.patch.object(sa.time, "time", return_value=NOW):
            with self.assertRaisesRegex(RuntimeError, "RATE_LIMIT_BANNED"):
                sa.gmgn_get("/v1/market/rank", {})
            with self.assertRaisesRegex(RuntimeError, "istirahat 120 detik"):
                sa.gmgn_get("/v1/market/rank", {})
        self.assertEqual(op.call_count, 1)

    def test_business_error_raises(self):
        resp = FakeResponse({"code": 40001, "error": "INVALID_API_KEY", "message": "bad key"})
        with mock.patch.object(sa.urllib.request, "urlopen", return_value=resp):
            with self.assertRaisesRegex(RuntimeError, "INVALID_API_KEY bad key"):
                sa.gmgn_get("/v1/market/rank", {})

    def test_discover_uses_server_side_filters(self):
        ranks = {"5m": [{"address": "0xAAA"}], "1h": [{"address": "0xaaa"}, {"address": "0xBBB"}],
                 "24h": [{"address": "0xCCC"}]}
        with mock.patch.object(sa, "gmgn_get", side_effect=lambda path, q: {"rank": ranks[q["interval"]]}) as g, \
                mock.patch.object(sa, "gt_get") as gt, mock.patch.object(sa, "get_json") as ds:
            self.assertEqual(sa.discover(CFG), ["0xaaa", "0xbbb", "0xccc"])
        gt.assert_not_called()
        ds.assert_not_called()
        params = g.call_args_list[2][0][1]
        self.assertEqual((params["min_marketcap"], params["min_liquidity"]), ("400000", "5000"))
        self.assertEqual((params["interval"], params["order_by"], params["limit"]), ("24h", "marketcap", 100))

    def test_discover_falls_back_to_geckoterminal(self):
        with mock.patch.object(sa, "gmgn_get", side_effect=RuntimeError("down")), \
                mock.patch.object(sa, "gt_get", return_value={"data": []}) as gt, \
                mock.patch.object(sa, "get_json", return_value=[]):
            self.assertEqual(sa.discover(CFG), [])
        self.assertTrue(gt.called)

    def test_candles_from_gmgn_kline(self):
        rows = [{"time": NOW, "close": "1.5", "amount": "900"},
                {"time": (NOW - 3600) * 1000, "close": "1.0", "amount": "100"},  # milidetik juga diterima
                {"time": NOW - 7200, "close": "0", "amount": "5"}]
        with mock.patch.object(sa, "gmgn_get", return_value={"list": rows}) as g, mock.patch.object(sa, "gt_get") as gt:
            candles = sa.fetch_candles("robinhood", pair(), 30, {})
        self.assertEqual(candles, [(NOW - 3600, 1.0, 100.0), (NOW, 1.5, 900.0)])
        self.assertEqual(g.call_args[0][1]["resolution"], "1h")
        gt.assert_not_called()

    def test_gmgn_resolves_ipv4_only(self):
        v4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))
        v6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
        with mock.patch.object(sa, "_orig_getaddrinfo", return_value=[v6, v4]):
            self.assertEqual(sa._getaddrinfo_ipv4_for_gmgn("openapi.gmgn.ai", 443), [v4])
            self.assertEqual(sa._getaddrinfo_ipv4_for_gmgn("api.dexscreener.com", 443), [v6, v4])


class GeckoTerminalPacingTest(unittest.TestCase):
    def test_429_pauses_instead_of_retrying(self):
        err = http_error(429, headers={"Retry-After": "30"})
        with mock.patch.object(sa, "_gt_blocked_until", 0.0), mock.patch.object(sa, "GT_MIN_GAP_S", 0), \
                mock.patch.object(sa.urllib.request, "urlopen", side_effect=err) as op:
            with self.assertRaises(sa.urllib.error.HTTPError):
                sa.gt_get("/networks/robinhood/pools")
            with self.assertRaisesRegex(RuntimeError, "istirahat"):
                sa.gt_get("/networks/robinhood/pools")
        self.assertEqual(op.call_count, 1)


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

    def test_zsh_inline_comment_ignored(self):
        with mock.patch.object(sa, "check") as check, mock.patch.object(sa, "run") as run:
            sa.main(["--check", "#", "cek", "status", "sekarang"])
            sa.main(["--scan", "#", "pantau", "terus", "+", "kirim", "alert"])
        self.assertEqual(check.call_args[0][0], sa.read_watchlist(sa.WATCHLIST))
        self.assertFalse(run.call_args[0][0].no_scan)

    def test_bundled_watchlist_has_zero(self):
        self.assertIn("0x316fa3AB9A8FD8d7567a823DedecF28d9FEE2894", sa.read_watchlist(sa.WATCHLIST))


if __name__ == "__main__":
    unittest.main()
