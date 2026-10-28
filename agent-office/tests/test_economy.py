"""Economy pack: gold, the waiting queue, and raid bosses."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import economy  # noqa: E402
import raids  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402


class DataCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ao-gold-"))
        self.old = store.DATA_DIR
        store.DATA_DIR = self.tmp

    def tearDown(self):
        store.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)


class GoldMath(unittest.TestCase):
    def test_budget_maps_onto_the_purse(self):
        self.assertEqual(economy.figures(0, 0, 100), (100, False, False))  # no budget: full purse, not paused
        gold, paused, metered = economy.figures(5, 10, 100)
        self.assertEqual((gold, paused, metered), (50, False, True))
        gold, paused, metered = economy.figures(10, 10, 100)
        self.assertEqual((gold, paused, metered), (0, True, True))
        gold, paused, _ = economy.figures(25, 10, 80)
        self.assertEqual((gold, paused), (0, True))


class Queue(DataCase):
    def eco(self, spent, budget=10):
        office = type("Office", (), {"cfg": {"daily_budget_usd": budget, "daily_gold": 100}, "scanner": None})()
        desk = economy.Economy(office)
        desk.spent_today = lambda: spent
        return desk

    def test_local_quests_wait_and_a_token_cannot_force_them(self):
        desk = self.eco(10)
        self.assertTrue(desk.snapshot()["paused"])
        held = desk.consider(False, False, "job", "ship the health endpoint", {"text": "ship"})
        self.assertTrue(held["queued_for_gold"])
        self.assertEqual(len(desk.snapshot()["queue"]), 1)
        self.assertEqual(desk.consider(True, True, "job", "force it", {})["blocked"], True)
        self.assertEqual(len(desk.snapshot()["queue"]), 1)
        desk.spent_today = lambda: 0
        self.assertIsNone(desk.consider(False, False, "job", "now affordable", {}))
        opened = desk.eco if False else desk
        opened.spent_today = lambda: 10
        self.assertIsNone(opened.consider(False, True, "job", "owner override", {}))

    def test_release_hands_the_quest_back(self):
        desk = self.eco(10)
        started = {}

        class Office:
            cfg = {"daily_budget_usd": 10, "daily_gold": 100}
            scanner = None

            def start_queued(self, item):
                started["item"] = item
                return {"run": "r", "sid": "s"}

        office = Office()
        desk.office = office
        held = desk.consider(False, False, "job", "queued quest", {"text": "go"})
        result = desk.release(held["id"])
        self.assertEqual(result["sid"], "s")
        self.assertEqual(started["item"]["label"], "queued quest")
        self.assertEqual(desk.snapshot()["queue"], [])
        with self.assertRaises(LookupError):
            desk.release("deadbeef")


class Raid(DataCase):
    def test_hits_chip_health_and_victory_pays_loot(self):
        granted = []

        class Progress:
            def actor_for_cwd(self, cwd, sid):
                return sid, "Leader"

            def actor_named(self, name):
                return "id-" + name, name

            def grant(self, key, name, xp, kind, title, rarity="Epic", ok=True, cost=None, seconds=0):
                granted.append((key, xp, kind))
                return []

        office = type("Office", (), {"progress": Progress(), "ledger": None, "scanner": None, "team": None})()
        book = raids.Raids(office)
        book.begin("boss", "Rebuild the fee board")
        book.join("Lyra")
        self.assertEqual(book.strike("Lyra"), 66)
        book.strike("Aldric")
        self.assertEqual(book.strike("Quill"), 1)  # three hits, still standing
        done = book.finish(True)
        self.assertEqual((done["status"], done["hp"], done["loot"]), ("victory", 0, 80))
        self.assertEqual(granted[0], ("boss", 80, "raid"))
        self.assertEqual({row[0] for row in granted}, {"boss", "id-Lyra", "id-Aldric", "id-Quill"})
        self.assertIsNone(book.finish(True))  # already over

    def test_a_failed_leader_ends_the_raid(self):
        book = raids.Raids(None)
        book.begin("boss", "A hard bug")
        run = type("Run", (), {"sid": "boss", "ok": False, "helper": False, "cwd": ""})()
        done = book.on_run(run)
        self.assertEqual(done["status"], "fled")
        self.assertEqual(done["hp"], 100)


class Routes(unittest.TestCase):
    def test_gold_is_readable_and_the_strongbox_is_not(self):
        self.assertTrue(server.token_route_allowed("GET", "/api/gold"))
        self.assertTrue(server.token_route_allowed("GET", "/api/raids"))
        self.assertFalse(server.token_route_allowed("POST", "/api/gold/release/abcdef01"))


if __name__ == "__main__":
    unittest.main()
