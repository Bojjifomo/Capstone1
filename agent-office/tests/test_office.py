"""Tests for Agent Office. Run: python3 -m unittest discover -s tests"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402
import sessions  # noqa: E402

PORT = 7791


class ServerCase(unittest.TestCase):
    """Starts a copy of the server on PORT against a fake $HOME."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ao-test-"))
        cls.home = fixtures.build(cls.tmp / "home")
        cls.app = cls.tmp / "app"
        shutil.copytree(ROOT, cls.app, ignore=shutil.ignore_patterns("*.json", "docs", "__pycache__", "logs"))
        cls.write_config(cls.app)
        env = dict(os.environ, HOME=str(cls.home), AGENT_OFFICE_DATA="")
        cls.proc = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--bind", "127.0.0.1", "--dev"],
                                    cwd=str(cls.app), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(50):
            try:
                cls.request("GET", "/api/state")
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def write_config(cls, app):
        pass

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def request(method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        h = {"Host": host or "127.0.0.1:%d" % PORT}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            return res.status, json.loads(raw)
        except ValueError:
            return res.status, raw

    @classmethod
    def post(cls, path, body=None, **kw):
        headers = {"X-Agent-Office": "1", "Origin": "http://127.0.0.1:%d" % PORT}
        headers.update(kw.pop("headers", {}))
        return cls.request("POST", path, body or {}, headers=headers, **kw)


class QuestionDetection(unittest.TestCase):
    def test_real_questions(self):
        self.assertTrue(sessions.ends_with_question("Done.\n\nShould I also migrate the old endpoints?"))
        self.assertTrue(sessions.ends_with_question("Which one do you prefer?\n\n- A\n- B?"))
        self.assertTrue(sessions.ends_with_question("Want me to push it? **"))

    def test_not_questions(self):
        self.assertFalse(sessions.ends_with_question("All 42 tests pass. Done."))
        self.assertFalse(sessions.ends_with_question("Let me know if you want changes."))
        self.assertFalse(sessions.ends_with_question("Why? Because the cache was stale.\n\nFixed it.\n\nAll good."))
        self.assertFalse(sessions.ends_with_question("```\nis_ok?\n```"))
        self.assertFalse(sessions.ends_with_question("Done.\n\n> Should we ship?"))


class Parsing(unittest.TestCase):
    def test_usage_dedupes_message_ids(self):
        tmp = Path(tempfile.mkdtemp())
        f = tmp / "11111111-1111-1111-1111-111111111111.jsonl"
        line = fixtures.assistant(time.time(), "hi", mid="msg_same")
        f.write_text(json.dumps(line) + "\n" + json.dumps(line) + "\n")
        t = sessions.Transcript(f)
        t.update()
        self.assertEqual(t.usage_total()[1], 800)
        self.assertEqual(len(t.messages), 1)
        shutil.rmtree(tmp)

    def test_partial_line_waits(self):
        tmp = Path(tempfile.mkdtemp())
        f = tmp / "11111111-1111-1111-1111-111111111112.jsonl"
        whole = json.dumps(fixtures.user("hello", time.time())) + "\n"
        f.write_text(whole + whole[:20])
        t = sessions.Transcript(f)
        t.update()
        self.assertEqual(len(t.messages), 1)
        with open(f, "a") as fh:
            fh.write(whole[20:])
        t.update()
        self.assertEqual(len(t.messages), 2)
        shutil.rmtree(tmp)

    def test_costs(self):
        u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        self.assertAlmostEqual(sessions.usage_cost("claude-opus-5-5", u), 24.0)
        self.assertAlmostEqual(sessions.usage_cost("claude-haiku-5-5", u), 0.6)
        self.assertEqual(sessions.usage_cost("<synthetic>", u), 0)


class Stage1(ServerCase):
    def state(self):
        status, data = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        return {s["id"]: s for s in data["sessions"]}

    def test_a_statuses(self):  # runs first: later tests change state
        s = self.state()
        self.assertEqual(s[fixtures.sid(1)]["status"], "working")
        self.assertEqual(s[fixtures.sid(2)]["status"], "needs")
        self.assertEqual(s[fixtures.sid(3)]["status"], "needs")
        self.assertIn("Which audience", s[fixtures.sid(3)]["note"])
        self.assertEqual(s[fixtures.sid(4)]["status"], "idle")  # finished a reply, asked nothing
        self.assertEqual(s[fixtures.sid(5)]["status"], "idle")
        self.assertEqual(s[fixtures.sid(6)]["status"], "finished")

    def test_detail_and_rename_archive(self):
        sid = fixtures.sid(4)
        status, d = self.request("GET", "/api/session/" + sid)
        self.assertEqual(status, 200)
        self.assertIn("Pipeline report", d["messages"][-1]["text"])
        self.assertEqual(self.post("/api/session/%s/rename" % sid, {"name": "Pipeline Paladin"})[0], 200)
        self.assertEqual(self.state()[sid]["name"], "Pipeline Paladin")
        self.post("/api/session/%s/archive" % sid, {"archived": True})
        self.assertEqual(self.state()[sid]["status"], "archived")
        self.post("/api/session/%s/archive" % sid, {"archived": False})
        self.assertTrue((self.app / "names.json").exists())

    def test_bad_host_rejected(self):
        self.assertEqual(self.request("GET", "/api/state", host="evil.example:7791")[0], 403)
        self.assertEqual(self.request("GET", "/", host="attacker.com")[0], 403)

    def test_post_needs_header_and_origin(self):
        path = "/api/session/%s/rename" % fixtures.sid(5)
        self.assertEqual(self.request("POST", path, {"name": "x"})[0], 403)
        self.assertEqual(self.request("POST", path, {"name": "x"}, headers={"X-Agent-Office": "1"})[0], 403)
        self.assertEqual(self.post(path, {"name": "x"}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.post(path, {"name": "x"}, headers={"X-Agent-Office": "0"})[0], 403)
        self.assertEqual(self.post(path, {"name": ""})[0], 200)

    def test_static_is_contained(self):
        self.assertEqual(self.request("GET", "/static/../server.py")[0], 404)
        self.assertEqual(self.request("GET", "/static/%2e%2e/server.py")[0], 404)
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Guild Hall", body)

    def test_security_headers(self):
        conn = http.client.HTTPConnection("127.0.0.1", PORT)
        conn.request("GET", "/", headers={"Host": "127.0.0.1:%d" % PORT})
        res = conn.getresponse()
        res.read()
        self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))
        self.assertEqual(res.getheader("X-Frame-Options"), "DENY")
        conn.close()

    def test_hook_flags_permission(self):
        sid = fixtures.sid(5)
        self.post("/api/hook/notify", {"session_id": sid, "notification_type": "permission_prompt",
                                       "message": "Claude needs your permission to use Bash"})
        s = self.state()[sid]
        self.assertEqual(s["status"], "needs")
        self.assertIn("permission", s["note"])


if __name__ == "__main__":
    unittest.main()
