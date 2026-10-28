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
import guard  # noqa: E402
import push  # noqa: E402
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
        env = dict(os.environ, HOME=str(cls.home), AGENT_OFFICE_DATA="", AGENT_OFFICE_TOKEN="")
        cls.log = open(cls.tmp / "server.log", "w")
        cls.proc = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--bind", "127.0.0.1", "--dev"],
                                    cwd=str(cls.app), env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(50):
            try:
                cls.request("GET", "/api/state")
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def write_config(cls, app):
        (app / "config.json").write_text(json.dumps({
            "bind": ["127.0.0.1"], "port": PORT, "claude_bin": str(app / "tests" / "fake_claude.py"),
            "project_roots": [str(cls.home / "Projects")], "agents_dir": str(cls.home / "Agents"),
            "launchctl_bin": str(cls.home / "bin" / "launchctl"),
            "extra_hosts": ["mac.tailnet.ts.net"],
            "external_hosts": ["guild.example.ts.net"],
            "external_token": "test-token-not-a-secret",
            "external_origin": "grokbot"}))

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.log.close()
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

    def seal(self, purpose):
        """Ask for a code and read it from the dev-mode log, like reading it off the phone."""
        status, r = self.post("/api/code", {"purpose": purpose})
        self.assertEqual(status, 200, r)
        time.sleep(0.2)
        lines = [l for l in (self.tmp / "server.log").read_text().splitlines() if "seal code for " + purpose in l]
        return {"code_id": r["code_id"], "code": lines[-1].rsplit(" ", 1)[1]}

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


class Crypto(unittest.TestCase):
    def test_rfc8291_vector(self):
        u = push.unb64u
        body = push.encrypt(b"When I grow up, I want to be a watermelon",
                            u("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
                            u("BTBZMqHH6r4Tts7J_aSIgg"),
                            as_private=int.from_bytes(u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                            salt=u("DGv6ra1nlYgDCS1FRnbzlw"))
        self.assertEqual(push.b64u(body),
                         "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6Tlz"
                         "AC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

    def test_localhost_subject_replaced(self):
        import store
        old, store.DATA_DIR = store.DATA_DIR, Path(tempfile.mkdtemp())
        try:
            self.assertEqual(push.Pusher("mailto:agent-office@localhost").subject, push.DEFAULT_SUBJECT)
            self.assertEqual(push.Pusher("mailto:me@example.com").subject, "mailto:me@example.com")
        finally:
            shutil.rmtree(store.DATA_DIR)
            store.DATA_DIR = old

    def test_endpoint_allowlist(self):
        self.assertTrue(push.endpoint_allowed("https://web.push.apple.com/abc"))
        self.assertTrue(push.endpoint_allowed("https://wns2-par02p.notify.windows.com/w/?token=x"))
        self.assertFalse(push.endpoint_allowed("http://web.push.apple.com/abc"))
        self.assertFalse(push.endpoint_allowed("https://evil.example/web.push.apple.com"))
        self.assertFalse(push.endpoint_allowed("https://127.0.0.1:7777/api/state"))


class Seals(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.codes = guard.Codes(lambda title, body: self.sent.append(title) or True)

    def code(self):
        return self.sent[-1].split(": ")[1]

    def test_right_code_once(self):
        cid = self.codes.request("bypass:new", "x")
        self.assertTrue(self.codes.verify(cid, self.code(), "bypass:new"))
        self.assertFalse(self.codes.verify(cid, self.code(), "bypass:new"))  # single use

    def test_wrong_purpose_and_tries(self):
        cid = self.codes.request("bypass:new", "x")
        self.assertFalse(self.codes.verify(cid, self.code(), "clean:npm"))
        cid = self.codes.request("bypass:new", "x")
        good = self.code()
        for _ in range(guard.MAX_TRIES):
            self.assertFalse(self.codes.verify(cid, "000000" if good != "000000" else "111111", "bypass:new"))
        self.assertFalse(self.codes.verify(cid, good, "bypass:new"))  # locked out

    def test_expiry_and_memory_only(self):
        cid = self.codes.request("bypass:new", "x")
        self.codes.pending[cid]["expires"] = time.time() - 1
        self.assertFalse(self.codes.verify(cid, self.code(), "bypass:new"))
        self.assertNotIn(self.code(), json.dumps({k: str(v) for k, v in self.codes.__dict__.items()}))

    def test_no_phone_no_code(self):
        codes = guard.Codes(lambda t, b: False)
        with self.assertRaises(RuntimeError):
            codes.request("bypass:new", "x")


class Stage2(ServerCase):
    def wait_run(self, rid):
        v = -1
        for _ in range(60):
            status, run = self.request("GET", "/api/run/%s?v=%d" % (rid, v))
            self.assertEqual(status, 200)
            v = run["v"]
            if run["done"]:
                return run
        self.fail("run never finished")

    def test_send_streams_reply_into_transcript(self):
        sid = fixtures.sid(4)
        status, r = self.post("/api/session/%s/send" % sid, {"text": "status report please", "mode": "acceptEdits"})
        self.assertEqual(status, 200, r)
        run = self.wait_run(r["run"])
        self.assertTrue(run["ok"])
        self.assertIn("Heard you", "".join(e["t"] for e in run["events"] if e["k"] == "text"))
        time.sleep(2.2)
        status, d = self.request("GET", "/api/session/" + sid)
        self.assertIn("status report please", d["messages"][-2]["text"])

    def test_busy_session_queues(self):
        sid = fixtures.sid(1)  # transcript written seconds ago: working
        status, r = self.post("/api/session/%s/send" % sid, {"text": "after that, run lint", "mode": "acceptEdits"})
        self.assertEqual(status, 200)
        qid = r["queued"]["id"]
        self.assertEqual(self.request("GET", "/api/session/" + sid)[1]["queue_items"][0]["text"], "after that, run lint")
        status, r = self.post("/api/session/%s/queue/%s/now" % (sid, qid))
        self.assertEqual(status, 200)
        self.wait_run(r["run"])
        status, r = self.post("/api/session/%s/send" % sid, {"text": "slow one", "mode": "acceptEdits"})
        rid = r.get("run")
        status, r2 = self.post("/api/session/%s/send" % sid, {"text": "then this", "mode": "acceptEdits"})
        self.assertIn("queued", r2)
        self.assertEqual(self.post("/api/session/%s/queue/%s/remove" % (sid, r2["queued"]["id"]))[1], {"removed": True})
        if rid:
            self.wait_run(rid)

    def test_bypass_needs_seal(self):
        status, r = self.post("/api/session/%s/send" % fixtures.sid(5), {"text": "go", "mode": "bypassPermissions"})
        self.assertEqual(status, 409)
        self.assertEqual(r["needs_code"], "bypass:" + fixtures.sid(5))
        status, r = self.post("/api/session/%s/send" % fixtures.sid(5),
                              {"text": "go", "mode": "bypassPermissions", "code_id": "nope", "code": "123456"})
        self.assertEqual(status, 409)
        self.assertEqual(self.post("/api/code", {"purpose": "rm -rf /"})[0], 400)

    def test_unknown_mode_and_paths_rejected(self):
        status, r = self.post("/api/session/%s/send" % fixtures.sid(5), {"text": "go", "mode": "--dangerously-skip-permissions"})
        self.assertEqual(status, 409)
        status, r = self.post("/api/job", {"project": "/etc", "text": "hi", "mode": "acceptEdits"})
        self.assertEqual(status, 409)
        self.assertIn("unknown project", r["error"])

    def test_desktop_session_refused(self):
        sid = fixtures.sid(2)
        fake_app = self.tmp / "Claude.app" / "Contents"
        fake_app.mkdir(parents=True)
        exe = fake_app / "claude"
        exe.write_text("#!/bin/sh\nsleep 30\n")
        exe.chmod(0o755)
        proc = subprocess.Popen([str(exe), "--resume", sid])
        try:
            time.sleep(2.5)
            s = {x["id"]: x for x in self.request("GET", "/api/state")[1]["sessions"]}[sid]
            self.assertTrue(s["desktop"])
            status, r = self.post("/api/session/%s/send" % sid, {"text": "hi", "mode": "acceptEdits"})
            self.assertEqual(status, 409)
            self.assertTrue(r["desktop"])
        finally:
            proc.terminate()
            proc.wait()

    def test_new_job(self):
        projects = self.request("GET", "/api/projects")[1]["projects"]
        self.assertTrue(all("path" not in p for p in projects))
        pid = [p for p in projects if p["label"].endswith("webapp")][0]["id"]
        status, r = self.post("/api/job", {"project": pid, "text": "add a health endpoint", "mode": "plan"})
        self.assertEqual(status, 200, r)
        run = self.wait_run(r["run"])
        self.assertIn("mode=plan", run["events"][0]["t"])
        time.sleep(2.2)
        self.assertIn(r["sid"], {x["id"] for x in self.request("GET", "/api/state")[1]["sessions"]})

    def test_push_subscription_validated(self):
        bad = {"endpoint": "https://evil.example/x", "keys": {"p256dh": "x", "auth": "y"}}
        self.assertEqual(self.post("/api/push/subscribe", {"subscription": bad})[0], 400)
        key = push.b64u(push.public_of(push.new_private()))
        good = {"endpoint": "https://web.push.apple.com/QAbc", "keys": {"p256dh": key, "auth": push.b64u(os.urandom(16))}}
        self.assertEqual(self.post("/api/push/subscribe", {"subscription": good})[0], 200)
        self.assertTrue(self.request("GET", "/api/push/key")[1]["subscribed"])
        self.post("/api/push/unsubscribe")


class Stage3(ServerCase):
    wait_run = Stage2.wait_run

    def hire(self, **kw):
        body = {"name": "", "role": "Python backend engineer", "instructions": "Write tests first.", "division": ""}
        body.update(kw)
        status, r = self.post("/api/staff", body)
        self.assertEqual(status, 200, r)
        return r

    def cli(self, *args, stdin=None):
        env = dict(os.environ, HOME=str(self.home), AGENT_OFFICE_URL="http://127.0.0.1:%d" % PORT)
        p = subprocess.run([sys.executable, str(self.app / "tools" / "office")] + list(args), env=env,
                           input=stdin, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def test_a_hire_unique_names_and_folder(self):
        a = self.hire(name="Lyra")
        self.assertEqual(a["staff"]["name"], "Lyra")
        self.assertEqual(a["staff"]["title"], "Artificer of the Deep Halls")
        self.assertEqual(a["staff"]["division"], "The Forge")
        b = self.hire(name="lyra", role="DeFi researcher", instructions="Track LP fee yields on Robinhood Chain.")
        self.assertEqual(b["staff"]["name"], "lyra II")
        self.assertEqual(b["renamed_from"], "lyra")
        self.assertEqual(b["staff"]["division"], "The Counting House")
        c = self.hire(role="Technical writer", instructions="Plain English, short sentences.")
        self.assertEqual(c["staff"]["name"], "Aldric")
        md = (self.home / "Agents" / "lyra" / "CLAUDE.md").read_text()
        self.assertIn("Write tests first.", md)
        self.assertIn("Never ask the user questions", md)
        self.assertEqual(self.post("/api/staff", {"role": ""})[0], 400)

    def test_b_who_and_team_cli(self):
        code, out = self.cli("team")
        self.assertEqual(code, 0, out)
        self.assertIn("The Forge", out)
        code, out = self.cli("who", "research the best LP fee pools")
        self.assertEqual(out.split()[0], "lyra")
        code, out = self.cli("who", "write the release notes")
        self.assertEqual(out.split()[0], "Aldric")

    def test_c_ask_waits_for_answer_and_helpers_never_need_you(self):
        code, out = self.cli("ask", "Aldric", "Draft a README intro. ASKBACK")
        self.assertEqual(code, 0, out)
        self.assertIn("Heard you", out)
        time.sleep(2.5)
        state = self.request("GET", "/api/state")[1]
        aldric = [m for m in state["team"]["staff"] if m["name"] == "Aldric"][0]
        self.assertNotEqual(aldric["status"], "needs")
        helper = [s for s in state["sessions"] if s["id"] == aldric["sid"]][0]
        self.assertTrue(helper["helper"])
        self.assertNotEqual(helper["status"], "needs")

    def test_d_no_wait_then_result(self):
        code, out = self.cli("ask", "lyra II", "slow: list three pools", "--no-wait")
        self.assertEqual(code, 0, out)
        sid = out.split("Session ")[1].split()[0]
        code, out = self.cli("result", sid)
        self.assertEqual(code, 0, out)
        self.assertIn("list three pools", out)

    def test_e_lead_still_needs_you(self):
        sid = fixtures.sid(5)
        status, r = self.post("/api/session/%s/send" % sid, {"text": "ASKBACK please", "mode": "acceptEdits"})
        self.wait_run(r["run"])
        time.sleep(5)
        s = {x["id"]: x for x in self.request("GET", "/api/state")[1]["sessions"]}[sid]
        self.assertEqual(s["status"], "needs")

    def test_f_pm_mode_appends_prompt(self):
        pid = [p for p in self.request("GET", "/api/projects")[1]["projects"] if p["label"].endswith("webapp")][0]["id"]
        status, r = self.post("/api/job", {"project": pid, "text": "ship the health endpoint", "mode": "acceptEdits", "pm": True})
        run = self.wait_run(r["run"])
        self.assertIn("pm=yes", run["events"][0]["t"])

    def test_g_install_hook_is_idempotent(self):
        code, out = self.cli("install-hook")
        self.assertEqual(code, 0, out)
        code, out = self.cli("install-hook")
        self.assertIn("already", out)
        settings = json.loads((self.home / ".claude" / "settings.json").read_text())
        self.assertEqual(len(settings["hooks"]["Notification"]), 1)
        code, _ = self.cli("hook", stdin=json.dumps({"session_id": fixtures.sid(6), "notification_type": "permission_prompt",
                                                     "message": "Claude needs your permission to use Bash"}))
        self.assertEqual(code, 0)

    def test_h_dismiss(self):
        r = self.hire(name="Temp", role="tester")
        self.assertTrue(self.post("/api/staff/%s/dismiss" % r["staff"]["id"])[1]["dismissed"])
        self.assertTrue((self.home / "Agents" / "temp").exists())


class Stage4(ServerCase):
    def test_usage(self):
        status, u = self.request("GET", "/api/usage")
        self.assertEqual(status, 200)
        self.assertEqual(len(u["days"]), 14)
        self.assertGreater(u["today"], 0)
        self.assertIn("Opus 5.5", [m["model"] for m in u["models"]])
        self.assertGreater(self.request("GET", "/api/state")[1]["cost_today"], 0)

    def test_bots(self):
        jobs = {j["label"]: j for j in self.request("GET", "/api/bots")[1]["jobs"]}
        self.assertTrue(jobs["com.bojjifomo.agent-office"]["running"])
        report = jobs["com.bojjifomo.daily-report"]
        self.assertTrue(report["failed"])
        self.assertIn("timed out", report["error"])
        self.assertEqual(report["schedule"], "daily at 07:30")
        self.assertEqual(jobs["com.bojjifomo.lp-scanner"]["schedule"], "every 15 min")
        status, r = self.post("/api/bots/%s/run" % jobs["com.bojjifomo.lp-scanner"]["id"])
        self.assertEqual(status, 200, r)
        self.assertIn("kickstart gui/", (self.home / "launchctl.log").read_text())
        self.assertEqual(self.post("/api/bots/%s/run" % jobs["com.bojjifomo.agent-office"]["id"])[0], 409)
        self.assertEqual(self.post("/api/bots/0000000000/run")[0], 404)

    def test_disk_and_sealed_cleaner(self):
        d = self.request("GET", "/api/disk?rescan=1")[1]
        self.assertGreater(d["total"], 0)
        for _ in range(30):
            d = self.request("GET", "/api/disk")[1]
            if d["folders"] and not d["scanning"]:
                break
            time.sleep(0.2)
        self.assertEqual(d["folders"][0]["path"], "~/Downloads")
        self.assertEqual(self.post("/api/disk/clean/trash", {})[0], 409)
        self.assertEqual(self.post("/api/disk/clean/rm-rf", {})[0], 404)
        wrong = self.seal("clean:npm")
        self.assertEqual(self.post("/api/disk/clean/trash", wrong)[0], 409)  # seal for another cleaner
        status, r = self.post("/api/disk/clean/trash", self.seal("clean:trash"))
        self.assertEqual(status, 200, r)
        self.assertEqual(list((self.home / ".Trash").iterdir()), [])
        self.assertTrue((self.home / "Downloads" / "blob.bin").exists())

    def test_bypass_with_seal_runs(self):
        sid = fixtures.sid(5)
        body = dict(self.seal("bypass:" + sid), text="go wild", mode="bypassPermissions")
        status, r = self.post("/api/session/%s/send" % sid, body)
        self.assertEqual(status, 200, r)
        run = Stage2.wait_run(self, r["run"])
        self.assertIn("mode=bypassPermissions", run["events"][0]["t"])
        self.assertEqual(self.post("/api/session/%s/send" % sid, body)[0], 409)  # the seal was single-use

    def test_search(self):
        r = self.request("GET", "/api/search?q=" + "v1%20endpoints")[1]
        self.assertEqual(r["results"][0]["id"], fixtures.sid(2))
        self.assertEqual(r["results"][0]["match"].lower(), "v1 endpoints")
        old = self.request("GET", "/api/search?q=websocket")[1]["results"]
        self.assertTrue(any(h["id"] == fixtures.sid(6) for h in old))
        self.assertEqual(self.request("GET", "/api/search?q=x")[1]["results"], [])

    def test_ledger_records_actions(self):
        self.post("/api/session/%s/send" % fixtures.sid(4), {"text": "ledger check", "mode": "acceptEdits"})
        entries = self.request("GET", "/api/ledger")[1]["entries"]
        self.assertTrue(any(e["text"] == "ledger check" for e in entries))


class Petitions(ServerCase):
    wait_run = Stage2.wait_run
    cli = Stage3.cli

    def propose(self, title, cwd=None):
        env = dict(os.environ, HOME=str(self.home), AGENT_OFFICE_URL="http://127.0.0.1:%d" % PORT)
        cwd = cwd or self.home / "Agents" / "orin"
        cwd.mkdir(parents=True, exist_ok=True)
        p = subprocess.run([sys.executable, str(self.app / "tools" / "office"), "propose", "--title", title,
                            "--app", "RH Wallet Finder", "--summary", "Alert when a starred wallet moves.",
                            "--impact", "high", "--effort", "M"], cwd=str(cwd), env=env,
                           input="## Plan\n\n1. Watch LP events\n2. Push an alert", capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout.split()[1]

    def test_propose_reject_and_build(self):
        pid = self.propose("Watchlist alerts")
        listing = self.request("GET", "/api/petitions")[1]["petitions"]
        item = [p for p in listing if p["id"] == pid][0]
        self.assertEqual((item["by"], item["status"], item["impact"], item["effort"]), ("Orin", "pending", "high", "M"))
        self.assertEqual(self.request("GET", "/api/state")[1]["petitions"], 1)

        status, r = self.post("/api/petitions/%s/build" % pid, {})
        self.assertEqual(status, 400)  # not approved yet
        project = [p for p in self.request("GET", "/api/projects")[1]["projects"] if p["label"].endswith("webapp")][0]["id"]
        status, r = self.post("/api/petitions/%s/decide" % pid, {"decision": "approve", "build": True, "note": "keep it small",
                                                              "project": project, "mode": "acceptEdits"})
        self.assertEqual(status, 200, r)
        run = self.wait_run(r["run"])
        brief = run["text"]
        self.assertIn("Watchlist alerts", brief)
        self.assertIn("Do not merge", "".join(e["t"] for e in run["events"]) + Path(self.app / "petitions.py").read_text())
        item = [p for p in self.request("GET", "/api/petitions")[1]["petitions"] if p["id"] == pid][0]
        self.assertEqual((item["status"], item["sid"], item["note"]), ("building", r["sid"], "keep it small"))
        time.sleep(2.2)
        names = {s["id"]: s["name"] for s in self.request("GET", "/api/state")[1]["sessions"]}
        self.assertEqual(names[r["sid"]], "Petition: Watchlist alerts")

        pid2 = self.propose("Dark mode for the website")
        self.assertEqual(self.post("/api/petitions/%s/decide" % pid2, {"decision": "maybe"})[0], 400)
        self.post("/api/petitions/%s/decide" % pid2, {"decision": "reject", "note": "not now"})
        self.assertEqual(self.post("/api/petitions/%s/decide" % pid2, {"decision": "approve"})[0], 400)
        code, out = self.cli("petitions")
        self.assertIn("rejected", out)
        self.assertIn("note: not now", out)
        self.assertEqual(self.post("/api/petitions", {"title": "", "plan": "x"})[0], 400)

    def test_build_bad_id_is_404(self):
        status, body = self.post("/api/petitions/deadbeef/build", {})
        self.assertEqual(status, 404)
        self.assertNotEqual(body.get("error"), "server error")
        self.assertIn("no such petition", body.get("error", ""))


class Profiles(ServerCase):
    def test_persona_pay_and_facts(self):
        s = {x["id"]: x for x in self.request("GET", "/api/state")[1]["sessions"]}[fixtures.sid(4)]
        p = s["persona"]
        for key in ("name", "title", "age", "quirk", "home", "bio", "motto"):
            self.assertTrue(p[key], key)
        again = {x["id"]: x for x in self.request("GET", "/api/state")[1]["sessions"]}[fixtures.sid(4)]
        self.assertEqual(again["persona"], p)  # the same character every time
        self.assertEqual(s["started_as"], "Terminal")
        self.assertGreater(s["pay"]["month"], 0)
        self.assertEqual(self.request("GET", "/api/session/" + fixtures.sid(1))[1]["last_tool"], "Bash")

    def test_division_assignment(self):
        sid = fixtures.sid(5)
        self.post("/api/session/%s/division" % sid, {"division": "The Counting House"})
        s = {x["id"]: x for x in self.request("GET", "/api/state")[1]["sessions"]}[sid]
        self.assertEqual(s["division"], "The Counting House")
        self.post("/api/session/%s/division" % sid, {"division": ""})
        s = {x["id"]: x for x in self.request("GET", "/api/state")[1]["sessions"]}[sid]
        self.assertEqual(s["division"], "")

    def test_haiku_summary_and_replies(self):
        sid = fixtures.sid(2)
        status, r = self.post("/api/session/%s/summarize" % sid)
        self.assertEqual(status, 200, r)
        self.assertIn("v1 endpoints", r["summary"])
        status, r = self.post("/api/session/%s/suggest" % sid)
        self.assertEqual(status, 200, r)
        self.assertEqual(len(r["replies"]), 3)
        before = {x["id"] for x in self.request("GET", "/api/state")[1]["sessions"]}
        time.sleep(2.2)
        after = {x["id"] for x in self.request("GET", "/api/state")[1]["sessions"]}
        self.assertEqual(before, after)  # no stray sessions from Haiku calls
        self.assertEqual(self.request("POST", "/api/session/%s/suggest" % sid, {})[0], 403)  # still guarded


class TokenCompare(unittest.TestCase):
    def test_match_and_config(self):
        import server
        self.assertTrue(server.tokens_match("abc", "abc"))
        self.assertFalse(server.tokens_match("abc", "abd"))
        self.assertFalse(server.tokens_match("abc", "abcd"))
        self.assertFalse(server.tokens_match("", "abc"))
        self.assertFalse(server.tokens_match("a" * 513, "a" * 513))
        names = set()
        server.remember_host(names, "Guild.Example.ts.net.", 7777)
        self.assertIn("guild.example.ts.net", names)
        self.assertIn("guild.example.ts.net:7777", names)
        old = os.environ.get("AGENT_OFFICE_TOKEN")
        try:
            os.environ["AGENT_OFFICE_TOKEN"] = "  from-env  "
            self.assertEqual(server.configured_token({"external_token": "from-file"}), "from-env")
            os.environ["AGENT_OFFICE_TOKEN"] = "   "
            self.assertEqual(server.configured_token({"external_token": "from-file"}), "from-file")
            os.environ.pop("AGENT_OFFICE_TOKEN", None)
            self.assertEqual(server.configured_token({}), "")
            self.assertEqual(server.external_origin_name({}), "grokbot")
            self.assertEqual(server.external_origin_name({"external_origin": "hall bot!"}), "grokbot")
        finally:
            if old is None:
                os.environ.pop("AGENT_OFFICE_TOKEN", None)
            else:
                os.environ["AGENT_OFFICE_TOKEN"] = old

    def test_mark_building_bad_id(self):
        import store
        import petitions
        old = store.DATA_DIR
        tmp = Path(tempfile.mkdtemp())
        store.DATA_DIR = tmp
        try:
            book = petitions.Petitions()
            with self.assertRaises(LookupError):
                book.mark_building("deadbeef", "not-a-session")
            with self.assertRaises(LookupError):
                book.mark_building("", "x")
            self.assertEqual(book.items, [])
        finally:
            store.DATA_DIR = old
            shutil.rmtree(tmp, ignore_errors=True)


_OMIT = object()


class ExternalAccess(ServerCase):
    """Bearer token for Funnel and external_hosts. Local requests with no token stay as they were."""

    TOKEN = "test-token-not-a-secret"
    HOST = "guild.example.ts.net"
    PHONE = "mac.tailnet.ts.net"

    def call(self, method, path, body=None, token=_OMIT, host=None, extra=None):
        headers = dict(extra or {})
        if token is not None:
            headers["Authorization"] = "Bearer " + (self.TOKEN if token is _OMIT else token)
        return self.request(method, path, body, headers=headers, host=host or self.HOST)

    def test_no_token_keeps_local_behavior(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.assertEqual(self.request("GET", "/api/state", host=self.PHONE)[0], 200)  # tailnet Serve name
        opened = self.request("GET", "/api/state", host=self.HOST, headers={"X-Forwarded-For": "203.0.113.4"})
        self.assertEqual(opened[0], 401)
        sid = fixtures.sid(5)
        path = "/api/session/%s/rename" % sid
        self.assertEqual(self.request("POST", path, {"name": "x"})[0], 403)
        self.assertEqual(self.post(path, {"name": "Still Local"})[0], 200)
        self.assertEqual(self.request("GET", "/", host="evil.example")[0], 403)

    def test_wrong_token_is_401_and_not_logged(self):
        secret = "nope-token-value"
        status, body = self.call("GET", "/api/state", token=secret, extra={"X-Forwarded-For": "203.0.113.8"})
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "unauthorized")
        self.assertNotIn(secret, json.dumps(body))
        local = self.request("GET", "/api/state", headers={"Authorization": "Bearer " + secret})
        self.assertEqual(local[0], 401)
        time.sleep(0.2)
        log = (self.tmp / "server.log").read_text()
        self.assertIn("rejected bearer token", log)
        self.assertNotIn(secret, log)
        self.assertNotIn(self.TOKEN, log)

    def test_valid_token_allowlist(self):
        status, state = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("sessions", state)
        self.assertEqual(self.call("GET", "/api/usage")[0], 200)
        self.assertEqual(self.call("GET", "/api/bots")[0], 200)
        self.assertEqual(self.call("GET", "/api/team")[0], 200)
        self.assertEqual(self.call("GET", "/api/team/who?task=tests")[0], 200)
        self.assertEqual(self.call("GET", "/api/petitions")[0], 200)
        self.assertEqual(self.call("GET", "/api/ledger")[0], 200)
        self.assertEqual(self.call("GET", "/api/orchestrator")[0], 200)
        self.assertEqual(self.call("GET", "/api/quests")[0], 200)
        self.assertEqual(self.call("GET", "/api/progress")[0], 200)
        self.assertEqual(self.call("GET", "/api/gold")[0], 200)
        self.assertEqual(self.call("GET", "/api/raids")[0], 200)
        self.assertEqual(self.call("POST", "/api/gold/release/abcdef01", {})[0], 403)
        self.assertEqual(self.call("POST", "/api/orchestrator/brief", {})[0], 403)
        brief = self.call("POST", "/api/team/ask", {"name": "Quill", "brief": "today"})
        self.assertEqual(brief[0], 403)
        self.assertEqual(self.call("GET", "/api/options")[0], 403)
        self.assertEqual(self.call("GET", "/")[0], 403)
        sid = fixtures.sid(4)
        send = self.call("POST", "/api/session/%s/send" % sid, {"text": "hi", "mode": "acceptEdits"})
        self.assertEqual(send[0], 403)
        self.assertEqual(send[1].get("why"), "route")
        self.assertEqual(self.call("POST", "/api/team/ask", {"name": "Nobody", "brief": "hi"})[0], 404)
        self.assertEqual(self.call("GET", "/api/state", token=self.TOKEN, host="evil.example")[0], 403)

    def test_valid_token_on_localhost_does_not_skip_origin(self):
        sid = fixtures.sid(5)
        path = "/api/session/%s/rename" % sid
        headers = {"Authorization": "Bearer " + self.TOKEN}
        self.assertEqual(self.request("POST", path, {"name": "nope"}, headers=headers)[0], 403)
        self.assertEqual(self.post(path, {"name": "Token Local"}, headers=headers)[0], 200)

    def test_funnel_header_requires_token_on_the_phone_host(self):
        funnel = {"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "203.0.113.20"}
        self.assertEqual(self.request("GET", "/api/state", host=self.PHONE, headers=funnel)[0], 401)
        ok = self.call("GET", "/api/state", host=self.PHONE, extra=funnel)
        self.assertEqual(ok[0], 200)
        blocked = self.call("POST", "/api/staff", {"role": "x"}, host=self.PHONE, extra=funnel)
        self.assertEqual(blocked[0], 403)
        plain = self.request("GET", "/api/state", host=self.PHONE, headers={"Tailscale-Funnel-Request": "true"})
        self.assertEqual(plain[0], 200)  # only the exact Tailscale marker counts

    def test_ledger_post_and_audit_origin(self):
        status, body = self.call("POST", "/api/ledger", {"kind": "chatter", "text": "The watch reports all quiet.", "by": "Guild Hall"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.call("POST", "/api/ledger", {"kind": "seal", "text": "forged"})[0], 400)
        self.assertEqual(self.call("POST", "/api/ledger", {"kind": "guild", "text": ""})[0], 400)
        self.call("GET", "/api/ledger")  # its audit line is written as the response finishes
        entries = self.call("GET", "/api/ledger")[1]["entries"]
        posted = [e for e in entries if e.get("text") == "The watch reports all quiet."]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["kind"], "chatter")
        self.assertEqual(posted[0]["by"], "Guild Hall")
        self.assertEqual(posted[0]["origin"], "grokbot")
        audits = [e for e in entries if e.get("kind") == "external" and str(e.get("text", "")).startswith("POST /api/ledger")]
        self.assertTrue(audits)
        self.assertEqual(audits[0]["origin"], "grokbot")
        self.assertTrue(any(str(e.get("text", "")).startswith("GET /api/ledger") for e in entries))

    def test_decide_note_and_job_seal(self):
        status, made = self.call("POST", "/api/petitions", {
            "by": "Guild Hall", "title": "External petition", "app": "Hall", "plan": "Add a bell.", "summary": "A bell."})
        self.assertEqual(status, 200, made)
        pid = made["petition"]["id"]
        built = self.call("POST", "/api/petitions/%s/decide" % pid, {"decision": "approve", "build": True, "note": "no"})
        self.assertEqual(built[0], 403)
        item = [p for p in self.call("GET", "/api/petitions")[1]["petitions"] if p["id"] == pid][0]
        self.assertEqual(item["status"], "pending")
        decided = self.call("POST", "/api/petitions/%s/decide" % pid, {"decision": "reject", "note": "not now"})
        self.assertEqual(decided[0], 200, decided[1])
        self.assertEqual(self.call("POST", "/api/petitions/%s/build" % pid, {})[0], 403)
        self.assertEqual(self.call("GET", "/api/projects")[0], 403)  # folders are not on the allowlist
        # A risky quest reaches /api/job and then stops on the seal. The project id is local-only.
        local_projects = self.request("GET", "/api/projects")[1]["projects"]
        project = [p for p in local_projects if p["label"].endswith("webapp")][0]["id"]
        risky = self.call("POST", "/api/job", {"project": project, "text": "external bypass", "mode": "bypassPermissions"})
        self.assertEqual(risky[0], 409)
        self.assertIn("seal", risky[1].get("error", "").lower())
        quest = self.call("POST", "/api/job", {"project": project, "text": "external pm quest", "mode": "acceptEdits", "pm": True})
        self.assertEqual(quest[0], 200, quest[1])
        job = [e for e in self.call("GET", "/api/ledger")[1]["entries"] if e.get("text") == "external pm quest"]
        self.assertTrue(job)
        self.assertEqual(job[0]["origin"], "grokbot")
        self.assertTrue(job[0]["pm"])

    def test_failed_tokens_back_off(self):
        import server
        fwd = {"X-Forwarded-For": "198.51.100.23"}
        for _ in range(server.TokenFailures.MAX_FAILS - 1):
            self.assertEqual(self.call("GET", "/api/state", token="wrong", extra=fwd)[0], 401)
        self.assertEqual(self.call("GET", "/api/state", token="wrong", extra=fwd)[0], 429)
        self.assertEqual(self.call("GET", "/api/state", extra=fwd)[0], 200)  # a good token clears the count
        self.assertEqual(self.call("GET", "/api/state", token="wrong", extra=fwd)[0], 401)

    def test_cli_sends_token_only_when_remote(self):
        path = self.app / "tools" / "office"
        script = (
            "import importlib.machinery, json\n"
            "m = importlib.machinery.SourceFileLoader('officecli', %r).load_module()\n"
            "print(json.dumps(m.token_header()))\n" % str(path)
        )

        def header(url):
            env = dict(os.environ, AGENT_OFFICE_URL=url, AGENT_OFFICE_TOKEN="remote-secret")
            proc = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)

        self.assertEqual(header("http://127.0.0.1:%d" % PORT), {})
        self.assertEqual(header("http://localhost:7777"), {})
        self.assertEqual(header("http://100.89.176.76:7777"), {})
        self.assertEqual(header("https://guild.example.ts.net"), {"Authorization": "Bearer remote-secret"})
        env = dict(os.environ, HOME=str(self.home), AGENT_OFFICE_URL="http://127.0.0.1:%d" % PORT,
                   AGENT_OFFICE_TOKEN="nope-token-value")
        proc = subprocess.run([sys.executable, str(path), "status"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
