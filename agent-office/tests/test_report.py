"""Usage report, the CEO's model controls, and rationing."""
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chain  # noqa: E402
import report  # noqa: E402
import store  # noqa: E402
import team  # noqa: E402
from test_chain import Run, Stub  # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")


class T:
    def __init__(self, sid, cwd, usage):
        self.id, self.cwd, self.usage, self.mtime = sid, cwd, usage, time.time()


class Scanner:
    def __init__(self, transcripts):
        self.lock = threading.Lock()
        self.transcripts = {t.id: t for t in transcripts}

    def refresh(self, min_interval=None):
        pass


def office_with(transcripts, budget=0, spent=0):
    office = Stub()
    office.scanner = Scanner(transcripts)
    office.name_of = lambda t: "session " + t.id[:4]
    office.team.member_for_cwd = lambda cwd: next((s for s in office.team.staff if s.get("folder") == cwd), None)
    office.economy.snapshot = lambda: {"paused": False, "budget": budget, "spent": spent}
    return office


def row(model_cost, i=1_000_000, o=100_000, cr=0, cw=0):
    return [i, o, cr, cw, model_cost]


class ReportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="report-test-"))
        self._data = store.DATA_DIR
        store.DATA_DIR = self.tmp

    def tearDown(self):
        store.DATA_DIR = self._data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, budget=0, spent=0):
        # 1M in + 100k out: Opus 5.5 costs 4 + 2 = $6, Sonnet 5 costs 2 + 1 = $3, Haiku 5 costs 0.10 + 0.05
        office = office_with([
            T("11111111-0000-0000-0000-000000000000", "/agents/lyra",
              {TODAY: {"claude-opus-5-5": row(6.0)}}),
            T("22222222-0000-0000-0000-000000000000", "/agents/bram",
              {TODAY: {"claude-sonnet-5-5": row(3.0)}}),
            T("33333333-0000-0000-0000-000000000000", "/home/me/Capstone1",
              {TODAY: {"claude-sonnet-5-5": row(1.0)}, "1999-01-01": {"claude-opus-5-5": row(99.0)}}),
        ], budget, spent)
        office.team.staff[0]["folder"] = "/agents/lyra"
        office.team.staff[1]["folder"] = "/agents/bram"
        office.chain = chain.Chain(office, cli="office")
        office.chain.appoint("ops", "aaaa0002")
        return office, report.build(office, 7)

    def test_totals_ignore_old_days_and_group_by_kind(self):
        office, r = self.build()
        self.assertEqual(r["total"], 10.0)
        self.assertEqual(r["today"], 10.0)
        kinds = {k["kind"]: k["cost"] for k in r["kinds"]}
        self.assertEqual(kinds, {"you": 1.0, "chain": 3.0, "guild": 6.0})
        self.assertEqual({m["family"]: m["cost"] for m in r["models"]}, {"opus": 6.0, "sonnet": 4.0})
        self.assertEqual(r["actors"][0]["name"], "Lyra")
        self.assertEqual(r["actors"][0]["share"], 0.6)
        self.assertEqual(r["projects"][0]["name"], "lyra")
        self.assertEqual(r["projected_month"], round(10 / 7 * 30, 2))

    def test_ideas_are_priced_from_the_same_tokens(self):
        office, r = self.build()
        opus = next(i for i in r["ideas"] if i["id"] == "opus-Lyra")
        self.assertEqual(opus["saving"], 3.0)              # $6 on Opus vs $3 for the same tokens on Sonnet
        self.assertEqual(opus["action"], {"staff": "aaaa0001", "model": "sonnet"})
        self.assertEqual(opus["kind"], "likely")
        self.assertFalse(any(i["id"] == "haiku-Bram" for i in r["ideas"]), "the operations manager stays on Sonnet")
        self.assertTrue(any(i["id"] == "top-Lyra" for i in r["ideas"]), "60% of the spend in one place")
        self.assertEqual(r["potential"], round(sum(i["saving"] for i in r["ideas"]), 2))

    def test_text_for_the_ceo(self):
        office, r = self.build()
        text = report.text(r)
        self.assertIn("Lyra [guild", text)
        self.assertIn("Bram [chain", text)
        self.assertIn("Opus seats: 1 of 2", text)
        self.assertIn("Saves about $3.00", text)

    def test_seats_and_ceo_are_protected(self):
        office = Stub()
        office.team = team.Team.__new__(team.Team)
        office.team.office, office.team.lock = office, threading.Lock()
        office.team.staff = [{"id": "a%07d" % i, "name": n, "model": m, "folder": "/f/" + n, "role": "x", "title": "t",
                              "division": "d", "instructions": "", "kind": "knight", "seed": "s"}
                             for i, (n, m) in enumerate([("Ceo", "opus"), ("Ann", "opus"), ("Bob", "sonnet")])]
        office.team.divisions = []
        office.team.save = lambda: None
        office.chain = chain.Chain(office, cli="office")
        office.chain.roles["ceo"] = office.team.staff[0]["id"]
        with self.assertRaises(RuntimeError):
            office.team.set_model("Bob", "opus")           # both seats taken
        office.team.set_model("Ann", "sonnet")
        self.assertEqual(office.team.set_model("Bob", "opus")[1], "sonnet")
        with self.assertRaises(PermissionError):
            office.team.set_model("Ceo", "haiku", by="aurelian")
        self.assertEqual(office.team.set_model("Ceo", "sonnet", by="bojjifomo")[0]["model"], "sonnet")
        with self.assertRaises(ValueError):
            office.team.set_model("Ann", "gpt")
        with self.assertRaises(LookupError):
            office.team.set_model("Nobody", "haiku")

    def test_rationing_stops_rounds_but_not_incidents(self):
        office, r = self.build(budget=10, spent=8.5)
        c = office.chain
        c.appoint("ceo", "aaaa0003")
        self.assertTrue(c.guarded())
        self.assertTrue(any("rationing" in n[0].lower() for n in office.notes))
        c.tick()
        self.assertEqual(office.team.asked, [], "no automatic round while rationing")
        c.guarded()
        self.assertEqual(sum("rationing" in n[0].lower() for n in office.notes), 1, "told once a day")
        office.economy.snapshot = lambda: {"paused": False, "budget": 10, "spent": 1}
        self.assertFalse(c.guarded())
        c.tick()
        self.assertTrue(office.team.asked, "rounds resume under the line")

    def test_budget_review_gets_the_report_and_runs_daily(self):
        office, r = self.build()
        c = office.chain
        c.appoint("ceo", "aaaa0003")
        c.configure({"review_hours": 12})
        c.tick()
        brief = next(a["brief"] for a in office.team.asked if a["brief"].startswith("You are the CEO of bojjifomo's guild, and you keep"))
        self.assertIn("Lyra [guild", brief)
        self.assertIn("office model <name>", brief)
        asked = len(office.team.asked)
        c.tick()
        self.assertEqual(len(office.team.asked), asked, "not again until review_hours pass")
        c.configure({"review_hours": 0})
        c.rounds["budget"]["started"] = time.time() - 99 * 3600
        c.tick()
        self.assertEqual(len(office.team.asked), asked, "0 turns the review off")
        sid = next(a["sid"] for a in office.team.asked if "keep the purse" in a["brief"] or "you keep" in a["brief"])
        c.on_run(Run(sid, True, "Moved Lyra to Sonnet."))
        self.assertEqual(c.public()["people"]["ceo"]["review"]["summary"], "Moved Lyra to Sonnet.")


if __name__ == "__main__":
    unittest.main()
