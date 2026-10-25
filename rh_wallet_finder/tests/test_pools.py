"""Offline tests for the pool finder: python3 -m unittest discover -s rh_wallet_finder/tests"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chain as C  # noqa: E402
import config  # noqa: E402
import pools as P  # noqa: E402

TOKEN = "0x" + "11" * 20
V4_STATIC = "0x" + "a1" * 32
V4_DYNAMIC = "0x" + "b2" * 32
V4_ELSEWHERE = "0x" + "c3" * 32
V3 = "0x" + "d4" * 20


def gt_pool(addr, name, base, quote, dex="uniswap-v4-robinhood", tvl="10000", v1="1000", v24="12000",
            mcap=None, fdv="900000"):
    return {"attributes": {"address": addr, "name": name, "reserve_in_usd": tvl, "market_cap_usd": mcap,
                           "fdv_usd": fdv, "volume_usd": {"h1": v1, "h6": "0", "h24": v24},
                           "transactions": {"h1": {"buys": 3, "sells": 2},
                                            "h24": {"buys": 40, "sells": 30, "buyers": 25, "sellers": 20}},
                           "price_change_percentage": {"h1": "1.5", "h24": "-3"},
                           "pool_created_at": "2026-07-02T20:51:08Z"},
            "relationships": {"base_token": {"data": {"id": f"robinhood_{base}"}},
                              "quote_token": {"data": {"id": f"robinhood_{quote}"}},
                              "dex": {"data": {"id": dex}}}}


TOKENS = {f"robinhood_{TOKEN}": {"address": TOKEN, "symbol": "MEME"},
          f"robinhood_{config.WETH}": {"address": config.WETH, "symbol": "WETH"},
          f"robinhood_{config.USDG}": {"address": config.USDG, "symbol": "USDG"}}


def agg_result(rets):
    """Multicall3 aggregate3 return data for [(ok, hex returnData or None)]."""
    heads, tails, off = [], [], 32 * len(rets)
    for ok, data in rets:
        b = (data or "0x")[2:]
        el = C.word(1 if ok else 0) + C.word(0x40) + C.word(len(b) // 2) + b + "0" * (-len(b) % 64)
        heads.append(C.word(off))
        tails.append(el)
        off += len(el) // 2
    return "0x" + C.word(0x20) + C.word(len(rets)) + "".join(heads) + "".join(tails)


def slot0(sqrtp, lp_fee):
    return "0x" + C.word(sqrtp) + C.word(0) + C.word(0) + C.word(lp_fee)


def pool_key(fee, c0=config.ZERO, c1=TOKEN):
    return "0x" + C.addr_word(c0) + C.addr_word(c1) + C.word(fee) + C.word(60) + C.addr_word(config.ZERO)


class FakeRPC:
    def __init__(self, agg, swaps):
        self.agg, self.swaps, self.log_calls = agg, swaps, 0

    def call(self, method, params):
        if method == "eth_call":
            return self.agg
        if method == "eth_blockNumber":
            return hex(100_000)
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(1_000_000 + int(params[0], 16) // 10)}  # 10 blocks a second
        if method == "eth_getLogs":
            self.log_calls += 1
            return self.swaps
        raise AssertionError(method)


class ParseTest(unittest.TestCase):
    def test_fee_from_name(self):
        self.assertEqual(P.name_fee_pct("PONS / USDG 0.3%"), 0.3)
        self.assertEqual(P.name_fee_pct("WETH / USDG 0.051%"), 0.051)
        self.assertIsNone(P.name_fee_pct("PONS / USDG"))
        self.assertEqual(P.pair_name("PONS / USDG 0.3%"), "PONS / USDG")
        self.assertEqual(P.pair_name("PONS / USDG"), "PONS / USDG")

    def test_asset_side_and_mcap(self):
        p = P.parse_pool(gt_pool(V3, "MEME / WETH 1%", TOKEN, config.WETH, mcap="700000"), TOKENS)
        self.assertEqual((p["asset"], p["asset_side"], p["mcap"], p["mcap_src"]), (TOKEN, "base", 700000, "mcap"))
        p = P.parse_pool(gt_pool(V3, "MEME / WETH 1%", TOKEN, config.WETH), TOKENS)
        self.assertEqual((p["mcap"], p["mcap_src"]), (900000, "fdv"))
        # base is the money side: the pool's FDV is USDG's, not the token's, so it isn't used
        p = P.parse_pool(gt_pool(V3, "USDG / MEME", config.USDG, TOKEN), TOKENS)
        self.assertEqual((p["asset"], p["asset_side"], p["mcap"]), (TOKEN, "quote", None))
        p = P.parse_pool(gt_pool(V3, "WETH / USDG 0.05%", config.WETH, config.USDG), TOKENS)
        self.assertIsNone(p["asset"])
        self.assertEqual((p["dex"], p["tx_24h"], p["traders_24h"]), ("uniswap-v4", 70, 45))
        self.assertEqual(p["created_ts"], 1783025468)


class ComputeTest(unittest.TestCase):
    def pool(self, **kw):
        p = P.parse_pool(gt_pool(V3, "MEME / WETH 1%", TOKEN, config.WETH), TOKENS)
        p.update(kw)
        return P.compute(p)

    def test_yields(self):
        p = self.pool(fee_pct=1.0)  # $1k vol 1h, $12k vol 24h, $10k TVL
        self.assertAlmostEqual(p["fees_1h"], 10)
        self.assertAlmostEqual(p["per1k_1h"], 1.0)
        self.assertAlmostEqual(p["per1k_24h"], 12.0)
        self.assertAlmostEqual(p["apr_24h"], 120 * 365 / 10_000 * 100)
        self.assertAlmostEqual(p["apr_1h"], 10 * 24 * 365 / 10_000 * 100)
        self.assertAlmostEqual(p["pace"], 2.0)
        self.assertAlmostEqual(p["blend"], (p["apr_1h"] * p["apr_24h"]) ** 0.5)

    def test_unknown_and_zero_fee(self):
        p = self.pool(fee_pct=None)
        self.assertIsNone(p["apr_24h"])
        self.assertIsNone(p["blend"])
        p = self.pool(fee_pct=0.0)
        self.assertEqual((p["apr_24h"], p["blend"]), (0, 0.0))

    def test_flags(self):
        p = self.pool(fee_pct=0.3, traders_24h=5, vol_24h=5_000_000, created_ts=time.time() - 600)
        self.assertEqual(p["flags"], ["few traders", "volume ≫ TVL", "new <24h"])

    def test_filters(self):
        p = self.pool(fee_pct=1.0)
        self.assertTrue(P.keep(p, 500_000, 5_000))
        self.assertFalse(P.keep(p, 1_000_000, 5_000))
        self.assertFalse(P.keep(p, 500_000, 20_000))
        self.assertFalse(P.keep(p, 500_000, 5_000, min_vol_1h=5_000))
        self.assertTrue(P.keep(p, 500_000, 5_000, quote="weth"))
        self.assertFalse(P.keep(p, 500_000, 5_000, quote="USDG"))
        p.update(asset=None, mcap=None)  # ETH / USDG: no token cap to check
        self.assertTrue(P.keep(p, 10**12, 5_000))


class OnchainFeeTest(unittest.TestCase):
    def test_v4_static_dynamic_elsewhere_and_v3(self):
        pools = [{"pool": V4_STATIC, "dex": "uniswap-v4"}, {"pool": V4_DYNAMIC, "dex": "uniswap-v4"},
                 {"pool": V4_ELSEWHERE, "dex": "uniswap-v4"}, {"pool": V3, "dex": "uniswap-v3"},
                 {"pool": "0x" + "e5" * 20, "dex": "aero-slipstream"}]
        agg = agg_result([(True, slot0(1 << 96, 3000)), (True, pool_key(3000)),
                          (True, slot0(1 << 96, 0)), (True, pool_key(P.DYNAMIC_FEE_FLAG)),
                          (True, slot0(0, 0)), (True, pool_key(0, config.ZERO, config.ZERO)),
                          (True, "0x" + C.word(500)), (True, "0x" + C.word(500))])
        swap = lambda fee: {"topics": [P.SWAP_V4, V4_DYNAMIC], "data": "0x" + "".join(C.word(v) for v in
                                                                                     (1, 2, 3, 4, 5, fee))}
        rpc = FakeRPC(agg, [swap(10000), swap(12000), swap(30000)])
        fees = P.onchain_fees(pools, rpc)
        self.assertEqual(fees[V4_STATIC], (3000, "chain"))
        self.assertEqual(fees[V4_DYNAMIC], (12000, "swaps"))
        self.assertEqual(fees[V3], (500, "chain"))
        self.assertNotIn(V4_ELSEWHERE, fees)  # not in this PoolManager: the name decides
        self.assertNotIn("0x" + "e5" * 20, fees)
        self.assertEqual(rpc.log_calls, 1)


class BestTest(unittest.TestCase):
    def test_ranking_filters_and_unknowns(self):
        rows = []
        for addr, fee, v1 in ((V3, 1.0, "100"), ("0x" + "e5" * 20, 0.3, "5000"), ("0x" + "f6" * 20, None, "1"),
                              ("0x" + "a7" * 20, 0.0, "999")):
            p = P.parse_pool(gt_pool(addr, f"MEME{addr[2:4]} / WETH", TOKEN, config.WETH, v1=v1), TOKENS)
            p["fee_pct"], p["fee_src"] = fee, "chain"
            rows.append(P.compute(p))
        res = {"at": time.time(), "pools": rows, "errors": []}
        with mock.patch.object(P, "cached", return_value=res):
            out = P.best({}, sort="1h")
            self.assertEqual([r["pool"] for r in out["rows"]], ["0x" + "e5" * 20, V3])
            self.assertEqual((out["passed_filters"], out["zero_fee"], len(out["fee_unknown"])), (4, 1, 1))
            out = P.best({}, sort="24h", min_vol_1h=200)
            self.assertEqual([r["pool"] for r in out["rows"]], ["0x" + "e5" * 20])
            self.assertEqual(P.best({}, min_mcap=10**9)["rows"], [])


if __name__ == "__main__":
    unittest.main()
