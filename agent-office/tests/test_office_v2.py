"""The office: health, limits, money, effort, eco for the whole office, team suggestions, the hire draft, programs, alerts."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402
import limits  # noqa: E402
import money  # noqa: E402
import roster  # noqa: E402
import system  # noqa: E402
import test_office  # noqa: E402
ServerCase = test_office.ServerCase


class Health(unittest.TestCase):
    def test_snapshot_has_the_basics_and_never_raises(self):
        s = system.snapshot(can_free=123)
        self.assertIn("disk", s)
        self.assertIn("cpu", s)
        self.assertEqual(s["can_free"], 123)
        self.assertGreater(s["disk"].get("total", 0), 0)
        again = system.snapshot()
        self.assertEqual(again["at"], s["at"], "cached for half a minute")


class Limits(unittest.TestCase):
    def test_parse_windows(self):
        out = limits.parse({"five_hour": {"utilization": 23.4, "resets_at": "2026-11-11T14:00:00Z"},
                            "seven_day": {"utilization": 180, "resets_at": 1794000000}, "junk": 1, "seven_day_opus": {"utilization": None}})
        self.assertEqual([w["key"] for w in out], ["five_hour", "seven_day"])
        self.assertEqual(out[0]["pct"], 23.4)
        self.assertEqual(out[1]["pct"], 100.0, "clamped")
        self.assertAlmostEqual(out[1]["resets"], 1794000000)
        self.assertIn("Session 23%", limits.text({"on": True, "windows": out, "error": ""}))

    def test_token_from_file_and_off_switch(self):
        tmp = Path(tempfile.mkdtemp())
        f = tmp / "creds.json"
        f.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat-test"}}))
        self.assertEqual(limits.token_from_file(f), "sk-ant-oat-test")
        self.assertEqual(limits.token_from_file(tmp / "missing.json"), "")
        self.assertEqual(limits.snapshot({"limits": False}), {"on": False, "windows": [], "checked": 0, "error": ""})
        with self.assertRaises(RuntimeError):
            limits.fetch({"credentials_file": str(tmp / "missing.json")})
        shutil.rmtree(tmp)


class Money(unittest.TestCase):
    def test_pick_reads_many_shapes(self):
        self.assertEqual(money.pick(12.5), ({"today": 12.5, "week": None, "month": None}, ""))
        v, note = money.pick({"today": "3.2", "7d": 20, "earned_month": 90, "note": "fees"})
        self.assertEqual(v, {"today": 3.2, "week": 20.0, "month": 90.0})
        self.assertEqual(note, "fees")
        v, _ = money.pick({"earnings": {"usd": {"today": 1}}}, path="earnings.usd")
        self.assertEqual(v["today"], 1.0)

    def test_sources_sum_and_errors_keep_last_values(self):
        import store
        old = store.DATA_DIR
        tmp = Path(tempfile.mkdtemp())
        store.DATA_DIR = tmp
        try:
            good = tmp / "good.json"
            good.write_text(json.dumps({"today": 10, "week": 40, "month": 100, "note": "ok"}))
            (tmp / "money.json").write_text(json.dumps({"fixed_monthly_usd": 30, "refresh_minutes": 15, "sources": [
                {"name": "Good", "file": str(good)},
                {"name": "Cmd", "cmd": [sys.executable, "-c", "print('{\"today\": 2.5}')"]},
                {"name": "Broken", "cmd": [sys.executable, "-c", "import sys; sys.exit(3)"]}]}))
            m = money.Money(None)
            m.refresh(wait=True)
            snap = m.snapshot()
            by = {r["name"]: r for r in snap["sources"]}
            self.assertEqual(by["Good"]["today"], 10)
            self.assertEqual(by["Cmd"]["today"], 2.5)
            self.assertIn("exit 3", by["Broken"]["error"])
            self.assertEqual(snap["earned"], {"today": 12.5, "week": 40.0, "month": 100.0})
            self.assertEqual(snap["fixed"], {"today": 1.0, "week": 7.0, "month": 30.0})
            self.assertEqual(snap["net"]["today"], 11.5)
            self.assertEqual(len(snap["errors"]), 1)
            # a broken source keeps what it had
            good.write_text("not json")
            m.refresh(wait=True)
            self.assertEqual({r["name"]: r for r in m.snapshot()["sources"]}["Good"]["today"], 10)
        finally:
            store.DATA_DIR = old
            shutil.rmtree(tmp)


class OfficeNames(unittest.TestCase):
    def test_divisions_have_office_names(self):
        self.assertEqual(roster.division_name("The Counting House"), "Finance")
        self.assertEqual(roster.division_name("Finance"), "Finance")
        self.assertEqual(roster.division_name(" Design "), "Design")
        self.assertTrue(all(p["division"] in roster.DIVISION_ORDER for p in roster.PRESETS.values()))
        self.assertGreaterEqual(len(roster.KNOWN_INTERNS), 9)


class OfficeRoutes(ServerCase):
    wait_run = test_office.Stage2.wait_run

    def test_state_carries_the_header(self):
        s = self.request("GET", "/api/state")[1]
        for key in ("system", "limits", "eco", "programs", "alerts"):
            self.assertIn(key, s)
        self.assertIn("disk", s["system"])
        self.assertEqual(s["eco"]["all"], False)
        self.assertTrue(any(a["kind"] == "needs" for a in s["alerts"]))
        self.assertTrue(any(a["kind"] == "bot" for a in s["alerts"]))
        o = self.request("GET", "/api/options")[1]
        self.assertEqual(o["efforts"], ["", "low", "medium", "high", "xhigh", "max"])
        self.assertIn("rows", self.request("GET", "/api/programs")[1])
        self.assertIn("alerts", self.request("GET", "/api/alerts")[1])

    def test_effort_reaches_claude(self):
        pid = [p for p in self.request("GET", "/api/projects")[1]["projects"] if p["label"].endswith("webapp")][0]["id"]
        status, r = self.post("/api/job", {"project": pid, "text": "tidy the readme", "mode": "acceptEdits", "effort": "high"})
        self.assertEqual(status, 200, r)
        self.assertIn("effort=high", self.wait_run(r["run"])["events"][0]["t"])
        self.assertEqual(self.post("/api/job", {"project": pid, "text": "x", "mode": "acceptEdits", "effort": "turbo"})[0], 409)
        sid = r["sid"]
        time.sleep(1.5)
        self.assertEqual(self.post("/api/session/%s/effort" % sid, {"effort": "low"})[0], 200)
        again = self.post("/api/session/%s/send" % sid, {"text": "and the changelog", "mode": "acceptEdits", "now": True})[1]
        self.assertIn("effort=low", self.wait_run(again["run"])["events"][0]["t"], "the saved effort sticks")
        detail = self.request("GET", "/api/session/" + sid)[1]
        self.assertEqual(detail["effort_pref"], "low")

    def test_eco_for_the_whole_office(self):
        self.assertEqual(self.post("/api/eco", {"on": True})[1]["all"], True)
        try:
            pid = [p for p in self.request("GET", "/api/projects")[1]["projects"] if p["label"].endswith("webapp")][0]["id"]
            r = self.post("/api/job", {"project": pid, "text": "rename a helper", "mode": "acceptEdits", "model": "haiku"})[1]
            self.assertIn("model=opus eco=yes", self.wait_run(r["run"])["events"][0]["t"])
            self.assertTrue(self.request("GET", "/api/state")[1]["eco"]["all"])
            reply = self.post("/api/chat", {"text": "/eco"})[1]["reply"]
            self.assertIn("Eco on", reply)
        finally:
            self.assertEqual(self.post("/api/chat", {"text": "/eco off"})[1]["reply"][:7], "Eco off")

    def test_team_suggest_draft_and_hire_auto(self):
        hired = self.post("/api/staff", {"name": "Vega", "role": "Backend engineer", "instructions": "Python APIs.", "model": "sonnet", "effort": "high"})
        self.assertEqual(hired[0], 200, hired[1])
        self.assertEqual(hired[1]["staff"]["effort"], "high")
        s = self.post("/api/team/suggest", {"text": "add an endpoint to the Python API"})[1]
        self.assertIn("Vega", s["names"])
        d = self.post("/api/team/draft", {"role": "Copy Trading Specialist"})[1]
        self.assertEqual(d["name"], "Hyperion")
        self.assertEqual(d["division"], "Finance")
        self.assertEqual(d["effort"], "high")
        self.assertIn("drafted by sonnet", d["reason"])
        auto = self.post("/api/staff", {"role": "Copy Trading Specialist", "model": "auto"})
        self.assertEqual(auto[0], 200, auto[1])
        self.assertTrue(auto[1]["drafted"])
        self.assertEqual(auto[1]["staff"]["name"], "Hyperion")
        self.assertEqual(auto[1]["staff"]["model"], "sonnet")
        eff = self.post("/api/team/effort", {"name": "Hyperion", "effort": "max"})[1]
        self.assertEqual((eff["was"], eff["effort"]), ("high", "max"))
        self.assertEqual(self.post("/api/team/effort", {"name": "Hyperion", "effort": "nope"})[0], 400)
        pid = [p for p in self.request("GET", "/api/projects")[1]["projects"] if p["label"].endswith("webapp")][0]["id"]
        r = self.post("/api/job", {"project": pid, "text": "ship it", "mode": "acceptEdits", "team": ["Vega", "nobody"]})[1]
        self.assertIn("team=yes", self.wait_run(r["run"])["events"][0]["t"])
        m = self.request("GET", "/api/money")[1]
        self.assertFalse(m["configured"])
        self.assertIn("api_prices", m)
        self.assertEqual(self.post("/api/money/refresh", {})[0], 200)


if __name__ == "__main__":
    unittest.main()
