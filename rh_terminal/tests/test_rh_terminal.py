"""Offline tests: no network. python3 -m unittest rh_terminal.tests.test_rh_terminal"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rh_terminal import activity, chain, filters, portfolio, settings
from rh_terminal.cli import read_secrets

CFG = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text())


def row(**kw):
    r = {"lpFeePct": 1.0, "ageHours": 30, "tvlUsd": 50_000, "mcapUsd": 2_000_000, "feePctDay": {"1h": 10, "24h": 8},
         "chg": {"1h": 2, "6h": -3}, "txns": {"h1": {"buys": 10, "sells": 10}, "h24": {"buys": 100, "sells": 100}},
         "vol": {"1h": 1000, "24h": 24000}, "hooks": chain.ZERO}
    r.update(kw)
    return r


class LpFee(unittest.TestCase):
    def test_protocol_cut_removed(self):
        # V4: swapFee = pf + lp - pf*lp/1e6. Measured on chain: lp 100 + pf 25 = 125, lp 4000 + pf 700 = 4698.
        self.assertEqual(activity.lp_fee(125, 25 | (25 << 12), True), 100)
        # the event's fee is rounded up on chain, so the LP part comes back within a pip
        self.assertAlmostEqual(activity.lp_fee(4698, 700 | (700 << 12), False), 4000, delta=1)
        self.assertEqual(activity.lp_fee(10000, 0, True), 10000)

    def test_direction_bits(self):
        pf = 100 | (300 << 12)            # 0->1 = 100, 1->0 = 300
        self.assertEqual(activity.lp_fee(1100, pf, True), 1000)
        self.assertEqual(activity.lp_fee(1300, pf, False), 1000)


class Chain(unittest.TestCase):
    def test_quote_side(self):
        tok = "0x" + "ab" * 20
        self.assertEqual(chain.quote_side({"currency0": chain.ZERO, "currency1": tok}), 0)
        self.assertEqual(chain.quote_side({"currency0": tok, "currency1": chain.USDG}), 1)
        self.assertEqual(chain.quote_side({"currency0": chain.ZERO, "currency1": chain.USDG}), 1)   # USDG beats ETH
        self.assertIsNone(chain.quote_side({"currency0": tok, "currency1": "0x" + "cd" * 20}))

    def test_price_orientation(self):
        # ETH/USDG at $2000: sqrtP for 2000 USDG (6 dec) per ETH (18 dec)
        raw = 2000 * 10 ** 6 / 10 ** 18
        sqrt = int(raw ** 0.5 * 2 ** 96)
        self.assertAlmostEqual(chain.price_of(sqrt, 18, 6), 2000, delta=0.01)

    def test_decoders(self):
        w = lambda v: format(v % (1 << 256), "064x")
        key = "0x" + chain.addr_word(chain.ZERO) + chain.addr_word("0x" + "ab" * 20) + w(10000) + w(-200) + chain.addr_word(chain.ZERO)
        k = chain.decode_pool_key(key)
        self.assertEqual((k["fee"], k["tickSpacing"], k["currency1"]), (10000, -200, "0x" + "ab" * 20))
        s0 = chain.decode_slot0("0x" + w(1 << 96) + w(-345405) + w(1000 | (1000 << 12)) + w(50000))
        self.assertEqual((s0["tick"], s0["lpFee"], s0["protocolFee"] & 0xFFF), (-345405, 50000, 1000))
        self.assertIsNone(chain.decode_pool_key("0x" + "0" * 64 * 5))

    def test_pool_id_matches_known(self):
        # pool id of the ETH/token 1% pool used in the live simulation
        pid = chain.pool_id(chain.ZERO, "0x978e82db664577e78e3a8dae8812a876b52daf5a", 10000, 200, chain.ZERO)
        self.assertEqual(pid, "0x3458a47fb6b52e240a7f99b6a6d2df63170c59dfa2f1b0260c8afb7cd86bc58a")


class Ladder(unittest.TestCase):
    def test_single_sided_round_trip(self):
        # liquidity bought by 300 USDG over a range gives back 300 USDG, in either orientation
        # realistic ticks for each orientation: quote = currency0 sits at high ticks, currency1 at low ones
        for side, (lo, hi) in ((0, (300000, 302000)), (1, (-302000, -300000))):
            sa, sb = 1.0001 ** (lo / 2), 1.0001 ** (hi / 2)
            amt = 300e6
            L = amt / (1 / sa - 1 / sb) if side == 0 else amt / (sb - sa)
            self.assertAlmostEqual(portfolio.single_sided_quote(int(L), lo, hi, side), amt, delta=amt * 1e-6)

    def test_amounts_out_of_range(self):
        a0, a1 = portfolio.amounts(10 ** 18, 100, 200, 50, int(1.0001 ** 25 * 2 ** 96))
        self.assertGreater(a0, 0)
        self.assertEqual(a1, 0)


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
        v = self.ev(row(ageHours=0.5))
        self.assertEqual(v["level"], "WAIT")
        self.assertTrue(any(g.startswith("poolAge") for g in v["gatesFailed"]))

    def test_unknown_tvl_waits(self):
        self.assertEqual(self.ev(row(tvlUsd=None))["level"], "WAIT")

    def test_pump_is_careful(self):
        v = self.ev(row(chg={"1h": 40, "6h": 10}))
        self.assertEqual(v["level"], "CAREFUL")
        self.assertIn("pump1h +40%", v["flags"])

    def test_max_position(self):
        self.assertAlmostEqual(filters.max_position_usd(CFG), 8000 * 0.05 / 0.9)


class Settings(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(settings, "PATH", Path(d) / "s.json"):
            base = json.loads(json.dumps(CFG))
            settings.save(base, {"position.defaultSizeUsd": "250", "exits.takeProfitPct": "", "ping.sortBy": "fees1hUsd"})
            got = settings.apply(json.loads(json.dumps(CFG)))
            self.assertEqual(got["position"]["defaultSizeUsd"], 250)
            self.assertEqual(got["ping"]["sortBy"], "fees1hUsd")
            self.assertNotIn("exits.takeProfitPct", settings.load())     # same as config.json: not stored
            with self.assertRaises(ValueError):
                settings.save(base, {"position.ladderSlices": "0"})
            with self.assertRaises(ValueError):
                settings.save(base, {"not.a.key": 1})


class Secrets(unittest.TestCase):
    def test_reads_rh_secrets_without_running_it(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "RH Secrets.js"
            p.write_text('module.exports = {\n  // ALCHEMY_API_KEY: "commented",\n  ALCHEMY_API_KEY: "abc_123",\n'
                         '  PRIVATE_KEY: "",\n  PRACTICE_MODE: true,\n  ZAP_PRESETS: [{ amount: 400 }],\n};\n')
            s = read_secrets(p)
            self.assertEqual(s["ALCHEMY_API_KEY"], "abc_123")
            self.assertEqual(s["PRIVATE_KEY"], "")
            self.assertEqual(s["PRACTICE_MODE"], "true")


class SwapIndex(unittest.TestCase):
    """Index.poll on synthetic Swap logs: quote pools only, LP fee per swap, windows."""

    def test_poll(self):
        w = lambda v, bits=256: format(v % (1 << 256), "064x")
        usdg_pool, eth_pool, other = "0x" + "11" * 32, "0x" + "22" * 32, "0x" + "33" * 32
        tok = "0x" + "ab" * 20
        keys = {usdg_pool: {"currency0": tok, "currency1": chain.USDG, "fee": 50000, "tickSpacing": 1000, "hooks": chain.ZERO},
                eth_pool: {"currency0": chain.ZERO, "currency1": tok, "fee": 0x800000, "tickSpacing": 200, "hooks": chain.ZERO},
                other: None}
        sqrt_eth = int((2000 * 10 ** 6 / 10 ** 18) ** 0.5 * 2 ** 96)
        eth_usdg = "0x" + "44" * 32
        keys[eth_usdg] = {"currency0": chain.ZERO, "currency1": chain.USDG, "fee": 500, "tickSpacing": 10, "hooks": chain.ZERO}

        def log(pid, a0, a1, fee, blk, sqrt=1 << 96):
            return {"topics": [chain.TOPIC["Swap"], pid, "0x" + "0" * 64], "blockNumber": hex(blk),
                    "data": "0x" + w(a0) + w(a1) + w(sqrt) + w(10 ** 18) + w(-100) + w(fee)}
        logs = [log(usdg_pool, -1000, 200 * 10 ** 6, 51000, 990),         # sold token for 200 USDG at a 5% tier
                log(usdg_pool, 5000, -100 * 10 ** 6, 51000, 995),         # bought token with 100 USDG
                log(eth_pool, -10 ** 17, 10 ** 20, 3000, 996),            # dynamic fee pool, 0.1 ETH in, fee 0.3%
                log(eth_usdg, -10 ** 18, 2000 * 10 ** 6, 500, 997, sqrt_eth),
                log(other, 1, -1, 3000, 998)]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(chain, "block_number", return_value=1000), \
                mock.patch.object(chain, "block_time", side_effect=lambda b: int(time.time()) - (1000 - b)), \
                mock.patch.object(chain, "get_logs", return_value=logs), \
                mock.patch.object(chain, "pool_keys", side_effect=lambda ids: {i: keys[i] for i in ids}), \
                mock.patch.object(chain, "slot0s", side_effect=lambda ids: {i: {"protocolFee": 0} for i in ids}):
            ix = activity.Index(Path(d), backfill_hours=0.01)
            n = ix.poll()
            self.assertEqual(n, 5)
            self.assertAlmostEqual(ix.eth_usd(), 2000, delta=0.1)
            rows = {r["pool"]: r for r in ix.rows()}
            self.assertNotIn(other, rows)                                  # no USDG/ETH side: ignored
            u = rows[usdg_pool]["win"]["1h"]
            self.assertEqual(u["swaps"], 2)
            self.assertAlmostEqual(u["volUsd"], 300)
            self.assertAlmostEqual(u["feesUsd"], 300 * 0.05)               # static tier, not the event's 5.1%
            e = rows[eth_pool]["win"]["1h"]
            self.assertAlmostEqual(e["volUsd"], 0.1 * 2000)
            self.assertAlmostEqual(e["feesUsd"], 0.1 * 2000 * 0.003)       # dynamic: the event's fee
            ix.save()
            again = activity.Index(Path(d))
            self.assertEqual(again.head, 1000)
            self.assertEqual(len(again.rows()), 3)


if __name__ == "__main__":
    unittest.main()


class EnvPrecedence(unittest.TestCase):
    """.env beats RH Secrets, also when the web re-reads both (practice mode must not flip off)."""

    def test_env_beats_secrets_on_reload(self):
        import os
        from rh_terminal import cli
        with tempfile.TemporaryDirectory() as d:
            env, sec = Path(d) / ".env", Path(d) / "RH Secrets.js"
            env.write_text("RH_PRACTICE_MODE=true\n")
            sec.write_text('module.exports = { PRACTICE_MODE: false, ALCHEMY_API_KEY: "k1" };\n')
            keys = ("RH_PRACTICE_MODE", "ALCHEMY_API_KEY", "RH_PRIVATE_KEY")
            saved = {k: os.environ.pop(k, None) for k in keys}
            try:
                with mock.patch.object(cli, "env_files", return_value=(env, Path(d) / "none")), \
                        mock.patch.object(cli, "secrets_file", return_value=sec):
                    cli._FROM_FILE.clear()
                    for reload in (False, True, True):
                        cli.load_env(reload=reload)
                        self.assertEqual(os.environ["RH_PRACTICE_MODE"], "true")
                        self.assertEqual(os.environ["ALCHEMY_API_KEY"], "k1")     # Secrets fills the gap
                    sec.write_text('module.exports = { PRACTICE_MODE: false, ALCHEMY_API_KEY: "k2" };\n')
                    cli.load_env(reload=True)
                    self.assertEqual(os.environ["ALCHEMY_API_KEY"], "k2")         # a changed Secrets is picked up
                    self.assertTrue(cli.practice_mode())
            finally:
                for k, v in saved.items():
                    os.environ.pop(k, None)
                    if v is not None:
                        os.environ[k] = v


class RpcFallback(unittest.TestCase):
    def test_range_limits_from_real_errors(self):
        f = chain._range_limit
        self.assertEqual(f("Under the Free tier plan, you can make eth_getLogs requests with up to a 10 block range."), 10)
        self.assertEqual(f("query exceeds max block range 2000"), 2000)
        self.assertEqual(f("eth_getLogs block range exceeds 100 blocks"), 100)
        self.assertIsNone(f("logs matched by query exceeds limit of 10000"))     # too many logs: halve instead

    def test_logs_never_go_to_alchemy(self):
        with mock.patch.dict("os.environ", {"ALCHEMY_API_KEY": "abc"}):
            eps = dict(chain.endpoints())
        self.assertEqual(eps["https://robinhood-mainnet.g.alchemy.com/v2/abc"], 10)
        self.assertGreaterEqual(chain.max_log_span(), 2000)


class TradeAccess(unittest.TestCase):
    def _h(self, addr):
        from rh_terminal import web
        h = web.Handler.__new__(web.Handler)
        h.client_address = (addr, 0)
        h.headers = {"X-RH-Trade": "1", "Content-Type": "application/json"}
        return h

    def test_only_mac_and_tailscale(self):
        with mock.patch.dict("os.environ", {"RH_WEB_PIN": "", "RH_WEB_ALLOW_LAN": ""}):
            for a in ("127.0.0.1", "::1", "100.101.5.9", "fd7a:115c:a1e0::1234"):
                self.assertTrue(self._h(a)._is_allowed(), a)
                self.assertIsNone(self._h(a)._trade_refused({}), a)
            for a in ("192.168.1.20", "10.0.0.5", "::ffff:192.168.0.3", "8.8.8.8"):
                self.assertFalse(self._h(a)._is_allowed(), a)
                self.assertIsNotNone(self._h(a)._trade_refused({}), a)

    def test_lan_opt_in(self):
        with mock.patch.dict("os.environ", {"RH_WEB_PIN": "", "RH_WEB_ALLOW_LAN": "true"}):
            self.assertTrue(self._h("192.168.1.20")._is_allowed())
            self.assertFalse(self._h("8.8.8.8")._is_allowed())

    def test_pin_set_local_skips_others_need_it(self):
        with mock.patch.dict("os.environ", {"RH_WEB_PIN": "4829"}):
            self.assertIsNone(self._h("127.0.0.1")._trade_refused({}))
            self.assertEqual(self._h("100.101.5.9")._trade_refused({}), "PIN salah")
            self.assertIsNone(self._h("100.101.5.9")._trade_refused({"pin": "4829"}))

    def test_header_required(self):
        h = self._h("127.0.0.1"); h.headers = {}
        self.assertEqual(h._trade_refused({}), "missing trade header")
