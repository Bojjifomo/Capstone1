"""RPG pack: rarity, XP, ranks, badges."""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import petitions  # noqa: E402
import progress  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402


class DataCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ao-rpg-"))
        self.old = store.DATA_DIR
        store.DATA_DIR = self.tmp

    def tearDown(self):
        store.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)


def quest(ok=True, cost=0.01, text="fix the gate", seconds=30):
    now = time.time()
    return type("Run", (), {"ok": ok, "cost": cost, "text": text, "cwd": "", "sid": "sid-1",
                            "started": now - seconds, "ended": now})()


class Ranks(DataCase):
    def test_rarity_and_titles(self):
        self.assertEqual(progress.rarity_of("high", "L"), "Legendary")
        self.assertEqual(progress.rarity_of("high", "M"), "Epic")
        self.assertEqual(progress.rarity_of("medium", "L"), "Epic")
        self.assertEqual(progress.rarity_of("high", "S"), "Rare")
        self.assertEqual(progress.rarity_of("low", "S"), "Common")
        self.assertEqual(progress.rarity_of("", ""), "Common")
        self.assertEqual(progress.xp_for("Epic", "approve"), 30)
        self.assertEqual(progress.titled("Journeyman Adventurer", "Apprentice"), "Apprentice Adventurer")
        self.assertEqual(progress.titled("Artificer of the Deep Halls", "Adept"), "Adept Artificer of the Deep Halls")
        self.assertEqual(progress.band(0)[0], "Apprentice")
        self.assertEqual(progress.band(40)[0], "Journeyman")
        self.assertEqual(progress.band(120)[0], "Adept")
        self.assertEqual(progress.band(300)[0], "Master")
        self.assertEqual(progress.band(300)[3], "")

    def test_quest_xp_badges_and_stats(self):
        book = progress.Progress(None)
        badges = book.on_quest(quest(cost=0), None)
        self.assertEqual(badges, ["first-quest", "zero-cost"])
        view = book.public("sid-1")
        self.assertEqual((view["xp"], view["rank"], view["level"]), (10, "Apprentice", 1))
        self.assertEqual(view["success"], 100)
        self.assertEqual(view["efficiency"], 100)
        self.assertEqual(view["badges"][0]["label"], "First Quest")
        book.on_quest(quest(ok=False, cost=0.2), None)
        self.assertEqual(book.public("sid-1")["success"], 50)
        self.assertEqual(book.on_quest(quest(cost=0), None), [])  # badges do not repeat
        for _ in range(8):
            book.grant("sid-1", "Ada", 10, "quest", "another", "Common", ok=True, cost=0.05, seconds=10)
        self.assertIn("ten-quests", book.actors["sid-1"]["badges"])
        # 10 + 0 + 10 + 8*10 = 100, Journeyman starts at 40
        self.assertEqual(book.public("sid-1")["rank"], "Journeyman")

    def test_approval_and_merge_stack(self):
        book = progress.Progress(None)
        item = {"by": "Orin", "title": "Watchlist alerts", "impact": "high", "effort": "L", "sid": "builder"}
        book.on_decision(item, "reject")
        self.assertEqual(book.actors, {})
        badges = book.on_decision(item, "approve")
        self.assertEqual(badges, ["petition-sealed"])
        self.assertEqual(book.public("by:orin")["xp"], 60)  # half of legendary 120
        merged = book.on_merged(item)
        self.assertEqual(merged, ["first-pr"])
        self.assertEqual(book.public("builder")["xp"], 120)
        self.assertEqual(book.public("builder")["rank"], "Adept")

    def test_board_lists_open_petitions_only(self):
        desk = petitions.Petitions({"petition_expire_days": 0})
        pending = desk.add("Orin", "Ship the bell", "Hall", "", "Plan.", impact="high", effort="M")
        other = desk.add("Orin", "Later", "Other", "", "Plan.", impact="low", effort="S")
        desk.decide(other["id"], "reject")
        rows = progress.Progress(None).board(desk)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["id"], rows[0]["rarity"], rows[0]["xp"]), (pending["id"], "Epic", 60))
        shown = progress.Progress(None).decorate(pending)
        self.assertNotIn("plan", {k: shown[k] for k in ("rarity", "xp")})
        self.assertEqual(shown["plan"][:4], "Plan")


class Routes(unittest.TestCase):
    def test_read_routes_are_on_the_token_list(self):
        self.assertTrue(server.token_route_allowed("GET", "/api/quests"))
        self.assertTrue(server.token_route_allowed("GET", "/api/progress"))
        self.assertFalse(server.token_route_allowed("POST", "/api/progress"))


if __name__ == "__main__":
    unittest.main()
