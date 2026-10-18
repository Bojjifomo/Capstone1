"""Offline tests: python3 -m unittest discover -s rh_wallet_finder/tests"""
import json
import math
import sys
import tempfile
import threading
import unittest
import urllib.request
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
        good = {"net": 500, "roi": 40, "win": 80, "daily": 90, "dd": -2, "recover": 3600, "fees": 50, "closed": 40}
        bad = {"net": -100, "roi": -10, "win": 20, "daily": 10, "dd": -40, "recover": 9e5, "fees": 1, "closed": 40}
        mid = {"net": 50, "roi": 5, "win": 55, "daily": 50, "dd": -10, "recover": 86400, "fees": 10, "closed": 3}
        s = M.score_all([good, bad, mid])
        self.assertGreater(s[0], s[2])
        self.assertLessEqual(s[1], 45)
        self.assertEqual(M.verdict(75), "Elite")
        self.assertEqual(M.verdict(10), "Avoid")

    def test_copyable_and_style(self):
        self.assertEqual(M.copyable({"duration": 60, "per_day": 5}), "✗")
        self.assertEqual(M.copyable({"duration": 7200, "per_day": 5}), "✓")
        self.assertEqual(M.copyable({"duration": 900, "per_day": 5}), "?")
        ps = [_pos(0, 600, 1, mcap=2e5), _pos(0, 700, 1, mcap=3e5), _pos(0, 90000, 1, mcap=5e7)]
        st, conf = M.style(ps, {})
        self.assertEqual(st, "Scalp·Degen")
        self.assertEqual(conf, 67)

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


class ServerTest(unittest.TestCase):
    def test_import_and_endpoints(self):
        import server
        with tempfile.TemporaryDirectory() as d:
            orig = config.DB_PATH
            config.DB_PATH = Path(d) / "w.db"
            try:
                con = db.connect()
                text = ("No\tWallet Address\tShort\n"
                        "1\t0x74b26ec2754dcbd27e43a2a527e38005358a772e\t0x74b2...772e\tROBINHOOD\t"
                        "https://app.lpagent.io/portfolio?address=0x74b2&chain=ROBINHOOD\tCactus\n"
                        "0xd21556896db6242f6e5823389623728da49118ae\n")
                self.assertEqual(server.import_text(con, text, "t")["added"], 2)
                labs = {r["addr"]: r["label"] for r in con.execute("SELECT * FROM labels")}
                self.assertEqual(labs["0x74b26ec2754dcbd27e43a2a527e38005358a772e"], "Cactus")
                self.assertIsNone(labs["0xd21556896db6242f6e5823389623728da49118ae"])
                httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                base = f"http://127.0.0.1:{httpd.server_address[1]}"
                st = json.load(urllib.request.urlopen(base + "/api/status"))
                self.assertIn("discovered", st)
                self.assertEqual(json.load(urllib.request.urlopen(base + "/api/wallets?view=raw"))["rows"], [])
                page = urllib.request.urlopen(base + "/").read().decode()
                self.assertIn(server.TOKEN, page)
                req = urllib.request.Request(base + "/api/label", data=b'{"addr":"0x' + b"a" * 40 + b'"}',
                                             headers={"x-token": "wrong"})
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(req)
                self.assertEqual(e.exception.code, 403)
                e.exception.close()
                httpd.shutdown()
                httpd.server_close()
                con.close()
            finally:
                config.DB_PATH = orig


if __name__ == "__main__":
    unittest.main()
