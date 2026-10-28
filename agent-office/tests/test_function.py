"""Function pack: webhooks, petition lifecycle, Quill's read-only brief."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

import briefing  # noqa: E402
import petitions  # noqa: E402
import prwatch  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402
import team  # noqa: E402
import webhooks  # noqa: E402


class DataCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ao-fn-"))
        self.old = store.DATA_DIR
        store.DATA_DIR = self.tmp
        prwatch.clear_cache()

    def tearDown(self):
        store.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)


class PetitionLifecycle(DataCase):
    def book(self, **cfg):
        return petitions.Petitions(cfg)

    def test_near_duplicate_same_app_is_refused(self):
        book = self.book()
        book.add("Orin", "Watchlist alerts", "RH Wallet Finder", "", "Plan one.")
        with self.assertRaises(ValueError):
            book.add("Orin", "Watchlist alert", "rh wallet finder", "", "Plan two.")
        book.add("Orin", "Watchlist alerts", "Other App", "", "Different app, same words.")
        book.add("Orin", "Dark mode for the site", "RH Wallet Finder", "", "Not the same petition.")
        self.assertEqual(book.pending_count(), 3)

    def test_settled_duplicate_can_be_filed_again(self):
        book = self.book()
        item = book.add("Orin", "Watchlist alerts", "Hall", "", "Plan.")
        book.decide(item["id"], "reject", "not now")
        again = book.add("Orin", "Watchlist alerts", "Hall", "", "Plan, revised.")
        self.assertEqual(again["status"], "pending")

    def test_pending_petitions_expire(self):
        book = self.book(petition_expire_days=14)
        item = book.add("Orin", "Old idea", "Hall", "", "Plan.")
        item["created"] = time.time() - 15 * 86400
        book.items[0]["created"] = item["created"]
        expired = book.expire()
        self.assertEqual(expired[0]["status"], "expired")
        self.assertEqual(book.pending_count(), 0)
        self.assertEqual(book.expire(), [])
        off = self.book(petition_expire_days=0)
        kept = off.add("Orin", "Stays", "Hall", "", "Plan.")
        kept_id = kept["id"]
        off.items[0]["created"] = time.time() - 90 * 86400
        self.assertEqual(off.expire(), [])
        self.assertEqual(off.get(kept_id)["status"], "pending")

    def test_cap_drops_settled_before_live(self):
        book = self.book()
        now = time.time()
        for i in range(petitions.CAP):
            book.items.append({"id": "r%03d" % i, "title": "old %d" % i, "app": "Hall", "status": "rejected",
                                "created": now - 1000 + i, "by": "x", "note": "", "plan": "p", "sid": ""})
        live = book.add("Orin", "Keep me", "Hall", "", "Still open.")
        self.assertEqual(len(book.items), petitions.CAP)
        self.assertTrue(any(p["id"] == live["id"] and p["status"] == "pending" for p in book.items))
        self.assertFalse(any(p["id"] == "r000" for p in book.items))

    def test_build_failure_and_pr_poll(self):
        book = self.book()
        item = book.add("Orin", "Ship the bell", "Hall", "", "Plan.")
        book.decide(item["id"], "approve")
        book.mark_building(item["id"], "11111111-1111-1111-1111-111111111111")
        failed = book.finish_build("11111111-1111-1111-1111-111111111111", False, "the tests exploded")
        self.assertEqual(failed["status"], "failed")

        item = book.add("Orin", "Ship the other bell", "Hall", "", "Plan.")
        book.decide(item["id"], "approve")
        book.mark_building(item["id"], "22222222-2222-2222-2222-222222222222")
        url = "https://github.com/Bojjifomo/Capstone1/pull/21"
        held = book.finish_build("22222222-2222-2222-2222-222222222222", True, "Opened " + url)
        self.assertEqual((held["status"], held["pr_url"], held["quest_ok"]), ("building", url, True))
        self.assertNotIn("plan", petitions.public(held))

        def fetch(target):
            self.assertEqual(target, url)
            return "merged"

        closed = book.poll_prs(fetch)
        self.assertEqual(closed[0]["status"], "merged")
        self.assertEqual(book.poll_prs(lambda u: "closed"), [])

    def test_pr_classify_and_cache(self):
        url = "https://github.com/Bojjifomo/Capstone1/pull/21"
        self.assertEqual(prwatch.find_url("see " + url + " thanks"), url)
        self.assertEqual(prwatch.classify_gh({"state": "MERGED"}), "merged")
        self.assertEqual(prwatch.classify_gh({"state": "CLOSED"}), "closed")
        self.assertEqual(prwatch.classify_api({"state": "closed", "merged": True}), "merged")
        self.assertEqual(prwatch.classify_api({"state": "open"}), "open")

        calls = {"n": 0}

        def runner(argv):
            calls["n"] += 1
            self.assertEqual(argv[0], "gh")
            self.assertNotIn("--yes", argv)
            return 0, json.dumps({"state": "OPEN"})

        self.assertEqual(prwatch.fetch(url, runner=runner, now=1000), "open")
        self.assertEqual(prwatch.fetch(url, runner=runner, now=1100), "open")
        self.assertEqual(calls["n"], 1)
        prwatch.clear_cache()

        def boom(argv):
            raise OSError("no gh")

        def opener(target):
            self.assertIn("/pulls/21", target)
            return json.dumps({"state": "closed", "merged_at": "2020-01-01T00:00:00Z"})

        self.assertEqual(prwatch.fetch(url, runner=boom, opener=opener, use_cache=False), "merged")
        self.assertIsNone(prwatch.fetch("https://example.com/not-a-pr", use_cache=False))


class WebhookDelivery(unittest.TestCase):
    def test_posts_signature_and_logs_failure(self):
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                hits.append((dict(self.headers), self.rfile.read(length)))
                self.send_response(204)
                self.end_headers()

            def log_message(self, fmt, *args):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        logs = []
        secret = "test-secret"
        hooks = webhooks.Webhooks({
            "webhooks": [
                {"url": "http://127.0.0.1:%d/guild?token=hide-me" % port, "secret": secret, "header": "X-Guild-Token"},
                {"url": "http://127.0.0.1:%d/only-errors" % port, "events": ["familiar.error"]},
                {"url": "http://127.0.0.1:1/down"},
                {"url": "file:///tmp/nope"},
            ]
        }, log=logs.append, timeout=2)
        hooks.fire("petition.added", {"id": "abcd", "title": "A bell", "plan": "should not be required"})
        hooks.wait(3)
        self.assertEqual(len(hits), 1)
        header, body = hits[0]
        payload = json.loads(body)
        self.assertEqual(payload["event"], "petition.added")
        self.assertEqual(payload["source"], "agent-office")
        self.assertEqual(payload["data"]["title"], "A bell")
        self.assertEqual(header["X-Guild-Signature"], webhooks.sign(secret, body))
        self.assertEqual(header["X-Guild-Token"], secret)
        self.assertTrue(logs)
        self.assertIn("event=petition.added", logs[0])
        self.assertNotIn("hide-me", logs[0])
        self.assertNotIn(secret, logs[0])


class QuillBrief(DataCase):
    def test_command_is_read_only_and_refuses_yes(self):
        desk = briefing.Briefing({})
        argv, cwd, env = desk.command()
        self.assertEqual(argv[-2:], ["orchestrator", "brief"])
        self.assertNotIn("--yes", argv)
        self.assertEqual(env["ORCHESTRATOR_READ_ONLY"], "1")
        self.assertTrue((Path(cwd) / "orchestrator" / "__main__.py").is_file())
        refused = briefing.Briefing({"orchestrator_cmd": [sys.executable, "-c", "print(1)", "--yes"]})
        with self.assertRaises(RuntimeError):
            refused.command()

    def test_brief_runs_and_records_output(self):
        script = self.tmp / "fake_brief.py"
        script.write_text(
            "import os\n"
            "print('READ_ONLY=' + os.environ.get('ORCHESTRATOR_READ_ONLY', ''))\n"
            "print('hello from quill')\n"
        )
        phases = []
        desk = briefing.Briefing(
            {"orchestrator_cmd": [sys.executable, str(script)], "orchestrator_name": "Quill"},
            on_event=lambda phase, state: phases.append(phase),
        )
        started = desk.start()
        self.assertTrue(started["briefing"])
        for _ in range(50):
            if not desk.public()["running"]:
                break
            time.sleep(0.05)
        report = desk.public()
        self.assertFalse(report["running"])
        self.assertTrue(report["ok"])
        self.assertIn("READ_ONLY=1", report["text"])
        self.assertIn("hello from quill", report["text"])
        self.assertEqual(phases, ["started", "finished"])
        saved = json.loads((self.tmp / "orchestrator.json").read_text())
        self.assertIn("hello from quill", saved["text"])

    def test_resident_is_findable_and_not_ranked(self):
        office = type("Office", (), {})()
        office.agents_dir = str(self.tmp / "Agents")
        office.cfg = {}
        office.briefing = briefing.Briefing({})
        guild = team.Team(office)
        member = guild.find("Quill")
        self.assertTrue(member["builtin"])
        self.assertEqual(member["division"], "The Counting House")
        self.assertEqual(guild.find("b71ef001")["id"], briefing.ORCH_ID)
        self.assertFalse(guild.dismiss("b71ef001"))
        self.assertEqual(guild.rank("research the best LP fee pools"), [])


class ReadOnlyTools(unittest.TestCase):
    def test_read_only_hides_write_tools(self):
        from orchestrator.agent import select_tools
        from orchestrator.tools import all_tools
        tools = all_tools()
        self.assertTrue(any(tool.risk == "write" for tool in tools))
        with mock.patch.dict(os.environ, {"ORCHESTRATOR_READ_ONLY": "1"}):
            picked = select_tools(tools)
        self.assertTrue(picked)
        self.assertTrue(all(tool.risk == "read" for tool in picked))
        self.assertLess(len(picked), len(tools))


class TokenRoutes(unittest.TestCase):
    def test_brief_read_is_allowed_and_start_is_not(self):
        self.assertTrue(server.token_route_allowed("GET", "/api/orchestrator"))
        self.assertFalse(server.token_route_allowed("POST", "/api/orchestrator/brief"))
        self.assertFalse(server.token_route_allowed("POST", "/api/staff"))
        self.assertTrue(server.token_route_allowed("POST", "/api/team/ask"))


if __name__ == "__main__":
    unittest.main()
