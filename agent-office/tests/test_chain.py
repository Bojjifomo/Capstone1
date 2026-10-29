"""Chain of command: interns, health checks, incidents going up the chain, rounds, the CEO taking over."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chain  # noqa: E402
import store  # noqa: E402


class Run:
    def __init__(self, sid, ok=True, final="done"):
        self.sid, self.ok, self.final = sid, ok, final


class StubTeam:
    def __init__(self):
        self.staff = [
            {"id": "aaaa0001", "name": "Lyra", "title": "Keeper", "kind": "cleric", "seed": "1", "model": "sonnet"},
            {"id": "aaaa0002", "name": "Bram", "title": "Marshal", "kind": "cleric", "seed": "2", "model": "sonnet"},
            {"id": "aaaa0003", "name": "Aurelian", "title": "Regent", "kind": "knight", "seed": "3", "model": "opus"},
            {"id": "aaaa0004", "name": "Orin", "title": "Seer", "kind": "ranger", "seed": "4", "model": "sonnet"},
        ]
        self.asked = []
        self.fail_next = False

    def find(self, key):
        key = str(key).lower()
        return next((s for s in self.staff if s["id"] == key or s["name"].lower() == key), None)

    def hire(self, name, role, instructions, division, model):
        member = {"id": "bbbb%04d" % len(self.staff), "name": name, "title": "New", "kind": "knight", "seed": "x",
                  "model": model}
        self.staff.append(member)
        return member, ""

    def ask(self, key, brief, origin="", mode=None, add_dirs=()):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("claude was not found")
        member = self.find(key)
        sid = "%08d-0000-0000-0000-000000000000" % (len(self.asked) + 1)
        self.asked.append({"name": member["name"], "brief": brief, "mode": mode, "add_dirs": list(add_dirs), "sid": sid})
        return {"sid": sid, "run": "r%011d" % len(self.asked), "name": member["name"]}


class Stub:
    def __init__(self):
        self.team = StubTeam()
        self.notes, self.events, self.lines = [], [], []
        self.jobs = []
        self.paused = False
        office = self

        class Familiars:
            def jobs(self):
                return office.jobs

        class Economy:
            def snapshot(self):
                return {"paused": office.paused, "budget": 0, "spent": 0}

        class Ledger:
            def add(self, kind, text, **extra):
                office.lines.append(text)

            def recent(self, n=10):
                return [{"kind": "guild", "text": t} for t in office.lines[-n:]]

        class Petitions:
            def pending_count(self):
                return 1

        class Runner:
            stopped = []

            def active(self, sid):
                return False

            def stop(self, rid):
                self.stopped.append(rid)

        self.familiars, self.economy, self.ledger = Familiars(), Economy(), Ledger()
        self.petitions, self.runner = Petitions(), Runner()

    def notify(self, title, body, tag="", url="/"):
        self.notes.append((title, body))

    def emit(self, event, data):
        self.events.append(event)


class ChainCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="chain-test-"))
        self._data = store.DATA_DIR
        store.DATA_DIR = self.tmp
        self.project = self.tmp / "wallet-finder"
        (self.project / "data").mkdir(parents=True)
        self.db = self.project / "data" / "wallets.db"
        self.db.write_text("x")
        self.office = Stub()
        self.c = chain.Chain(self.office, cli="office")
        self.c.appoint("ops", "aaaa0002")
        self.c.appoint("ceo", "aaaa0003")
        self.intern = self.c.add_intern("Wallet Finder", str(self.project), fresh="data/wallets.db",
                                        fresh_hours=1, warden="aaaa0001")

    def tearDown(self):
        store.DATA_DIR = self._data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stale(self):
        old = time.time() - 3 * 3600
        os.utime(self.db, (old, old))

    def fresh(self):
        os.utime(self.db, None)

    def open_incident(self):
        self.stale()
        self.c.check_all()
        self.assertIsNone(self.c.open_incident(self.intern["id"]), "one failed check is not an incident yet")
        self.c.check_all()
        inc = self.c.open_incident(self.intern["id"])
        self.assertIsNotNone(inc)
        return inc

    def finish_last(self, ok=True, final="tried a restart"):
        self.c.on_run(Run(self.office.team.asked[-1]["sid"], ok, final))

    # ---- interns ------------------------------------------------------------------
    def test_intern_needs_a_check_and_a_folder_inside(self):
        with self.assertRaises(ValueError):
            self.c.add_intern("Nothing", str(self.project))
        with self.assertRaises(ValueError):
            self.c.add_intern("Escape", str(self.project), fresh="../../etc/passwd")
        with self.assertRaises(ValueError):
            self.c.add_intern("Gone", str(self.tmp / "nope"), url="http://127.0.0.1:9/")
        with self.assertRaises(ValueError):
            self.c.add_intern("Ftp", str(self.project), url="file:///etc/passwd")

    def test_launchd_checks(self):
        i = self.c.add_intern("Worker", str(self.project), launchd="com.x.worker")
        self.assertIn("not installed", self.c.check(i)["reasons"][0])
        self.office.jobs = [{"label": "com.x.worker", "loaded": True, "failed": True, "exit": "1", "error": "boom"}]
        self.assertIn("last exited 1: boom", self.c.check(i)["reasons"][0])
        self.office.jobs = [{"label": "com.x.worker", "loaded": True, "failed": False, "exit": "0", "error": ""}]
        self.assertTrue(self.c.check(i)["ok"])

    # ---- escalation ------------------------------------------------------------------
    def test_incident_climbs_warden_ops_ceo_you(self):
        inc = self.open_incident()
        asked = self.office.team.asked
        self.assertEqual(asked[-1]["name"], "Lyra")
        self.assertEqual(asked[-1]["mode"], "auto")
        self.assertEqual(asked[-1]["add_dirs"], [str(self.project)])
        self.assertIn("data/wallets.db is", asked[-1]["brief"])
        self.assertIn("incident.opened", self.office.events)

        self.finish_last()          # Lyra finished, still stale -> ops
        self.assertEqual(asked[-1]["name"], "Bram")
        self.assertIn("Lyra", asked[-1]["brief"], "the next rung sees what was tried")
        self.finish_last(ok=False)  # ops failed -> CEO
        self.assertEqual(asked[-1]["name"], "Aurelian")
        self.finish_last()          # CEO could not fix it -> you
        self.assertEqual(len(asked), 3)
        self.assertEqual(inc["status"], "needs-you")
        self.assertIn("needs your word", self.office.notes[-1][0])

    def test_fix_resolves_the_incident(self):
        inc = self.open_incident()
        self.fresh()
        self.finish_last(final="re-ran the sync")
        self.assertEqual(inc["status"], "resolved")
        self.assertEqual(inc["resolved_by"], "Lyra")
        self.assertIn("incident.resolved", self.office.events)
        self.assertEqual(len(self.office.team.asked), 1)

    def test_recovering_on_its_own_resolves(self):
        inc = self.open_incident()
        self.c.runs.clear()
        inc["attempts"][-1]["ended"] = time.time()
        self.fresh()
        self.c.check_all()
        self.assertEqual(inc["status"], "resolved")

    def test_no_warden_goes_straight_to_ops(self):
        self.c.update_intern(self.intern["id"], warden="")
        self.open_incident()
        self.assertEqual(self.office.team.asked[-1]["name"], "Bram")

    def test_ops_down_is_skipped(self):
        self.c.rounds["ops"] = {"started": time.time(), "ok": False}
        self.open_incident()
        self.finish_last()
        self.assertEqual(self.office.team.asked[-1]["name"], "Aurelian")

    def test_a_dispatch_that_cannot_start_moves_up(self):
        self.office.team.fail_next = True
        inc = self.open_incident()
        self.assertEqual(self.office.team.asked[-1]["name"], "Bram")
        self.assertIn("could not start", inc["attempts"][0]["summary"])

    def test_empty_treasury_goes_to_you(self):
        self.office.paused = True
        inc = self.open_incident()
        self.assertEqual(self.office.team.asked, [])
        self.assertEqual(inc["status"], "needs-you")
        self.assertIn("treasury", self.office.notes[-1][1])

    def test_daily_limit(self):
        self.c.configure({"max_auto_per_day": 1})
        inc = self.open_incident()
        self.finish_last()
        self.assertEqual(len(self.office.team.asked), 1)
        self.assertEqual(inc["status"], "needs-you")
        self.assertTrue(any("limit" in n[0] for n in self.office.notes))

    def test_autopilot_off_only_opens(self):
        self.c.configure({"auto": False})
        inc = self.open_incident()
        self.assertEqual(self.office.team.asked, [])
        self.assertEqual(inc["status"], "open")
        result = self.c.escalate(inc["id"])
        self.assertEqual(result["who"], "Lyra")

    def test_stuck_warden_is_stopped_and_sent_up(self):
        inc = self.open_incident()
        inc["attempts"][-1]["started"] = time.time() - 3 * 3600
        self.c.tick()
        self.assertTrue(self.office.runner.stopped)
        self.assertIn("stuck", inc["attempts"][0]["summary"])
        self.assertEqual(inc["attempts"][-1]["who"], "Bram")

    def test_manual_resolve(self):
        inc = self.open_incident()
        with self.assertRaises(RuntimeError):
            self.c.escalate(inc["id"])  # Lyra is still on it
        self.c.resolve(inc["id"], "restarted it by hand")
        self.assertEqual(inc["status"], "resolved")

    # ---- rounds -------------------------------------------------------------------------
    def test_ops_round_is_due_and_its_failure_calls_the_ceo(self):
        self.c.tick()
        ops = self.office.team.asked[-1]
        self.assertEqual(ops["name"], "Bram")
        self.assertIn("Wallet Finder", ops["brief"])
        self.assertEqual(ops["add_dirs"], [str(self.project)])
        self.c.on_run(Run(ops["sid"], ok=False, final="API error"))
        self.assertEqual(self.office.team.asked[-1]["name"], "Aurelian")
        self.assertIn("API error", self.office.team.asked[-1]["brief"])
        self.assertIn("ops.down", self.office.events)
        self.assertTrue(self.c.ops_down())

    def test_stuck_round_is_stopped_and_taken_over(self):
        self.c.tick()
        self.c.rounds["ops"]["started"] = time.time() - 3 * 3600
        self.c.tick()
        self.assertTrue(self.office.runner.stopped)
        self.assertEqual(self.office.team.asked[-1]["name"], "Aurelian")

    def test_ceo_acts_as_ops_when_ops_is_vacant(self):
        self.c.appoint("ops", "")
        self.c.tick()
        self.assertEqual(self.office.team.asked[-1]["name"], "Aurelian")
        self.assertIn("acting as one", self.office.team.asked[-1]["brief"])

    def test_innovator_review(self):
        self.c.appoint("innovator", "aaaa0004")
        self.c.tick()
        names = [a["name"] for a in self.office.team.asked]
        self.assertIn("Orin", names)
        brief = next(a["brief"] for a in self.office.team.asked if a["name"] == "Orin")
        self.assertIn("propose", brief)

    def test_recruit_reuses_orin_and_hires_missing(self):
        self.assertEqual(self.c.recruit("innovator")["name"], "Orin")
        self.c.appoint("ceo", "")
        member = self.c.recruit("ceo")
        self.assertEqual(self.c.holder("ceo")["id"], member["id"])
        self.assertIn("Warden of Wallet Finder", self.c.role_of("aaaa0001"))

    def test_state_survives_a_restart(self):
        self.open_incident()
        again = chain.Chain(self.office)
        self.assertEqual(again.intern("wallet finder")["warden"], "aaaa0001")
        self.assertEqual(len(again.incidents), 1)
        self.assertEqual(again.holder("ceo")["name"], "Aurelian")


if __name__ == "__main__":
    unittest.main()
