"""The handoff contract (RESULT lines), the Reviewer, the start checklist and the scorecard."""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chain  # noqa: E402
import handoff  # noqa: E402
import store  # noqa: E402
from test_chain import Run, Stub  # noqa: E402

PR = "https://github.com/Bojjifomo/Capstone1/pull/40"


class Parse(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(handoff.parse("fixed it\nRESULT: done - bot is up")["outcome"], "done")
        r = handoff.parse("**RESULT:** blocked — token expired, tried twice")
        self.assertEqual((r["outcome"], r["note"]), ("blocked", "token expired, tried twice"))
        self.assertEqual(handoff.parse("RESULT: needs you - pick an RPC plan")["outcome"], "needs-you")
        self.assertEqual(handoff.parse("result: Needs Bojjifomo: approve the cost")["outcome"], "needs-you")
        self.assertEqual(handoff.parse("RESULT: done\nlater\nRESULT: blocked - second thoughts")["outcome"], "blocked",
                         "the last line wins")
        self.assertEqual(handoff.parse("no line at all", ok=True), {"outcome": "done", "note": "", "stated": False})
        self.assertEqual(handoff.parse("", ok=False)["outcome"], "failed")
        quoted = "You said: check the bot" + handoff.CONTRACT[:200]
        self.assertEqual(handoff.parse(quoted)["outcome"], "done", "quoting the template is not an answer")
        self.assertEqual(handoff.parse(handoff.CONTRACT + "\nRESULT: blocked - real")["outcome"], "blocked")
        self.assertEqual(handoff.pr_urls("see %s and %s again, and https://github.com/a/b/issues/3" % (PR, PR)), [PR])


class ChainHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="handoff-test-"))
        self._data = store.DATA_DIR
        store.DATA_DIR = self.tmp
        self.project = self.tmp / "app"
        self.project.mkdir()
        self.office = Stub()
        self.c = chain.Chain(self.office, cli="office")
        self.c.appoint("ops", "aaaa0002")
        self.c.appoint("ceo", "aaaa0003")
        self.c.configure({"auto": True})

    def tearDown(self):
        store.DATA_DIR = self._data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_needs_you_skips_the_rest_of_the_ladder(self):
        i = self.c.add_intern("Bot", str(self.project), fresh="hb", fresh_hours=1, warden="aaaa0001")
        self.c.check_all()
        self.c.check_all()
        inc = self.c.open_incident(i["id"])
        self.assertEqual(self.office.team.asked[-1]["name"], "Lyra")
        self.c.on_run(Run(self.office.team.asked[-1]["sid"], True, "checked it\nRESULT: needs you - the bot token expired"))
        self.assertEqual(inc["status"], "needs-you")
        self.assertEqual(len(self.office.team.asked), 1, "nobody else is sent: only bojjifomo can renew a token")
        self.assertEqual(inc["attempts"][0]["outcome"], "needs-you")
        self.assertIn("token expired", self.office.notes[-1][1])

    def test_blocked_still_climbs(self):
        i = self.c.add_intern("Bot", str(self.project), fresh="hb", fresh_hours=1, warden="aaaa0001")
        self.c.check_all()
        self.c.check_all()
        self.c.on_run(Run(self.office.team.asked[-1]["sid"], True, "RESULT: blocked - cannot reach the RPC"))
        self.assertEqual(self.office.team.asked[-1]["name"], "Bram")

    def test_reviews(self):
        self.assertEqual(self.c.maybe_review(Run("s1", True, "opened " + PR)), [], "no Reviewer yet")
        self.c.appoint("reviewer", "aaaa0004")
        lyra = self.office.team.find("Lyra")
        started = self.c.maybe_review(Run("s1", True, "opened " + PR + "\nRESULT: done - fixed"), lyra)
        self.assertEqual([r["who"] for r in started], ["Orin"])
        brief = self.office.team.asked[-1]["brief"]
        self.assertIn(PR, brief)
        self.assertIn("never merge", brief)
        self.assertEqual(self.c.maybe_review(Run("s2", True, "again " + PR), lyra), [], "one review per PR a day")
        self.assertEqual(self.c.maybe_review(Run("s3", True, "mine " + PR.replace("40", "41")), self.office.team.find("Orin")), [],
                         "the Reviewer does not review their own pull request")
        failed = Run("s4", False, PR.replace("40", "42"))
        self.assertEqual(self.c.maybe_review(failed, lyra), [], "a failed run's link is not reviewed")
        sid = started[0]["sid"]
        self.c.on_run(Run(sid, True, "Looks fine.\nRESULT: blocked - the diff prints the RPC key"))
        rv = self.c.reviews[-1]
        self.assertEqual((rv["outcome"], rv["note"]), ("blocked", "the diff prints the RPC key"))
        self.assertIn("do not merge", self.office.notes[-1][0])
        with self.assertRaises(ValueError):
            self.c.request_review("https://example.com/pull/1")
        self.c.configure({"auto_review": False})
        self.assertEqual(self.c.maybe_review(Run("s5", True, PR.replace("40", "43")), lyra), [])
        self.assertTrue(self.c.request_review(PR.replace("40", "43"))["sid"], "a manual review still works")

    def test_start_steps_follow_the_real_state(self):
        steps = {s["id"]: s["done"] for s in self.c.start_steps()}
        self.assertFalse(steps["team"], "no Innovator or Reviewer yet")
        self.assertFalse(steps["interns"])
        self.assertTrue(steps["autopilot"])
        self.c.appoint("innovator", "aaaa0004")
        self.c.appoint("reviewer", "aaaa0004")
        self.c.add_intern("Bot", str(self.project), url="http://127.0.0.1:9/", warden="aaaa0001")
        steps = {s["id"]: s for s in self.c.start_steps()}
        self.assertTrue(steps["team"]["done"])
        self.assertTrue(steps["wardens"]["done"])
        self.assertFalse(steps["healthy"]["done"])
        self.assertIn("Check now", steps["healthy"]["how"])

    def test_scorecard(self):
        class Progress:
            actors = {"aaaa0001": {"history": [
                {"kind": "quest", "ts": time.time(), "ok": True, "cost": 0.10, "seconds": 120, "outcome": "done"},
                {"kind": "quest", "ts": time.time(), "ok": True, "cost": 0.30, "seconds": 240, "outcome": "needs-you"},
                {"kind": "quest", "ts": time.time() - 40 * 86400, "ok": False, "outcome": "failed"},
            ]}}
        self.office.progress = Progress()
        sc = self.c.scorecard(7)
        lyra = sc["people"][0]
        self.assertEqual((lyra["name"], lyra["runs"], lyra["done_pct"], lyra["avg_cost"], lyra["avg_minutes"]),
                         ("Lyra", 2, 50, 0.2, 3.0))
        self.assertEqual(lyra["outcomes"], {"done": 1, "needs-you": 1})
        self.assertIn("Lyra (sonnet): 2 runs, 50% done", self.c.scorecard_text(7))


if __name__ == "__main__":
    unittest.main()
