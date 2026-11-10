"""Offline tests for tight-range math, signals, and the rebalance state machine.

No RPC, no wallet, no broadcast. The executor is a fake that counts calls.
"""
import tempfile
import unittest
from pathlib import Path

from elysium_terminal import tight


def _points(now, n=8, span=3300, px=1.0):
    step = span / (n - 1)
    return [{"t": now - span + i * step, "px": px * (1 + 0.001 * i), "swaps": 4, "vol": 10} for i in range(n)]


def _row(**kw):
    base = {
        "pool": "0x" + "ab" * 32, "symbol": "BOX", "quoteSymbol": "HYPE", "dex": "v4",
        "tick": 0, "tickSpacing": 1, "fees1h": 50.0, "tvlUsd": 1000.0, "vol1h": 20000.0,
        "swaps1h": 40, "feePpm": 10000, "feeSource": "index", "feeWindowSec": 3600,
        "hooks": tight.ZERO, "quoteIsToken0": False, "notionalUsd": 100.0, "dynamicFee": False,
    }
    base.update(kw)
    return base


def _pos(**kw):
    base = {
        "tokenId": 7, "pool": "0x" + "ab" * 32, "dex": "v4", "hooks": tight.ZERO,
        "tickSpacing": 10, "tickLower": -500, "tickUpper": -100, "valueUsd": 100.0,
        "quoteIsToken0": False, "dynamicFee": False,
    }
    base.update(kw)
    return base


def _snap(**kw):
    base = {"tick": 0, "ageSec": 10, "rpcOk": True, "movePct": 0.4, "grossPctPerHour": 5.0, "feePpm": 3000}
    base.update(kw)
    return base


class TickWidth(unittest.TestCase):
    def test_one_percent_around_zero_is_about_fifty_ticks_each_side(self):
        band = tight.align_range(0, 1, 1.0)
        self.assertTrue(band["ok"])
        # 0.5% each side is a little more than 50 ticks, then snapped outward.
        self.assertEqual(band["tickLower"], -51)
        self.assertEqual(band["tickUpper"], 50)
        self.assertLess(abs(band["actualWidthPct"] - 1.0), 0.05)
        self.assertFalse(band["spacingCoarse"])
        where = tight.range_status(0, band["tickLower"], band["tickUpper"])
        self.assertTrue(where["inRange"])
        self.assertEqual(where["side"], "in")

    def test_coarse_spacing_is_reported_not_pretended_tight(self):
        band = tight.align_range(100, 200, 0.2)
        self.assertTrue(band["ok"])
        self.assertEqual(band["tickLower"] % 200, 0)
        self.assertEqual(band["tickUpper"] % 200, 0)
        self.assertLessEqual(band["tickLower"], 100)
        self.assertGreater(band["tickUpper"], 100)
        self.assertTrue(band["spacingCoarse"])
        self.assertGreater(band["actualWidthPct"], 0.2 * 1.5)

    def test_negative_tick_stays_on_the_grid_and_inside(self):
        band = tight.align_range(-12345, 60, 1.0)
        self.assertTrue(band["ok"])
        self.assertEqual(band["tickLower"] % 60, 0)
        self.assertEqual(band["tickUpper"] % 60, 0)
        self.assertLessEqual(band["tickLower"], -12345)
        self.assertGreater(band["tickUpper"], -12345)

    def test_bad_width_and_missing_spacing(self):
        self.assertFalse(tight.align_range(0, 0, 1)["ok"])
        self.assertFalse(tight.align_range(0, 1, 0)["ok"])
        self.assertFalse(tight.align_range(0, 1, 200)["ok"])

    def test_out_of_range_sides(self):
        below = tight.range_status(-10, 0, 100)
        above = tight.range_status(100, 0, 100)
        self.assertEqual(below["side"], "below")
        self.assertEqual(above["side"], "above")
        self.assertFalse(below["inRange"])
        self.assertGreater(below["priceToEdgePct"], 0)

    def test_tick_on_upper_bound_is_outside(self):
        # Uniswap: in range while tickLower <= tick < tickUpper.
        self.assertFalse(tight.range_status(100, 0, 100)["inRange"])
        self.assertTrue(tight.range_status(99, 0, 100)["inRange"])


class YieldUnits(unittest.TestCase):
    def test_five_percent_per_hour_is_43800_simple_apr(self):
        # $50 of fees on $1000 TVL over a full hour is 5%/hour.
        # Simple APR = 5 × 24 × 365 = 43800. A daily 5% would be 1825, and that is a different number.
        y = tight.gross_yield(50, 1000, 3600)
        self.assertAlmostEqual(y["grossPctPerHour"], 5.0)
        self.assertAlmostEqual(y["simpleAprPct"], 43800.0)
        self.assertFalse(y["scaled"])

    def test_short_window_is_marked_scaled(self):
        y = tight.gross_yield(10, 1000, 1200)
        self.assertTrue(y["scaled"])
        self.assertAlmostEqual(y["grossPctPerHour"], 3.0)  # 1% in 20 min → 3%/hour

    def test_missing_tvl_is_not_a_yield(self):
        y = tight.gross_yield(10, None, 3600)
        self.assertIsNone(y["grossPctPerHour"])
        self.assertIsNone(y["simpleAprPct"])

    def test_net_does_not_pretend_to_include_il_or_gas(self):
        net = tight.net_estimate(gross_pct_per_hour=5, notional_usd=100, swap_frac=0.5,
                                 fee_ppm=10000, horizon_hours=1)
        # Gross proxy $5. Swap cost = 100 * 0.5 * 1% = $0.50. Net partial $4.50.
        self.assertAlmostEqual(net["grossUsd"], 5.0)
        self.assertAlmostEqual(net["swapCostUsd"], 0.5)
        self.assertAlmostEqual(net["netUsd"], 4.5)
        self.assertIsNone(net["gasUsd"])
        self.assertIsNone(net["ilUsd"])
        self.assertFalse(net["complete"])
        self.assertTrue(any("IL" in x for x in net["limitations"]))

    def test_tiny_sample_is_rejected_and_not_called_certain(self):
        v = tight.assess(fee_usd=50, tvl_usd=1000, covered_sec=600, swaps=2, buckets=1,
                         age_sec=10, move_pct=0.1, width_pct=1, target_pct_per_hour=5,
                         fee_source="index", spacing_coarse=False, fee_window_sec=3600)
        self.assertFalse(v["rawAbove"])
        self.assertEqual(v["status"], "tidak cocok")
        self.assertIn("bukan kepastian", " ".join(v["rejects"]))
        self.assertIn("jangan dianggap pasti", v["confidence"])
        self.assertIsNone(v["positionFeeUsd"])
        self.assertTrue(v["poolProxy"])

    def test_target_is_a_filter_not_a_gate_that_hides_the_number(self):
        v = tight.assess(fee_usd=10, tvl_usd=1000, covered_sec=3300, swaps=40, buckets=8,
                         age_sec=30, move_pct=0.2, width_pct=1, target_pct_per_hour=5,
                         fee_source="index", spacing_coarse=False, fee_window_sec=3600)
        self.assertEqual(v["status"], "di bawah target")
        self.assertAlmostEqual(v["grossPctPerHour"], 1.0)
        self.assertFalse(v["rawAbove"])
        self.assertTrue(any("bukan patokan aman" in n for n in v["notes"]))

    def test_full_sample_above_target_can_pass(self):
        v = tight.assess(fee_usd=50, tvl_usd=1000, covered_sec=3300, swaps=40, buckets=8,
                         age_sec=30, move_pct=0.2, width_pct=1, target_pct_per_hour=5,
                         fee_source="index", spacing_coarse=False, fee_window_sec=3600)
        self.assertTrue(v["rawAbove"])
        self.assertEqual(v["status"], "lolos filter")
        self.assertAlmostEqual(v["simpleAprPct"], 43800.0)

    def test_move_wider_than_the_range_is_rejected(self):
        v = tight.assess(fee_usd=50, tvl_usd=1000, covered_sec=3300, swaps=40, buckets=8,
                         age_sec=30, move_pct=4, width_pct=1, target_pct_per_hour=5,
                         fee_source="index", spacing_coarse=False, fee_window_sec=3600)
        self.assertFalse(v["rawAbove"])
        self.assertTrue(any("lebih lebar" in r for r in v["rejects"]))


class Hysteresis(unittest.TestCase):
    def test_needs_three_passes_and_two_misses(self):
        st = None
        for i in range(2):
            st = tight.apply_hysteresis(st, True, 1000 + i, 3, 2)
            self.assertFalse(st["on"])
        st = tight.apply_hysteresis(st, True, 1002, 3, 2)
        self.assertTrue(st["on"])
        st = tight.apply_hysteresis(st, False, 1003, 3, 2)
        self.assertTrue(st["on"])
        st = tight.apply_hysteresis(st, False, 1004, 3, 2)
        self.assertFalse(st["on"])

    def test_screen_uses_indexed_series_only(self):
        now = 1_000_000.0
        card = tight.screen_row(_row(), _points(now), now, tight.DEFAULT_CONFIG)
        self.assertTrue(card["verdict"]["rawAbove"])
        self.assertIsNone(card["verdict"]["positionFeeUsd"])
        self.assertTrue(card["where"]["inRange"])
        self.assertTrue(card["supported"])


class Review(unittest.TestCase):
    def test_open_review_lists_separate_steps_and_slippage(self):
        rev = tight.review_open(tick=0, spacing=1, width_pct=1, usd=100, quote_is_token0=False,
                                fee_ppm=10000, slippage_bps=100, gross_pct_per_hour=5)
        self.assertTrue(rev["ok"])
        self.assertFalse(rev["atomic"])
        self.assertEqual([s["what"] for s in rev["steps"]], ["swap", "mint"])
        self.assertIn("slippage 1.00%", rev["minOutNote"])
        self.assertIn("Gas tidak dihitung", rev["gasNote"])
        self.assertGreater(rev["steps"][0]["usd"], 0)
        self.assertLess(rev["steps"][0]["usd"], 100)

    def test_close_then_reopen_is_not_one_transaction(self):
        rev = tight.review_open(tick=0, spacing=1, width_pct=1, usd=80, quote_is_token0=True,
                                fee_ppm=3000, slippage_bps=50, include_close=True)
        self.assertEqual(rev["steps"][0]["what"], "close")
        self.assertGreater(len(rev["steps"]), 1)
        self.assertFalse(rev["atomic"])


class RebalanceMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = tight.Store(Path(self.tmp.name))
        self.store.save_config({
            "enabled": True, "watch": [{"pool": _pos()["pool"], "tokenId": 7, "symbol": "BOX"}],
            "persistenceSec": 0, "cooldownSec": 60, "maxPerDay": 2, "minNetUsd": 1,
            "maxSwapUsd": 200, "maxNotionalUsd": 400, "widthPct": 1, "boundaryPct": 0,
        })
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _exec(self, payload):
        self.calls.append(payload)
        return {"ok": True, "simulated": True}

    def test_off_by_default_and_practice_blocks_live(self):
        fresh = tight.Store(Path(self.tmp.name) / "other")
        cfg = fresh.config()
        self.assertFalse(cfg["enabled"])
        self.assertTrue(cfg["practice"])
        self.assertFalse(tight.live_allowed(cfg, env_practice=False))
        self.assertFalse(tight.live_allowed(fresh.save_config({"allowLive": True, "liveAck": "salah"}), True))
        allowed = fresh.save_config({"allowLive": True, "liveAck": tight.LIVE_PHRASE, "enabled": True})
        self.assertFalse(allowed["practice"])
        self.assertTrue(tight.live_allowed(allowed, env_practice=False))
        self.assertFalse(tight.live_allowed(allowed, env_practice=True))

    def test_persistence_cooldown_cap_and_stale(self):
        self.store.save_config({"persistenceSec": 180})
        now = 10_000.0
        pos, snap = _pos(tickLower=-50, tickUpper=50), _snap(tick=80)
        # Price is above the range, but the timer just started.
        first = tight.run_once(self.store, [pos], {pos["pool"]: snap}, now, self._exec, env_practice=True)
        self.assertEqual(self.calls, [])
        self.assertIn("nunggu", first["results"][0]["reason"])
        second = tight.run_once(self.store, [pos], {pos["pool"]: snap}, now + 181, self._exec, env_practice=True)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["step"], "close")
        self.assertTrue(self.calls[0]["dryRun"])
        # Practice stops after the simulated close. No swap, no mint.
        third = tight.run_once(self.store, [pos], {pos["pool"]: snap}, now + 182, self._exec, env_practice=True)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("practice", third["results"][0]["reason"])

    def test_stale_suspicious_and_net_gate_do_not_fire(self):
        now = 20_000.0
        pos = _pos(tickLower=-50, tickUpper=50)
        stale = tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80, ageSec=10_000)}, now, self._exec)
        self.assertEqual(self.calls, [])
        self.assertIn("basi", stale["results"][0]["reason"])
        jump = tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80, movePct=20)}, now, self._exec)
        self.assertIn("mencurigakan", jump["results"][0]["reason"])
        self.store.save_position(7, {"paused": False, "pauseReason": ""})
        poor = tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80, grossPctPerHour=0.01)}, now, self._exec)
        self.assertEqual(self.calls, [])
        self.assertIn("net", poor["results"][0]["reason"])

    def test_pending_hash_is_not_replaced_or_resent(self):
        now = 30_000.0
        pos = _pos(tickLower=-50, tickUpper=50)
        self.store.save_position(7, {"pendingHash": "0xabc", "pendingStep": "close", "outSince": now - 500})
        out = tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80)}, now, self._exec)
        self.assertEqual(self.calls, [])
        self.assertEqual(out["results"][0]["action"], "wait")
        still = tight.resolve_receipt(self.store, 7, None, now)
        self.assertEqual(still["state"], "pending")
        self.assertEqual(self.store.positions()["7"]["pendingHash"], "0xabc")

    def test_receipt_failure_pauses_and_does_not_retry(self):
        now = 40_000.0
        self.store.save_position(7, {"pendingHash": "0xabc", "pendingStep": "close"})
        failed = tight.resolve_receipt(self.store, 7, {"ok": False}, now)
        self.assertEqual(failed["state"], "failed")
        out = tight.run_once(self.store, [_pos(tickLower=-50, tickUpper=50)],
                             {_pos()["pool"]: _snap(tick=80)}, now, self._exec)
        self.assertEqual(self.calls, [])
        self.assertEqual(out["results"][0]["action"], "pause")

    def test_inflight_without_a_hash_is_not_broadcast_again(self):
        now = 50_000.0
        self.store.save_position(7, {"inflightKey": "7:1:close", "outSince": now - 500})
        out = tight.run_once(self.store, [_pos(tickLower=-50, tickUpper=50)],
                             {_pos()["pool"]: _snap(tick=80)}, now, self._exec)
        self.assertEqual(self.calls, [])
        self.assertIn("hash-nya tidak tersimpan", out["results"][0]["reason"])

    def test_live_continuation_is_one_step_and_a_duplicate_key_does_not_resend(self):
        self.store.save_config({"allowLive": True, "liveAck": tight.LIVE_PHRASE})
        now = 60_000.0
        pos = _pos(tickLower=-50, tickUpper=50)

        def send(payload):
            self.calls.append(payload["step"])
            return {"ok": True, "hash": "0x" + payload["step"]}

        tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80)}, now, send, env_practice=False)
        self.assertEqual(self.calls, ["close"])
        tight.resolve_receipt(self.store, 7, {"ok": True}, now + 1)
        tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80)}, now + 2, send, env_practice=False)
        self.assertEqual(self.calls, ["close", "swap"])
        # Same step is pending until the receipt. A second pass must not send it again.
        again = tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80)}, now + 3, send, env_practice=False)
        self.assertEqual(self.calls, ["close", "swap"])
        self.assertEqual(again["results"][0]["action"], "wait")

    def test_daily_cap(self):
        now = 70_000.0
        pos = _pos(tickLower=-50, tickUpper=50)
        self.store.save_config({"maxPerDay": 1, "persistenceSec": 0, "cooldownSec": 60})
        tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80)}, now, self._exec)
        self.assertEqual(len(self.calls), 1)
        # Finish the practice cycle so the next attempt is a new one inside the cooldown/cap.
        tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80)}, now + 1, self._exec)
        blocked = tight.run_once(self.store, [pos], {pos["pool"]: _snap(tick=80)}, now + 61, self._exec)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(blocked["results"][0]["action"] in ("none", "pause"))

    def test_kill_switch_and_unsupported_hook(self):
        self.store.save_config({"kill": True})
        out = tight.run_once(self.store, [_pos()], {_pos()["pool"]: _snap()}, 80_000, self._exec)
        self.assertFalse(out["ran"])
        self.assertIn("kill", out["reason"])
        self.store.save_config({"clearKill": True, "enabled": True,
                                "watch": [{"pool": _pos()["pool"], "tokenId": 7}]})
        hooked = tight.run_once(self.store, [_pos(hooks="0x" + "11" * 20, tickLower=-50, tickUpper=50)],
                                {_pos()["pool"]: _snap(tick=80)}, 80_000, self._exec)
        self.assertEqual(self.calls, [])
        self.assertEqual(hooked["results"][0]["action"], "unsupported")

    def test_manual_claim_rejects_a_second_broadcast(self):
        now = 90_000.0
        blocked = tight.manual_claim(self.store, "manual:7:close", 7)
        self.assertIsNone(blocked)
        tight.manual_finish(self.store, 7, "manual:7:close", {"ok": True, "hash": "0x1"}, now)
        again = tight.manual_claim(self.store, "manual:7:close", 7)
        self.assertTrue(again.get("wait") or again.get("duplicate") or again.get("error"))
        self.assertIsNotNone(again)

    def test_config_survives_reload(self):
        self.store.save_config({"widthPct": 0.5, "targetPctPerHour": 5})
        again = tight.Store(Path(self.tmp.name))
        cfg = again.config()
        self.assertEqual(cfg["widthPct"], 0.5)
        self.assertEqual(cfg["targetPctPerHour"], 5)
        self.assertFalse(cfg["killed"])


if __name__ == "__main__":
    unittest.main()
