"""Offline tests: no network. python3 -m unittest elysium_terminal.tests.test_elysium_terminal"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from elysium_terminal import activity, chain, filters, portfolio, settings
from elysium_terminal import cli

CFG = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text())
LIB = (Path(__file__).resolve().parents[1] / "executor" / "lib.mjs").read_text()


def row(**kw):
    r = {"lpFeePct": 1.0, "ageHours": 30, "tvlUsd": 50_000, "mcapUsd": 2_000_000, "feePctDay": {"1h": 10, "24h": 8},
         "chg": {"1h": 2, "6h": -3}, "txns": {"h1": {"buys": 10, "sells": 10}, "h24": {"buys": 100, "sells": 100}},
         "vol": {"1h": 1000, "24h": 24000}, "hooks": chain.ZERO}
    r.update(kw)
    return r


class Selectors(unittest.TestCase):
    def test_known_selectors(self):
        self.assertEqual(chain.SEL["tryAggregate"], "bce38bd7")
        self.assertEqual(chain.SEL["getSlot0"], "c815641c")
        self.assertEqual(chain.SEL["getFeeGrowthInside"], "53e9c1fb")
        self.assertEqual(chain.SEL["getPositionInfo"], "dacf1d2f")
        self.assertEqual(chain.SEL["poolKeys"], "86b6be7d")
        self.assertEqual(chain.SEL["collect"], "fc6f7865")
        self.assertEqual(chain.SEL["positions"], "99fbab88")
        self.assertEqual(chain.SEL["slot0"], "3850c7bd")
        self.assertEqual(chain.SEL["getPool"], "1698ee82")
        self.assertEqual(chain.TOPIC["v4Swap"], "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f")
        self.assertEqual(chain.TOPIC["v3Swap"], "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67")
        self.assertEqual(chain.TOPIC["v3PoolCreated"], "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118")
        self.assertEqual(chain.TOPIC["v4Init"], "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438")

    def test_executor_addresses_match(self):
        low = LIB.lower()
        for addr in (chain.USDC, chain.WHYPE, chain.V4_POOL_MANAGER, chain.V4_POSITION_MANAGER, chain.V4_STATE_VIEW,
                     chain.V4_UNIVERSAL_ROUTER, chain.ES_V3_FACTORY, chain.ES_V3_NPM, chain.ES_V3_ROUTER,
                     chain.UNI_V3_FACTORY, chain.UNI_V3_NPM, chain.PERMIT2):
            self.assertIn(addr[2:], low)


class Fees(unittest.TestCase):
    def test_v4_protocol_cut(self):
        self.assertEqual(activity.lp_fee_v4(125, 25 | (25 << 12), True), 100)
        self.assertAlmostEqual(activity.lp_fee_v4(4698, 700 | (700 << 12), False), 4000, delta=1)
        self.assertEqual(activity.lp_fee_v4(10000, 0, True), 10000)

    def test_v3_protocol_divisor(self):
        self.assertEqual(activity.lp_fee_v3(10000, 0, True), 10000)
        # feeProtocol 4 on token0 means the protocol takes 1/4
        self.assertEqual(activity.lp_fee_v3(10000, 4, True), 7500)
        self.assertEqual(activity.lp_fee_v3(10000, 4 << 4, False), 7500)
        self.assertEqual(activity.lp_fee_v3(10000, 4, False), 10000)


class ChainMath(unittest.TestCase):
    def test_quote_side(self):
        tok = "0x" + "ab" * 20
        self.assertEqual(chain.quote_side({"currency0": chain.ZERO, "currency1": tok}), 0)
        self.assertEqual(chain.quote_side({"currency0": tok, "currency1": chain.USDC}), 1)
        self.assertEqual(chain.quote_side({"currency0": chain.ZERO, "currency1": chain.USDC}), 1)
        self.assertEqual(chain.quote_side({"currency0": chain.WHYPE, "currency1": tok}), 0)
        self.assertIsNone(chain.quote_side({"currency0": tok, "currency1": "0x" + "cd" * 20}))

    def test_price_orientation(self):
        raw = 75 * 10 ** 6 / 10 ** 18
        sqrt = int(raw ** 0.5 * 2 ** 96)
        self.assertAlmostEqual(chain.price_of(sqrt, 18, 6), 75, delta=0.01)

    def test_v3_create2_matches_live_pool(self):
        # USDC/WHYPE 1% on ElysiumSwap. getPool returned this address on 9 Nov 2026.
        pool = chain.v3_pool_address(chain.ES_V3_FACTORY, chain.USDC, chain.WHYPE, 10000)
        self.assertEqual(pool, chain.REFERENCE_HYPE_POOL)

    def test_v4_pool_id(self):
        pid = chain.pool_id(chain.ZERO, "0x" + "ab" * 20, 10000, 200, chain.ZERO)
        self.assertEqual(len(pid), 66)
        self.assertTrue(pid.startswith("0x"))

    def test_decode_slot0(self):
        w = lambda v: format(v % (1 << 256), "064x")
        s0 = chain.decode_slot0_v4("0x" + w(1 << 96) + w(-100) + w(25) + w(3000))
        self.assertEqual((s0["tick"], s0["lpFee"]), (-100, 3000))
        v3 = "0x" + w(1 << 96) + w(200) + w(0) + w(1) + w(1) + w(4) + w(1)
        d = chain.decode_slot0_v3(v3)
        self.assertEqual((d["tick"], d["feeProtocol"]), (200, 4))


class Ladder(unittest.TestCase):
    def test_full_range_deposit_is_unknown(self):
        # A full-range NFT is not a quote-only ladder. The formula would print a nonsense PnL.
        self.assertIsNone(portfolio.ladder_deposit_usd(10 ** 18, -887200, 887200, 1, 6, 1.0))
        narrow = portfolio.ladder_deposit_usd(10 ** 12, -302000, -300000, 1, 6, 1.0)
        self.assertIsNotNone(narrow)
        self.assertGreater(narrow, 0)

    def test_single_sided_round_trip(self):
        for side, (lo, hi) in ((0, (300000, 302000)), (1, (-302000, -300000))):
            sa, sb = 1.0001 ** (lo / 2), 1.0001 ** (hi / 2)
            amt = 300e6
            L = amt / (1 / sa - 1 / sb) if side == 0 else amt / (sb - sa)
            self.assertAlmostEqual(portfolio.single_sided_quote(int(L), lo, hi, side), amt, delta=amt * 1e-6)


class Verdict(unittest.TestCase):
    def ev(self, r):
        return filters.evaluate(r, CFG, time.time())

    def test_open(self):
        self.assertEqual(self.ev(row())["level"], "OPEN")

    def test_zero_fee_skips(self):
        self.assertEqual(self.ev(row(lpFeePct=0))["level"], "SKIP")

    def test_honeypot_skips(self):
        self.assertEqual(self.ev(row(lpFeePct=95))["level"], "SKIP")

    def test_young_pool_waits(self):
        self.assertEqual(self.ev(row(ageHours=0.2))["level"], "WAIT")

    def test_hook_is_careful(self):
        v = self.ev(row(hooks="0x" + "11" * 20))
        self.assertEqual(v["level"], "CAREFUL")
        self.assertTrue(any("hook" in f for f in v["flags"]))


class Practice(unittest.TestCase):
    def test_defaults_on(self):
        saved = os.environ.pop("ELYSIUM_PRACTICE_MODE", None)
        try:
            self.assertTrue(cli.practice_mode())
            os.environ["ELYSIUM_PRACTICE_MODE"] = "false"
            self.assertFalse(cli.practice_mode())
            os.environ["ELYSIUM_PRACTICE_MODE"] = "true"
            self.assertTrue(cli.practice_mode())
        finally:
            if saved is None:
                os.environ.pop("ELYSIUM_PRACTICE_MODE", None)
            else:
                os.environ["ELYSIUM_PRACTICE_MODE"] = saved

    def test_report_never_prints_the_key(self):
        secret = "ab" * 32
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text(f"ELYSIUM_PRIVATE_KEY=0x{secret}\nELYSIUM_PRACTICE_MODE=true\n")
            with mock.patch.object(cli, "env_files", return_value=(env,)):
                text = "\n".join(cli.env_report())
        self.assertNotIn(secret, text)
        self.assertIn("format kelihatan benar", text)


class SwapIndex(unittest.TestCase):
    def test_poll_keeps_quote_pools(self):
        w = lambda v: format(v % (1 << 256), "064x")
        tok = "0x" + "11" * 20
        v4 = "0x" + "22" * 32
        other = "0x" + "33" * 32
        v4key = {"dex": "v4", "venue": "uniswap-v4", "currency0": tok, "currency1": chain.USDC,
                 "fee": 10000, "tickSpacing": 200, "hooks": chain.ZERO}
        pool = "0x" + "44" * 20
        created = {"topics": [chain.TOPIC["v3PoolCreated"], "0x" + tok[2:].rjust(64, "0"),
                              "0x" + chain.WHYPE[2:].rjust(64, "0"), "0x" + w(10000)],
                   "data": "0x" + w(200) + "0x" + pool[2:].rjust(64, "0") if False else "0x" + w(200) + pool[2:].rjust(64, "0"),
                   "blockNumber": hex(10)}

        def v4log(pid, a0, a1, fee, blk):
            return {"topics": [chain.TOPIC["v4Swap"], pid, "0x" + "0" * 64], "blockNumber": hex(blk), "address": chain.V4_POOL_MANAGER,
                    "data": "0x" + w(a0) + w(a1) + w(1 << 96) + w(10 ** 12) + w(-100) + w(fee)}

        def v3log(addr, a0, a1, blk):
            return {"topics": [chain.TOPIC["v3Swap"], "0x" + "0" * 64, "0x" + "0" * 64], "address": addr, "blockNumber": hex(blk),
                    "data": "0x" + w(a0) + w(a1) + w(1 << 96) + w(10 ** 12) + w(100)}

        logs_v4 = [v4log(v4, 5 * 10 ** 18, -200 * 10 ** 6, 10000, 990), v4log(other, 1, -1, 3000, 991)]
        logs_v3 = [v3log(pool, -10 ** 18, 2 * 10 ** 18, 992)]

        def get_logs(address, topics, start, end, chunk=2000, pause=0.0):
            top = topics[0]
            if top == chain.TOPIC["v3PoolCreated"]:
                if address == chain.ES_V3_FACTORY:
                    return [created]
                return []
            if top == chain.TOPIC["v4Swap"]:
                return logs_v4
            if top == chain.TOPIC["v3Swap"]:
                return logs_v3
            return []

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(chain, "block_number", return_value=1000), \
                mock.patch.object(chain, "block_time", side_effect=lambda b: int(time.time()) - (1000 - int(b))), \
                mock.patch.object(chain, "hype_usd_from_slot", return_value=75.0), \
                mock.patch.object(chain, "get_logs", side_effect=get_logs), \
                mock.patch.object(chain, "pool_keys", side_effect=lambda ids: {i: v4key if i == v4 else None for i in ids}), \
                mock.patch.object(chain, "slot0s_v4", return_value={v4: {"protocolFee": 0, "lpFee": 10000}}), \
                mock.patch.object(chain, "slot0s_v3", return_value={pool: {"feeProtocol": 0}}):
            ix = activity.Index(Path(d), backfill_hours=0.05)
            n = ix.poll()
            self.assertGreaterEqual(n, 2)
            rows = {r["pool"]: r for r in ix.rows()}
            self.assertIn(v4, rows)
            self.assertIn(pool, rows)
            self.assertNotIn(other, rows)
            self.assertAlmostEqual(rows[v4]["win"]["1h"]["volQuote"], 200, delta=1e-6)
            self.assertAlmostEqual(rows[v4]["win"]["1h"]["feesQuote"], 2, delta=1e-6)   # 1% of 200 USDC
            self.assertEqual(rows[pool]["key"]["venue"], "elysiumswap")
            self.assertAlmostEqual(rows[pool]["win"]["1h"]["volQuote"], 2, delta=1e-6)  # 2 WHYPE in
            ix.save()
            again = activity.Index(Path(d))
            self.assertEqual(again.head, 1000)
            self.assertIn(pool, again.v3pools)


class SettingsRoundTrip(unittest.TestCase):
    def test_save(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(settings, "PATH", Path(d) / "s.json"):
            base = json.loads(json.dumps(CFG))
            settings.save(base, {"position.defaultSizeUsd": "250", "exits.takeProfitPct": ""})
            got = settings.apply(json.loads(json.dumps(CFG)))
            self.assertEqual(got["position"]["defaultSizeUsd"], 250)
            with self.assertRaises(ValueError):
                settings.save(base, {"position.ladderSlices": "0"})


if __name__ == "__main__":
    unittest.main()
