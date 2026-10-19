"""Offline tests: python3 -m unittest discover -s rh_wallet_finder/tests"""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chain as C  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import indexer  # noqa: E402
import metrics as M  # noqa: E402
import rpc as R  # noqa: E402
import scorer as S  # noqa: E402
from keccak import keccak256, selector, topic  # noqa: E402

PM = config.POSITION_MANAGER
ZERO = config.ZERO


class KeccakTest(unittest.TestCase):
    def test_known_vectors(self):
        self.assertEqual(keccak256(b"").hex(), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(selector("transfer(address,uint256)"), "a9059cbb")
        self.assertEqual(topic("Transfer(address,address,uint256)"),
                         "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")

    def test_selectors_match_constants(self):
        self.assertEqual(selector("getSlot0(bytes32)"), C.SEL_GET_SLOT0)
        self.assertEqual(selector("getPositionInfo(bytes32,address,int24,int24,bytes32)"), C.SEL_POSITION_INFO)
        self.assertEqual(selector("getFeeGrowthInside(bytes32,int24,int24)"), C.SEL_FEE_GROWTH_INSIDE)
        self.assertEqual(selector("aggregate3((address,bool,bytes)[])"), C.SEL_AGGREGATE3)

    def test_eth_usdg_pool_id(self):
        # PoolKey(ETH, USDG, 500, 10, no hooks) hashes to the reference pool in config
        enc = bytes.fromhex("0" * 64 + C.addr_word(config.USDG) + C.word(500) + C.word(10) + "0" * 64)
        self.assertEqual("0x" + keccak256(enc).hex(), config.ETH_USD_POOLS[0])


class MathTest(unittest.TestCase):
    def test_amounts_below_inside_above(self):
        L = 10 ** 18
        lo, hi = -600, 600
        below = int(C.sqrt_at_tick(-1000) * C.Q96)
        above = int(C.sqrt_at_tick(1000) * C.Q96)
        mid = int(C.sqrt_at_tick(0) * C.Q96)
        a0, a1 = C.amounts_for_liquidity(L, below, lo, hi)
        self.assertGreater(a0, 0)
        self.assertEqual(a1, 0)
        a0, a1 = C.amounts_for_liquidity(L, above, lo, hi)
        self.assertEqual(a0, 0)
        self.assertGreater(a1, 0)
        a0, a1 = C.amounts_for_liquidity(L, mid, lo, hi)
        self.assertAlmostEqual(a0, a1, delta=a0 * 1e-9)  # symmetric range at price 1
        # value is continuous at the range edge
        edge = int(C.sqrt_at_tick(hi) * C.Q96)
        self.assertAlmostEqual(C.amounts_for_liquidity(L, edge, lo, hi)[1],
                               C.amounts_for_liquidity(L, above, lo, hi)[1], delta=1e9)

    def test_fees_wrap(self):
        # fee growth is uint256 and wraps; the difference must still be right
        last = C.U256 - 5 * C.Q128
        now = 3 * C.Q128
        self.assertEqual(C.fees_owed(10, now, last), 80)

    def test_range_width(self):
        self.assertAlmostEqual(C.range_width_pct(0, 6932), 100, delta=0.1)  # 1.0001^6932 ≈ 2

    def test_usd_prices(self):
        # token0 = USDG (6 dec) quote, token1 = token (18 dec) at 0.005 USDG each
        p_raw = (1 / 0.005) * 10 ** 18 / 10 ** 6  # token1 raw per token0 raw
        sqrtp = int(math.sqrt(p_raw) * C.Q96)
        u0, u1 = S.usd_prices(sqrtp, 6, 18, 0, "usd", None)
        self.assertAlmostEqual(u0, 1.0)
        self.assertAlmostEqual(u1, 0.005, places=9)
        # token0 = ETH quote at $2000, token1 = token at 0.001 ETH
        sqrtp = int(math.sqrt(1000) * C.Q96)
        u0, u1 = S.usd_prices(sqrtp, 18, 18, 0, "eth", 2000.0)
        self.assertAlmostEqual(u0, 2000)
        self.assertAlmostEqual(u1, 2.0)
        self.assertIsNone(S.usd_prices(sqrtp, 18, 18, 0, "eth", None))
        # $2 a token is fine for 1B supply ($2B mcap), not for 100B; a drained pool's 1e50 never is
        self.assertIsNotNone(S.usd_prices(sqrtp, 18, 18, 0, "eth", 2000.0, supply=1e9))
        self.assertIsNone(S.usd_prices(sqrtp, 18, 18, 0, "eth", 2000.0, supply=1e11))
        drained = int(math.sqrt(1e-60) * C.Q96)  # almost no token1 per token0 left
        self.assertIsNone(S.usd_prices(drained, 6, 18, 0, "usd", None))

    def test_quote_side(self):
        self.assertEqual(S.quote_side(config.USDG, "0xabc"), (0, "usd"))
        self.assertEqual(S.quote_side(ZERO, config.USDG), (1, "usd"))  # USDG beats ETH
        self.assertEqual(S.quote_side(ZERO, "0xabc"), (0, "eth"))
        self.assertEqual(S.quote_side("0xabc", "0xdef"), (None, None))


class AbiTest(unittest.TestCase):
    def test_aggregate3_roundtrip(self):
        calls = [(config.STATE_VIEW, C.cd_slot0("0x" + "11" * 32)), (PM, "0x1234")]
        data = C.encode_aggregate3(calls)
        self.assertTrue(data.startswith("0x" + C.SEL_AGGREGATE3))
        # build a fake reply: [(true, 0xdeadbeef), (false, "")]
        def tup(ok, b):
            pad = b + "0" * (-len(b) % 64)
            return C.word(ok) + C.word(0x40) + C.word(len(b) // 2) + pad
        t0, t1 = tup(1, "deadbeef"), tup(0, "")
        ret = "0x" + C.word(0x20) + C.word(2) + C.word(0x40) + C.word(0x40 + len(t0) // 2) + t0 + t1
        self.assertEqual(C.decode_aggregate3(ret), ["0xdeadbeef", None])

    def test_decode_modify(self):
        log = {"topics": [C.MODIFY_LIQUIDITY, "0x" + "ab" * 32, "0x" + C.addr_word(PM)],
               "data": "0x" + C.word(-120) + C.word(240) + C.word(-5000) + C.word(77),
               "blockNumber": "0x10", "logIndex": "0x2", "transactionHash": "0xAA", "blockTimestamp": "0x64"}
        d = C.decode_modify(log)
        self.assertEqual((d["tl"], d["tu"], d["delta"], d["salt"], d["sender"], d["ts"]),
                         (-120, 240, -5000, 77, PM, 100))

    def test_decode_string(self):
        s = "VRAX".encode().hex()
        ret = "0x" + C.word(0x20) + C.word(4) + s + "0" * (64 - len(s))
        self.assertEqual(C.decode_string(ret), "VRAX")
        self.assertEqual(C.decode_string("0x" + s + "0" * (64 - len(s))), "VRAX")  # bytes32 symbol


class RpcTest(unittest.TestCase):
    def test_range_hint(self):
        msg = ("Under the Free tier plan, you can make eth_getLogs requests with up to a 10 block range. "
               "Based on your parameters, this block range should work: [0x574b1aa, 0x574b1b3].")
        self.assertEqual(R._range_hint(msg), 10)
        self.assertEqual(R._range_hint("block range too large, max 5000 blocks"), 5000)

    def test_cut_off_replies(self):
        import http.client
        import io
        from unittest import mock
        rpc = R.RPC("http://example.invalid", workers=1)
        rpc._backoff = lambda *a: None
        replies = [http.client.IncompleteRead(b"x" * 10), ConnectionResetError("reset"),
                   io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":"0x10"}')]

        def urlopen(*a, **k):
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(rpc.call("eth_blockNumber", []), "0x10")  # one cut, one reset: retried
            replies[:] = [http.client.IncompleteRead(b"x" * 10)] * 2
            with self.assertRaises(R.TruncatedError):  # cut twice: give up so the caller asks for less
                rpc.call("eth_getLogs", [{}])

        # get_logs answers a reply that keeps getting cut by asking for smaller ranges
        def fake_call(method, params):
            f = params[0]
            a, b = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            if b - a + 1 > 50:
                raise R.TruncatedError("cut off")
            return [{"n": n} for n in range(a, b + 1) if n % 10 == 0]
        rpc.call = fake_call
        logs, chunk = rpc.get_logs({}, 0, 999, 400)
        self.assertEqual([x["n"] for x in logs], list(range(0, 1000, 10)))
        self.assertLessEqual(chunk, 50)

    def test_get_logs_shrinks_and_resumes(self):
        rpc = R.RPC("http://example.invalid", workers=2)
        seen = []

        def fake_call(method, params):
            f = params[0]
            a, b = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            if b - a + 1 > 25:
                raise R.RangeError("block range too large", 25)
            seen.append((a, b))
            return [{"n": n} for n in range(a, b + 1) if n % 7 == 0]

        rpc.call = fake_call
        logs, chunk = rpc.get_logs({}, 0, 299, 100)
        self.assertEqual([x["n"] for x in logs], [n for n in range(300) if n % 7 == 0])
        self.assertLessEqual(chunk, 25)
        covered = sorted(seen)
        self.assertEqual(covered[0][0], 0)
        self.assertEqual(covered[-1][1], 299)
        for (a1, b1), (a2, _) in zip(covered, covered[1:]):
            self.assertEqual(b1 + 1, a2)  # no gaps, no overlaps


def _pos(open_ts, close_ts, pnl, dep=100.0, **kw):
    p = {"open_ts": open_ts, "close_ts": close_ts, "dep": dep, "wd": dep + pnl, "fee": 1.0, "now": 0.0,
         "pnl": pnl, "cost": 2.0, "eth_pair": 0, "single": 0, "tl": 0, "tu": 6932, "mcap": 5e5}
    p.update(kw)
    return p


class MetricsTest(unittest.TestCase):
    now = 1_800_000_000

    def test_basic(self):
        d = 86400
        ps = [_pos(self.now - 5 * d, self.now - 5 * d + 3600, 10),
              _pos(self.now - 4 * d, self.now - 4 * d + 3600, -30),
              _pos(self.now - 3 * d, self.now - 3 * d + 3600, 25),
              _pos(self.now - 2 * d, self.now - 2 * d + 3600, 5),
              _pos(self.now - 3600, None, 0, now=104.0)]
        m = M.wallet_metrics(ps, self.now, 30, self.now - 10 * d)
        self.assertAlmostEqual(m["net"], 10)
        self.assertEqual(m["closed"], 4)
        self.assertEqual(m["open"], 1)
        self.assertEqual(m["win"], 75)
        self.assertEqual(m["daily"], 75)
        self.assertAlmostEqual(m["duration"], 3600)
        # equity 10 -> -20 -> 5 -> 10: worst drop 30 from the first close, healed by the 4th
        self.assertAlmostEqual(m["dd"], -30 / m["capital"] * 100)
        self.assertFalse(m["recover_open"])
        self.assertAlmostEqual(m["recover"], 3 * d)
        real = M.wallet_metrics(ps, self.now, 30, self.now - 10 * d, real=True)
        self.assertAlmostEqual(real["net"], 10 - 4 * 2.0)
        self.assertAlmostEqual(m["range"], 100, delta=0.1)

    def test_unrecovered_and_window(self):
        d = 86400
        ps = [_pos(self.now - 40 * d, self.now - 39 * d, 50),  # outside the 30 d window
              _pos(self.now - 3 * d, self.now - 2 * d, 20),
              _pos(self.now - 2 * d, self.now - 1 * d, -50)]
        m = M.wallet_metrics(ps, self.now, 30, self.now - 50 * d)
        self.assertEqual(m["closed"], 2)
        self.assertTrue(m["recover_open"])
        self.assertAlmostEqual(m["recover"], 2 * d)
        self.assertIsNone(M.wallet_metrics([_pos(self.now - 9, None, 0)], self.now, 30, 0))

    def test_scores_and_verdicts(self):
        good = {"net": 500, "roi": 40, "pf": 3, "win": 80, "daily": 90, "dd": -2, "recover": 3600, "fees": 50,
                "closed": 40, "closed_trades": 40}
        bad = {"net": -100, "roi": -10, "pf": 0.5, "win": 20, "daily": 10, "dd": -40, "recover": 9e5, "fees": 1,
               "closed": 40, "closed_trades": 40}
        mid = {"net": 50, "roi": 5, "pf": 1.2, "win": 55, "daily": 50, "dd": -10, "recover": 86400, "fees": 10,
               "closed": 3, "closed_trades": 3}
        s = M.score_all([good, bad, mid])
        self.assertGreater(s[0], s[2])
        self.assertLessEqual(s[1], 45)
        self.assertEqual(M.verdict(75), "Elite")
        self.assertEqual(M.verdict(10), "Avoid")
        # multiday players are judged on total PnL: a realized loss with a bigger open gain isn't capped
        a = {**mid, "net": -20, "total": 300, "closed_trades": 20}
        b = {**mid, "net": 40, "total": -100, "closed_trades": 20}
        sa, sb = M.score_all([a, b], "multiday")
        self.assertGreater(sa, sb)
        self.assertLessEqual(sb, 45)

    def test_copyable_and_style(self):
        self.assertEqual(M.copyable({"duration": 60, "per_day": 5}), "✗")
        self.assertEqual(M.copyable({"duration": 7200, "per_day": 5}), "✓")
        self.assertEqual(M.copyable({"duration": 900, "per_day": 5}), "?")
        legs = [_pos(0, 600, 1, pool="p", open_block=7, side="bid") for _ in range(5)]
        single = [_pos(0, 600, 1, pool="p", open_block=9, side="both")]
        st, conf = M.style(M.trades(legs + single))
        self.assertEqual((st, conf), ("Ladder ×5 · bid", 50))

    def test_ladder_is_one_trade(self):
        d = 86400
        # one 5-range ladder: two legs filled and earned, three were never reached (pnl 0)
        ladder = [_pos(self.now - 2 * d, self.now - d, x, dep=200, pool="p", open_block=10, side="bid")
                  for x in (12, 8, 0, 0, 0)]
        # an untouched ladder: flat, neither a win nor a loss
        flat = [_pos(self.now - d, self.now - d + 600, 0, dep=200, pool="p", open_block=20, side="bid")
                for _ in range(5)]
        loser = [_pos(self.now - 3 * d, self.now - 3 * d + 7200, -5, dep=1000, pool="q", open_block=5, side="both")]
        m = M.wallet_metrics(ladder + flat + loser, self.now, 30, self.now - 10 * d)
        self.assertEqual((m["trades"], m["closed_trades"], m["pos"]), (3, 3, 11))
        self.assertEqual(m["win"], 50)  # 1 win, 1 loss, 1 flat
        self.assertAlmostEqual(m["flat"], 100 / 3)
        self.assertAlmostEqual(m["pf"], 20 / 5)
        self.assertEqual(m["size"], 1000)  # per trade, not per range
        self.assertEqual(m["legs"], 5)
        self.assertAlmostEqual(m["net"], 15)  # PnL still adds every position

    def test_player_kind(self):
        h = 3600
        self.assertEqual(M.player_kind([20 * 60, 30 * 60, 3 * h], [])[0], "degen")
        self.assertEqual(M.player_kind([5 * h, 30 * h, 40 * 60], [])[0], "multiday")
        # long-open trades count once they're past the line; young ones don't
        self.assertEqual(M.player_kind([30 * 60], [26 * h, 50 * h])[0], "multiday")
        self.assertEqual(M.player_kind([30 * 60], [10 * 60, 20 * 60])[0], "degen")
        kind, fit = M.player_kind([5 * h, 6 * h, 7 * h, 60], [])
        self.assertEqual((kind, fit), ("multiday", 75))
        d = 86400
        quick = [_pos(self.now - d + i * 60, self.now - d + i * 60 + 30, 0, pool="p", open_block=i)
                 for i in range(200)]
        self.assertTrue(M.prescreen(quick, self.now)["bot"])
        slow = [_pos(self.now - 3 * d, self.now - 2 * d, 0, pool="p", open_block=1),
                _pos(self.now - 2 * d, self.now - d, 0, pool="p", open_block=2)]
        x = M.prescreen(slow, self.now)
        self.assertEqual((x["kind"], x["bot"]), ("multiday", False))

    def test_roi_over_active_days(self):
        d = 86400
        # a wallet that started 3 days ago: $30 on $1000 kept deployed = 1%/day ~ 30%/month
        ps = [_pos(self.now - 3 * d + i * d, self.now - 2 * d + i * d, 10, dep=1000, pool="p", open_block=i)
              for i in range(3)]
        m = M.wallet_metrics(ps, self.now, 30, self.now - 60 * d)
        self.assertAlmostEqual(m["span_days"], 3)
        self.assertAlmostEqual(m["roi"], 30, delta=0.5)

    def test_similarity(self):
        ms = [{"duration": 600, "range": 50, "single": 100, "eth": 0, "mcap": 1e5, "size": 100, "per_day": 50,
               "concur": 3, "win": 60},
              {"duration": 650, "range": 55, "single": 100, "eth": 0, "mcap": 1.2e5, "size": 110, "per_day": 45,
               "concur": 3, "win": 62},
              {"duration": 9e5, "range": 2000, "single": 0, "eth": 100, "mcap": 1e9, "size": 1e5, "per_day": 0.2,
               "concur": 1, "win": 90}]
        f = M.feature_matrix(ms)
        self.assertGreater(M.similarity(f[0], f[1]), M.similarity(f[0], f[2]))
        self.assertEqual(M.similarity(f[0], f[0]), 100)


class IndexerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def ev(self, block, li, tid, delta, ts=None):
        self.con.execute("INSERT INTO events(block, log_index, tx, ts, token_id, pool, tl, tu, delta) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (block, li, "0x", ts or block, tid, "0xp", -60, 60, str(delta)))

    def tr(self, block, li, tid, a, b):
        self.con.execute("INSERT INTO transfers VALUES(?,?,?,?,?,?)", (block, li, block, tid, a, b))

    def test_lifecycle_loaded_out_of_order(self):
        w = "0x" + "aa" * 20
        # newest segment first: burn arrives before the mint
        self.tr(200, 1, 7, w, ZERO)
        self.ev(200, 0, 7, -1000)
        indexer.rebuild_positions(self.con, {7})
        p = dict(self.con.execute("SELECT * FROM positions").fetchone())
        self.assertEqual((p["minted"], p["close_block"], p["owner"]), (0, 200, w))
        self.tr(100, 1, 7, ZERO, w)
        self.ev(100, 0, 7, 1000)
        self.ev(150, 0, 7, 0)  # fee claim
        indexer.rebuild_positions(self.con, {7})
        p = dict(self.con.execute("SELECT * FROM positions").fetchone())
        self.assertEqual((p["minted"], p["open_block"], p["close_block"], p["liq"], p["n_events"]),
                         (1, 100, 200, "0", 3))

    def test_closed_without_burn_and_transfer(self):
        a, b = "0x" + "aa" * 20, "0x" + "bb" * 20
        self.tr(10, 1, 9, ZERO, a)
        self.ev(10, 0, 9, 500)
        self.tr(20, 0, 9, a, b)  # sold the NFT
        self.ev(30, 0, 9, -500)
        indexer.rebuild_positions(self.con, {9})
        p = dict(self.con.execute("SELECT * FROM positions").fetchone())
        self.assertEqual((p["owner"], p["close_block"]), (b, 30))
        indexer.refresh_wallets(self.con)
        self.assertEqual(self.con.execute("SELECT n_closed FROM wallets WHERE addr=?", (b,)).fetchone()[0], 1)


class ScorerTest(unittest.TestCase):
    def test_idle_wallets_left_out_and_absurd_returns_dropped(self):
        import time as _t
        now = _t.time()
        active, idle = "0x" + "a" * 40, "0x" + "b" * 40
        with tempfile.TemporaryDirectory() as d:
            con = db.connect(Path(d) / "w.db")
            tid = 0
            for owner, last in ((active, now - 3 * 86400), (idle, now - 20 * 86400)):
                con.execute("INSERT INTO wallets(addr, last_seen) VALUES(?,?)", (owner, last))
                for k in range(3):
                    tid += 1
                    con.execute("""INSERT INTO positions(token_id, owner, pool, open_block, open_ts, close_ts, minted,
                                   valued, dep_usd, wd_usd, fee_usd, now_usd, pnl_usd)
                                   VALUES(?,?,'p',?,?,?,1,1,100,100,1,0,1)""",
                                (tid, owner, tid, last - 7200 - k, last - k))
            # a drained pool's leftovers valued at a crazy price: $10 in, $5M out
            con.execute("UPDATE positions SET wd_usd=5e6, pnl_usd=5e6-10, dep_usd=10 WHERE token_id=1")
            con.commit()
            sc = S.Scorer(con, None, dict(config.DEFAULTS), log=lambda *a: None)
            people, bots = sc.candidates(now, now - 30 * 86400)
            self.assertEqual((people, bots), ([active], []))  # idle for 20 days > ACTIVE_DAYS (14)
            sc.cfg["ACTIVE_DAYS"] = "0"
            self.assertEqual(sorted(sc.candidates(now, now - 30 * 86400)[0]), [active, idle])
            sc.drop_spiked_prices()
            self.assertEqual(con.execute("SELECT valued FROM positions WHERE token_id=1").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT valued FROM positions WHERE token_id=2").fetchone()[0], 1)
            con.close()


class LauncherTest(unittest.TestCase):
    def test_old_instance_on_the_port_is_replaced(self):
        import shutil
        import socket
        import subprocess
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import finder
        if not shutil.which("lsof"):
            self.skipTest("no lsof")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        # an "old" Wallet Finder still holding the port after a git pull
        old = subprocess.Popen([sys.executable, "-c", "import socket, sys, time; s = socket.socket(); "
                                f"s.bind(('127.0.0.1', {port})); s.listen(); time.sleep(60)", "streamlit_app.py"],
                               cwd=Path(finder.__file__).resolve().parent)
        try:
            for _ in range(50):
                if not finder._port_free(port):
                    break
                import time as _t
                _t.sleep(0.1)
            self.assertEqual(finder._free_port(port), port)
            self.assertIsNotNone(old.wait(timeout=10))  # stopped
        finally:
            old.kill()
        # something else holding it (here: this test, not run from that folder): the next free port instead
        with socket.socket() as other:
            other.bind(("127.0.0.1", port))
            other.listen()
            self.assertGreater(finder._free_port(port), port)


class ConfigTest(unittest.TestCase):
    def test_borrows_smart_lp_finder_rpcs(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "RH Smart LP Finder"
            (proj / "lp-finder").mkdir(parents=True)
            (proj / "lp-finder" / "route.ts").write_text("")
            (proj / ".env").write_text("RH_RPC_MAIN=https://root-main\nRH_RPC_BACKUP1=https://root-backup\n")
            (proj / "lp-finder" / ".env").write_text("RH_RPC_BACKUP1_LP_FINDER=https://solid\n")
            orig_env, orig_file = os.environ.get("SMART_LP_FINDER_DIR"), config.ENV_FILE
            os.environ["SMART_LP_FINDER_DIR"] = str(proj)
            config.ENV_FILE = Path(d) / "none.env"
            try:
                cfg = config.load_env()
                self.assertEqual(cfg["RPC_URL"], "https://solid")         # lp-finder/.env wins
                self.assertEqual(cfg["CALL_RPC_URL"], "https://root-main")  # falls back to RH_RPC_MAIN
                self.assertEqual(cfg["RPC_SOURCE"], "RH Smart LP Finder/.env")
                config.ENV_FILE.write_text("RPC_URL=https://mine\n")
                cfg = config.load_env()
                self.assertEqual((cfg["RPC_URL"], cfg["CALL_RPC_URL"], cfg["RPC_SOURCE"]),
                                 ("https://mine", "", "settings"))  # our own setting: borrow nothing
            finally:
                config.ENV_FILE = orig_file
                if orig_env is None:
                    os.environ.pop("SMART_LP_FINDER_DIR", None)
                else:
                    os.environ["SMART_LP_FINDER_DIR"] = orig_env


class SmartLpTest(unittest.TestCase):
    def test_imports_saved_wallets_and_rankings_from_a_live_wal_db(self):
        import json
        import smartlp
        with tempfile.TemporaryDirectory() as d:
            theirs = Path(d) / "lp-finder.sqlite"
            src = __import__("sqlite3").connect(str(theirs))
            src.execute("PRAGMA journal_mode=WAL")
            src.executescript("""
                CREATE TABLE pool_lp_finder_cache (pool_id TEXT, from_block INTEGER, to_block INTEGER,
                  result_json TEXT, computed_at_ms INTEGER, PRIMARY KEY (pool_id, from_block, to_block));
                CREATE TABLE smart_lp_wallets (wallet_address TEXT PRIMARY KEY, label TEXT, saved_at_ms INTEGER);""")
            a, b = "0x" + "aa" * 20, "0x" + "bb" * 20
            res = {"totalWalletsFound": 40, "wallets": [
                {"walletAddress": b, "pnlUsd": -5, "totalDepositedUsd": 100, "positionCount": 1},
                {"walletAddress": a, "pnlUsd": 90, "pnlPercent": 30, "totalDepositedUsd": 300, "positionCount": 4}]}
            src.execute("INSERT INTO pool_lp_finder_cache VALUES(?,?,?,?,?)",
                        ("0x" + "cd" * 32, 10, 20, json.dumps(res), 1_790_000_000_000))
            src.execute("INSERT INTO smart_lp_wallets VALUES(?,?,?)", (a.upper().replace("0X", "0x"), "Cactus", 1))
            src.commit()  # still only in the -wal file: the connection stays open
            con = db.connect(Path(d) / "ours.db")
            con.execute("INSERT INTO labels(addr, label, starred, source, added) VALUES(?, 'mine', 0, 'manual', 1)", (a,))
            got = smartlp.import_all(con, theirs, log=lambda *_: None)
            self.assertEqual(got, {"saved": 1, "ranked": 2})
            lab = dict(con.execute("SELECT label, starred FROM labels WHERE addr=?", (a,)).fetchone())
            self.assertEqual(lab, {"label": "mine", "starred": 1})  # our label kept, star added
            best = smartlp.best_ranks(con)
            self.assertEqual((best[a]["rank"], best[a]["total"], best[a]["pnl_usd"]), (1, 40, 90))
            self.assertNotIn(b, best)  # losing placings don't count
            self.assertEqual(smartlp.import_all(con, theirs, log=lambda *_: None)["ranked"], 2)  # idempotent
            self.assertEqual(con.execute("SELECT COUNT(*) FROM slf_ranks").fetchone()[0], 2)
            src.close()
            con.close()


class QueriesTest(unittest.TestCase):
    def test_import_labels_presets(self):
        import queries
        with tempfile.TemporaryDirectory() as d:
            con = db.connect(Path(d) / "w.db")
            text = ("No\tWallet Address\tShort\n"
                    "1\t0x74b26ec2754dcbd27e43a2a527e38005358a772e\t0x74b2...772e\tROBINHOOD\t"
                    "https://app.lpagent.io/portfolio?address=0x74b2&chain=ROBINHOOD\tCactus\n"
                    "0xd21556896db6242f6e5823389623728da49118ae\n")
            self.assertEqual(queries.import_text(con, text, "t")["added"], 2)
            labs = {r["addr"]: r["label"] for r in con.execute("SELECT * FROM labels")}
            self.assertEqual(labs["0x74b26ec2754dcbd27e43a2a527e38005358a772e"], "Cactus")
            self.assertIsNone(labs["0xd21556896db6242f6e5823389623728da49118ae"])
            a = "0xd21556896db6242f6e5823389623728da49118ae"
            queries.set_label(con, a, notes="ladder bot", starred=False)
            row = dict(con.execute("SELECT label, notes, starred FROM labels WHERE addr=?", (a,)).fetchone())
            self.assertEqual(row, {"label": None, "notes": "ladder bot", "starred": 0})
            queries.set_label(con, a, label="LB")  # starred=None keeps the star as it is
            self.assertEqual(con.execute("SELECT label, starred FROM labels WHERE addr=?", (a,)).fetchone()[:], ("LB", 0))
            with self.assertRaises(ValueError):
                queries.set_label(con, "0x123")
            queries.save_presets(con, [{"name": "mine", "filters": {"f_win": 60}}])
            self.assertEqual(queries.presets(con)[0]["filters"]["f_win"], 60)
            self.assertEqual(queries.wallet_rows(con, "raw")[0], [])
            # a new database starts with the two reference wallets
            refs = dict(con.execute("SELECT addr, ref FROM labels WHERE ref IS NOT NULL").fetchall())
            self.assertEqual(refs, {k: v[0] for k, v in config.REFERENCE_WALLETS.items()})
            queries.set_label(con, a, ref="degen")
            self.assertEqual(con.execute("SELECT ref FROM labels WHERE addr=?", (a,)).fetchone()[0], "degen")
            queries.set_label(con, a, notes="x")  # ref=None keeps it
            self.assertEqual(con.execute("SELECT ref FROM labels WHERE addr=?", (a,)).fetchone()[0], "degen")
            with self.assertRaises(ValueError):
                queries.set_label(con, a, ref="whale")
            # boards: one class each, ranked on its own; similarity to that class's reference
            base = {"score": 50, "net": 1, "float": 0, "roi": 1, "win": 50, "duration": 600, "size": 100,
                    "per_day": 5, "open_now": 0}
            for addr, kind, score in (("0x" + "1" * 40, "degen", 70), ("0x" + "2" * 40, "multiday", 60),
                                      ("0x" + "3" * 40, "degen", 40), (a, "degen", 55)):
                con.execute("INSERT INTO wallets(addr, data, score) VALUES(?,?,?)",
                            (addr, json.dumps({"raw": {**base, "kind": kind, "score": score}}), score))
            con.commit()
            degen, _ = queries.wallet_rows(con, "raw", "degen")
            self.assertEqual([(r["rank"], r["score"]) for r in degen], [(1, 70), (2, 55), (3, 40)])
            self.assertTrue(degen[0]["sim_ref"])
            self.assertEqual(degen[0]["sim_label"], "LB")  # the degen reference, by its label
            self.assertEqual([r["kind"] for r in queries.wallet_rows(con, "raw", "multiday")[0]], ["multiday"])
            self.assertEqual(queries.status(con)["kinds"], {"multiday": 1, "degen": 3})
            queries.set_label(con, a, ref="")
            self.assertIsNone(con.execute("SELECT ref FROM labels WHERE addr=?", (a,)).fetchone()[0])
            self.assertIn("error", queries.similar(con, "0x" + "9" * 40, "raw"))
            queries.delete_label(con, a)
            self.assertIsNone(con.execute("SELECT 1 FROM labels WHERE addr=?", (a,)).fetchone())
            self.assertIn("discovered", queries.status(con))
            con.close()


class AppTest(unittest.TestCase):
    def test_app_renders(self):
        try:
            from streamlit.testing.v1 import AppTest as StAppTest
        except ImportError:
            self.skipTest("streamlit not installed")
        import os
        with tempfile.TemporaryDirectory() as d:
            old = {k: os.environ.get(k) for k in ("RHWF_DB", "RHWF_NO_WORKER", "SMART_LP_FINDER_DIR")}
            os.environ.update(RHWF_DB=str(Path(d) / "w.db"), RHWF_NO_WORKER="1", SMART_LP_FINDER_DIR=d)
            orig = config.DB_PATH
            config.DB_PATH = Path(d) / "w.db"
            try:
                at = StAppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"),
                                         default_timeout=60).run()
                self.assertFalse(at.exception, at.exception)
                self.assertEqual([t.label for t in at.tabs],
                                 ["Multiday", "Degen", "Last 24h", "Twin finder", "Watchlist", "Settings"])
                self.assertTrue(any("WALLET FINDER" in m.value for m in at.markdown))
            finally:
                config.DB_PATH = orig
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
