"""Grok members: the tool loop against a fake xAI server, and the guards on its tools.
Run: python3 -m unittest discover -s tests"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import grok  # noqa: E402
from test_office import ServerCase  # noqa: E402


class FakeXAI(BaseHTTPRequestHandler):
    """First turn: read the hub and write a file. After the tool results: answer."""
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeXAI.seen.append({"auth": self.headers.get("Authorization"), "body": body})
        msgs = body["messages"]
        tools = {t["function"]["name"] for t in body["tools"]}
        if msgs[-1]["role"] == "tool":
            results = [m["content"] for m in msgs if m["role"] == "tool"]
            reply = {"role": "assistant", "content": "Beres. LP bot: %s" % ("alive" in results[0])}
        else:
            calls = [{"id": "c1", "type": "function", "function": {"name": "hub_lpbot", "arguments": "{}"}}]
            if "write_file" in tools:
                calls.append({"id": "c2", "type": "function", "function": {
                    "name": "write_file", "arguments": json.dumps({"path": "note.txt", "content": "halo"})}})
            calls.append({"id": "c3", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": "../../etc/passwd"})}})
            reply = {"role": "assistant", "content": "", "tool_calls": calls}
        out = json.dumps({"choices": [{"message": reply}], "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class GrokMembers(ServerCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeXAI)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.fake.shutdown()

    @classmethod
    def write_config(cls, app):
        bot = cls.tmp / "lp_call_bot"
        bot.mkdir(exist_ok=True)
        (bot / "bot.py").write_text("")
        (bot / "heartbeat").write_text("")
        (app / "config.json").write_text(json.dumps({
            "bind": ["127.0.0.1"], "port": 7791, "claude_bin": str(app / "tests" / "fake_claude.py"),
            "project_roots": [str(cls.home / "Projects")], "agents_dir": str(cls.home / "Agents"),
            "launchctl_bin": str(cls.home / "bin" / "launchctl"), "lp_call_bot_dir": str(bot),
            "xai_api_key": "xai-test-not-a-secret", "xai_base_url": "http://127.0.0.1:%d/v1" % cls.fake.server_port}))

    def wait_run(self, rid):
        v = -1
        for _ in range(30):
            status, run = self.request("GET", "/api/run/%s?v=%d" % (rid, v))
            self.assertEqual(status, 200, run)
            if run["done"]:
                return run
            v = run["v"]
        self.fail("the Grok run never finished")

    def test_grokbot_answers_in_the_chat(self):
        status, opts = self.request("GET", "/api/options")
        self.assertIn("grok", opts["models"])
        self.assertTrue(opts["grok"]["on"])
        staff = self.request("GET", "/api/team")[1]["staff"]
        bot = [s for s in staff if s["name"] == "Grokbot"]
        self.assertEqual(len(bot), 1)
        self.assertEqual(bot[0]["model"], "grok")
        status, sent = self.post("/api/chat", {"text": "@Grokbot halo, LP bot hidup?"})
        self.assertEqual(status, 200, sent)
        self.assertEqual(sent["to"], "Grokbot")
        run = self.wait_run(sent["run"])
        self.assertTrue(run["ok"], run)
        self.assertEqual(run["final"], "Beres. LP bot: True")
        self.assertGreater(run["cost"], 0)
        tools = [e["t"] for e in run["events"] if e["k"] == "tool"]
        self.assertTrue(any("write file note.txt" in t for t in tools), tools)
        folder = Path(self.home / "Agents")
        self.assertEqual([p.read_text() for p in folder.rglob("note.txt")], ["halo"])
        last = FakeXAI.seen[-1]
        self.assertEqual(last["auth"], "Bearer xai-test-not-a-secret")
        escaped = [m["content"] for m in last["body"]["messages"] if m["role"] == "tool" and m["tool_call_id"] == "c3"]
        self.assertTrue(escaped[0].startswith("error: outside your folders"), escaped)
        self.assertNotIn("run_command", {t["function"]["name"] for t in last["body"]["tools"]})  # acceptEdits
        for _ in range(20):
            entries = self.request("GET", "/api/ledger")[1]["entries"]
            replies = [e for e in entries if e.get("kind") == "reply" and e.get("by") == "Grokbot"]
            if replies:
                break
            time.sleep(0.1)
        self.assertIn("Beres", replies[0]["text"])
        # The second message carries the first exchange as memory.
        status, sent = self.post("/api/chat", {"text": "@Grokbot terus?"})
        self.wait_run(sent["run"])
        convo = [m for m in FakeXAI.seen[-2]["body"]["messages"] if m["role"] in ("user", "assistant")]
        self.assertIn("halo, LP bot hidup?", convo[0]["content"])
        self.assertEqual(convo[1]["content"], "Beres. LP bot: True")

    def test_quest_cannot_switch_to_grok(self):
        import fixtures
        status, body = self.post("/api/session/%s/model" % fixtures.sid(5), {"model": "grok"})
        self.assertNotEqual(status, 200)
        self.assertIn("Grok", body.get("error", ""))


class ToolGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "work").mkdir()
        (self.tmp / "work" / "a.txt").write_text("one\ntwo\n")
        (self.tmp / "work" / ".env").write_text("SECRET=1")
        (self.tmp / "outside.txt").write_text("no")
        os.symlink(self.tmp / "outside.txt", self.tmp / "work" / "link.txt")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tools(self, mode):
        return grok.Tools([str(self.tmp / "work")], mode)

    def test_paths_stay_inside(self):
        t = self.tools("auto")
        self.assertIn("two", t.call("read_file", {"path": "a.txt"}))
        for bad in ("../outside.txt", str(self.tmp / "outside.txt"), "link.txt", ".env"):
            with self.assertRaises(grok.GrokError):
                t.call("read_file", {"path": bad})
        self.assertNotIn(".env", t.call("search", {"pattern": "SECRET"}))

    def test_modes(self):
        with self.assertRaises(grok.GrokError):
            self.tools("plan").call("write_file", {"path": "b.txt", "content": "x"})
        with self.assertRaises(grok.GrokError):
            self.tools("acceptEdits").call("run_command", {"command": "ls"})
        t = self.tools("acceptEdits")
        t.call("edit_file", {"path": "a.txt", "old": "two", "new": "dua"})
        self.assertEqual((self.tmp / "work" / "a.txt").read_text(), "one\ndua\n")
        names = {s["function"]["name"] for s in grok.tool_specs("plan", {"status": "x"})}
        self.assertNotIn("write_file", names)
        self.assertIn("run_command", {s["function"]["name"] for s in grok.tool_specs("auto", {"status": "x"})})

    def test_commands(self):
        t = self.tools("auto")
        self.assertIn("a.txt", t.call("run_command", {"command": "ls"}))
        os.environ["XAI_API_KEY"] = "xai-hidden"
        try:
            self.assertNotIn("xai-hidden", t.call("run_command", {"command": "env"}))
        finally:
            del os.environ["XAI_API_KEY"]
        for bad in ("sudo ls", "curl https://example.com", "git push --force origin main", "rm -rf /"):
            with self.assertRaises(grok.GrokError, msg=bad):
                t.call("run_command", {"command": bad})
        with self.assertRaises(grok.GrokError):
            t.call("run_command", {"command": "ls", "cwd": ".."})


if __name__ == "__main__":
    unittest.main()
