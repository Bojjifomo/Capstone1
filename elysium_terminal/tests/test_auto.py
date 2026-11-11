"""Offline tests: positions stay separate, and the auto rules do what they say. No RPC, no wallet."""
import tempfile
import unittest
from pathlib import Path

from elysium_terminal import auto, depth, groups

POOL = "0x" + "ab" * 32
PM = "0x1d1c51b9d07fd27d36412a2b66cc23d19eb3e518"
NPM = "0xc6dc519451f58dfd27bb2920eefae57597bd91f5"


def nft(tid, lo, hi, manager=PM, venue="uniswap-v4", dex="v4", pool=POOL):
    return {"tokenId": tid, "tickLower": lo, "tickUpper": hi, "manager": manager, "venue": venue, "dex": dex, "pool": pool}


def row(**kw):
    base = {"id": "v4-7", "pool": POOL, "symbol": "KNTQ", "state": "in", "pnlPct": 0.0, "valueUsd": 100.0,
            "posCount": 1, "widthPct": 2.0, "hooked": False, "dynamicFee": False}
    base.update(kw)
    return base


class Grouping(unittest.TestCase):
    def test_two_ladders_in_one_pool_are_two_groups(self):
        # the bug: both ladders showed as one "4 slice" row because they share a pool
        pos = [nft(10, -400, -200), nft(11, -600, -400), nft(20, -400, -200), nft(21, -600, -400)]
        out = groups.group_positions(pos, {}, {10: 500, 11: 500, 20: 900, 21: 900})
        self.assertEqual(sorted(g["id"] for g in out), ["v4-10", "v4-20"])
        self.assertEqual(sorted(len(g["positions"]) for g in out), [2, 2])

    def test_same_range_opened_twice_stays_separate(self):
        pos = [nft(10, -200, 200), nft(11, -200, 200)]
        out = groups.group_positions(pos, {}, {10: 500, 11: 620})
        self.assertEqual(len(out), 2)

    def test_v3_ladder_is_consecutive_ids_with_touching_ranges(self):
        v3 = dict(manager=NPM, venue="elysiumswap", dex="v3")
        pos = [nft(5, -400, -200, **v3), nft(6, -600, -400, **v3), nft(7, -100, 100, **v3), nft(9, 100, 300, **v3)]
        out = {g["id"]: [p["tokenId"] for p in g["positions"]] for g in groups.group_positions(pos, {}, {})}
        self.assertEqual(out, {"es-5": [5, 6], "es-7": [7], "es-9": [9]})

    def test_registry_wins_and_survives_a_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            gid = groups.register(Path(tmp), "uniswap-v4", PM, [31], "tight", depositQuote=1.5, widthPct=2)
            self.assertEqual(gid, "v4-31")
            reg = groups.load(Path(tmp))
            # 30 and 31 were minted in the same block, but 31 is a registered tight position of its own
            out = groups.group_positions([nft(30, -200, 0), nft(31, 0, 200)], reg, {30: 1, 31: 1})
            by = {g["id"]: g for g in out}
            self.assertEqual(sorted(by), ["v4-30", "v4-31"])
            self.assertEqual(by["v4-31"]["meta"]["kind"], "tight")
            self.assertEqual(by["v4-31"]["meta"]["depositQuote"], 1.5)
            groups.prune(Path(tmp), {(PM, 30)})
            self.assertEqual(groups.load(Path(tmp)), {})


class Depth(unittest.TestCase):
    def test_walks_liquidity_net_out_from_the_price(self):
        # two positions: A = [-200, 200) with 100, B = [0, 400) with 50. Price at tick 10.
        nets = {-200: 100, 0: 50, 200: -100, 400: -50}
        segs = depth.segments(10, 150, nets, -600, 600)
        self.assertEqual([(s["a"], s["b"], s["L"]) for s in segs],
                         [(-600, -200, 0), (-200, 0, 100), (0, 200, 150), (200, 400, 50), (400, 600, 0)])

    def test_price_exactly_on_an_initialized_tick(self):
        segs = depth.segments(0, 150, {-200: 100, 0: 50, 200: -100, 400: -50}, -300, 300)
        self.assertEqual([(s["a"], s["b"], s["L"]) for s in segs], [(-300, -200, 0), (-200, 0, 100), (0, 200, 150), (200, 300, 50)])

    def test_window_keeps_range_and_price_in_view(self):
        lo, hi = depth.window(53411, 53400, 53520, 60)
        self.assertTrue(lo < 53400 and hi > 53520 and lo < 53411 < hi)
        lo, hi = depth.window(60000, 53400, 53520, 60)        # price far above the range
        self.assertTrue(lo < 53400 and hi > 60000)


class Decide(unittest.TestCase):
    G = auto.clean_global({"holdSec": 120, "cooldownSec": 600, "maxPerDay": 2, "maxNotionalUsd": 500})

    def rule(self, **kw):
        base = {**auto.clean_rule(kw), "status": "armed"}
        base.update({k: v for k, v in kw.items() if k not in ("tp", "sl", "above", "below")})
        return base

    def test_take_profit_and_stop_loss_exit(self):
        r = self.rule(tp=3, sl=10)
        self.assertEqual(r["sl"], -10.0)
        self.assertEqual(auto.decide(r, row(pnlPct=3.1), self.G, 1000)["action"], "exit")
        self.assertEqual(auto.decide(r, row(pnlPct=-10.5), self.G, 1000)["action"], "exit")
        self.assertEqual(auto.decide(r, row(pnlPct=1.0), self.G, 1000)["action"], "none")

    def test_out_of_range_waits_then_reenters(self):
        r = self.rule(above="reentry")
        first = auto.decide(r, row(state="above"), self.G, 1000)
        self.assertEqual(first["action"], "none")
        r.update(first["patch"])
        self.assertEqual(auto.decide(r, row(state="above"), self.G, 1060)["action"], "none")
        self.assertEqual(auto.decide(r, row(state="above"), self.G, 1121)["action"], "reentry")

    def test_old_rebalance_rules_now_mean_reentry(self):
        self.assertEqual(auto.clean_rule({"above": "rebalance", "below": "close"})["above"], "reentry")
        saved = {"above": "rebalance", "below": "hold", "status": "armed", "outSince": 0, "outSide": "above"}
        self.assertEqual(auto.decide(saved, row(state="above"), self.G, 1000)["action"], "reentry")

    def test_coming_back_in_range_resets_the_wait(self):
        r = self.rule(above="reentry", outSince=1000, outSide="above")
        back = auto.decide(r, row(state="in"), self.G, 1100)
        self.assertIsNone(back["patch"]["outSince"])

    def test_each_side_has_its_own_action(self):
        r = self.rule(above="reentry", below="close", outSince=0, outSide="below")
        self.assertEqual(auto.decide(r, row(state="below"), self.G, 1000)["action"], "exit")
        hold = self.rule(above="reentry")
        self.assertEqual(auto.decide(hold, row(state="below"), self.G, 1000)["action"], "none")

    def test_cooldown_daily_cap_notional_and_pause(self):
        base = dict(above="reentry", outSince=0, outSide="above")
        self.assertIn("cooldown", auto.decide(self.rule(**base, lastFire=900), row(state="above"), self.G, 1000)["reason"])
        capped = self.rule(**base, day=auto._day(1000), dayCount=2)
        self.assertIn("hari ini", auto.decide(capped, row(state="above"), self.G, 1000)["reason"])
        self.assertIn("batas auto", auto.decide(self.rule(**base), row(state="above", valueUsd=900), self.G, 1000)["reason"])
        paused = {**self.G, "paused": True}
        self.assertEqual(auto.decide(self.rule(tp=1), row(pnlPct=50), paused, 1000)["action"], "none")

    def test_reentry_is_skipped_when_the_range_already_hugs_the_price(self):
        # quote is token0, spacing 60, tick 54562: a re-entry would mint 54600-54720 again
        r = self.rule(above="reentry", outSince=0, outSide="above")
        hug = row(state="above", flip=True, tick=54562, tickSpacing=60, tickLower=54600, tickUpper=54720)
        self.assertEqual(auto.decide(r, hug, self.G, 1000)["action"], "none")
        away = {**hug, "tick": 54400}                      # price ran further: now it is worth moving
        self.assertEqual(auto.decide(r, away, self.G, 1000)["action"], "reentry")
        q1 = row(state="above", flip=False, tick=1210, tickSpacing=60, tickLower=1080, tickUpper=1200)
        self.assertEqual(auto.decide(r, q1, self.G, 1000)["action"], "none")

    def test_a_ladder_can_reenter_too(self):
        r = self.rule(above="reentry", outSince=0, outSide="above")
        self.assertEqual(auto.decide(r, row(state="above", posCount=5), self.G, 1000)["action"], "reentry")


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = auto.Store(Path(self.tmp.name))
        self.store.set_global({"holdSec": 0, "cooldownSec": 600, "maxPerDay": 3})
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, rows, result, now=1000.0, partial=False):
        def execute(action, rule, r, decision):
            self.calls.append((action, r["id"]))
            return result
        return auto.run_once(self.store, rows, partial, now, execute)

    def test_two_positions_in_one_pool_have_separate_rules(self):
        a, b = row(id="v4-7", pnlPct=5.0), row(id="v4-9", pnlPct=5.0)
        self.store.set_rule("v4-7", {"tp": 3}, a, 1)
        self.run_once([a, b], {"ok": True})
        self.assertEqual(self.calls, [("exit", "v4-7")])
        self.assertEqual(self.store.view()["rules"]["v4-7"]["status"], "done")
        self.assertNotIn("v4-9", self.store.view()["rules"])

    def test_reentry_moves_the_rule_to_the_new_position(self):
        r = row(state="above")
        self.store.set_rule("v4-7", {"above": "reentry", "tp": 20}, r, 1)
        self.run_once([r], {"ok": True, "newId": "v4-8"})
        rules = self.store.view()["rules"]
        self.assertEqual(list(rules), ["v4-8"])
        self.assertEqual((rules["v4-8"]["status"], rules["v4-8"]["count"], rules["v4-8"]["tp"]), ("armed", 1, 20.0))
        # the new position is out of range again a minute later: cooldown holds it
        self.run_once([row(id="v4-8", state="above")], {"ok": True, "newId": "v4-99"}, now=1060.0)
        self.assertEqual(len(self.calls), 1)

    def test_practice_run_does_not_loop(self):
        r = row(state="below")
        self.store.set_rule("v4-7", {"below": "reentry"}, r, 1)
        self.run_once([r], {"ok": True, "dryRun": True})
        self.run_once([r], {"ok": True, "dryRun": True}, now=1030.0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.view()["rules"]["v4-7"]["status"], "armed")

    def test_half_finished_cycle_is_never_retried(self):
        r = row(state="above")
        self.store.set_rule("v4-7", {"above": "reentry"}, r, 1)
        self.run_once([r], {"ok": False, "sent": True, "stage": "swap", "error": "would revert"})
        self.assertEqual(self.store.view()["rules"]["v4-7"]["status"], "attention")
        self.run_once([r], {"ok": True}, now=9000.0)
        self.assertEqual(len(self.calls), 1)

    def test_clean_failure_retries_after_cooldown_then_gives_up(self):
        r = row(state="above")
        self.store.set_rule("v4-7", {"above": "close"}, r, 1)
        fail = {"ok": False, "sent": False, "stage": "close", "error": "rpc"}
        self.run_once([r], fail, now=1000.0)
        self.run_once([r], fail, now=1100.0)             # still cooling down
        self.assertEqual(len(self.calls), 1)
        self.run_once([r], fail, now=2000.0)
        self.run_once([r], fail, now=3000.0)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.store.view()["rules"]["v4-7"]["status"], "failed")

    def test_missing_row_only_ends_the_rule_on_a_complete_read(self):
        self.store.set_rule("v4-7", {"tp": 3}, row(), 1)
        self.run_once([], {"ok": True}, partial=True)
        self.assertEqual(self.store.view()["rules"]["v4-7"]["status"], "armed")
        self.run_once([], {"ok": True}, partial=False)
        self.assertEqual(self.store.view()["rules"]["v4-7"]["status"], "done")

    def test_crash_mid_action_needs_a_human(self):
        self.store.set_rule("v4-7", {"tp": 3}, row(), 1)
        self.store.patch("v4-7", {"status": "running"})
        self.assertEqual(auto.Store(Path(self.tmp.name)).recover(5), 1)
        self.assertEqual(self.store.view()["rules"]["v4-7"]["status"], "attention")

    def test_old_per_pool_take_profit_is_reattached_per_position(self):
        (Path(self.tmp.name) / "auto.json").unlink()
        (Path(self.tmp.name) / "auto_exit.json").write_text('{"%s": {"pct": 3, "symbol": "KNTQ", "status": "armed"}}' % POOL)
        store = auto.Store(Path(self.tmp.name))
        self.assertEqual(store.adopt_legacy([row(id="v4-7"), row(id="v4-9")], 1), 2)
        rules = store.view()["rules"]
        self.assertEqual({k: v["tp"] for k, v in rules.items()}, {"v4-7": 3.0, "v4-9": 3.0})
        self.assertEqual(store.adopt_legacy([row(id="v4-11")], 2), 0)

    def test_empty_rule_removes_it(self):
        self.store.set_rule("v4-7", {"tp": 3}, row(), 1)
        self.assertIsNone(self.store.set_rule("v4-7", {}, row(), 2))
        self.assertEqual(self.store.view()["rules"], {})


if __name__ == "__main__":
    unittest.main()
