"""The 💰 Fund button: /fund/ on the Guild Hall forwards to the Fund app (../fund).

Runs the real Fund server from a temp copy and the real Guild Hall pointed at it.
Run: python3 -m unittest discover -s tests
"""
import json
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_office import PORT, ServerCase  # noqa: E402

FUND_SRC = Path(__file__).resolve().parent.parent.parent / "fund"
FUND_PORT = 7792


@unittest.skipUnless((FUND_SRC / "server.py").is_file(), "fund/ not next to agent-office/")
class FundDoor(ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fund = cls.tmp / "fund"
        shutil.copytree(FUND_SRC, cls.fund, ignore=shutil.ignore_patterns("tests", "*.db*", "config.json", "__pycache__"))
        (cls.fund / "config.json").write_text(json.dumps({"db": str(cls.tmp / "fund.db"), "token": "fund-secret"}))
        cls.fund_log = open(cls.tmp / "fund.log", "w")
        cls.fund_proc = subprocess.Popen([sys.executable, "server.py", "--port", str(FUND_PORT), "--bind", "127.0.0.1"],
                                         cwd=str(cls.fund), stdout=cls.fund_log, stderr=subprocess.STDOUT)
        for _ in range(50):
            if cls.request("GET", "/fund/api/meta")[0] == 200:
                break
            time.sleep(0.1)

    @classmethod
    def write_config(cls, app):
        super().write_config(app)
        cfg = json.loads((app / "config.json").read_text())
        cfg.update(fund_url="http://127.0.0.1:%d" % FUND_PORT, fund_token="fund-secret")
        (app / "config.json").write_text(json.dumps(cfg))

    @classmethod
    def tearDownClass(cls):
        cls.fund_proc.terminate()
        cls.fund_proc.wait(5)
        cls.fund_log.close()
        super().tearDownClass()

    def test_button_is_in_the_toolbar(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="fund-btn"', page)

    def test_redirect_and_page(self):
        self.assertEqual(self.request("GET", "/fund")[0], 302)
        status, page = self.request("GET", "/fund/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Fund</title>", page)
        self.assertEqual(self.request("GET", "/fund/app.js")[0], 200)

    def test_read_and_write_through_the_door(self):
        status, r = self.post("/fund/api/accounts", {"name": "BCA", "kind": "bank", "sleeve": 0})
        self.assertEqual(status, 200, r)
        status, r = self.post("/fund/api/balances", {"account": "BCA", "amount": 7_000_000})
        self.assertEqual(status, 200, r)
        status, s = self.request("GET", "/fund/api/summary")
        self.assertEqual((status, s["net_worth_idr"]), (200, 7_000_000))
        status, r = self.request("GET", "/fund/api/flows?month=2026-11")
        self.assertEqual((status, r["month"]), (200, "2026-11"))
        status, r = self.post("/fund/api/balances", {"account": "nope", "amount": 1})
        self.assertEqual((status, r["error"]), (400, "no account 'nope'"))  # Fund's own errors come back as is

    def test_guild_hall_rules_still_apply(self):
        # POST without the Guild Hall's header / origin is refused before it reaches Fund
        self.assertEqual(self.request("POST", "/fund/api/flows", {"amount": 1, "category": "x"})[0], 403)
        # external bearer-token callers (Grokbot over Funnel) never reach your money
        status, _ = self.request("GET", "/fund/api/summary", host="guild.example.ts.net",
                                 headers={"Authorization": "Bearer test-token-not-a-secret"})
        self.assertEqual(status, 403)
        # no path tricks
        self.assertEqual(self.request("GET", "/fund/../config.json")[0], 404)

    def test_fund_down(self):
        self.fund_proc.terminate()
        self.fund_proc.wait(5)
        try:
            status, page = self.request("GET", "/fund/")
            self.assertEqual(status, 502)
            self.assertIn(b"Fund belum jalan", page)
            status, r = self.request("GET", "/fund/api/summary")
            self.assertEqual(status, 502)
            self.assertIn("Fund belum jalan", r["error"])
        finally:
            type(self).fund_proc = subprocess.Popen(
                [sys.executable, "server.py", "--port", str(FUND_PORT), "--bind", "127.0.0.1"],
                cwd=str(self.fund), stdout=self.fund_log, stderr=subprocess.STDOUT)
            for _ in range(50):
                if self.request("GET", "/fund/api/meta")[0] == 200:
                    break
                time.sleep(0.1)


class FundAccess(unittest.TestCase):
    """Agents in The Counting House get the Fund commands; everyone else does not."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = {"fund_dir": str(self.tmp / "fund")}

    def member(self, division, model="sonnet"):
        folder = self.tmp / "agent"
        folder.mkdir(exist_ok=True)
        return {"name": "Sable", "division": division, "model": model, "folder": str(folder)}

    def settings(self):
        p = self.tmp / "agent" / ".claude" / "settings.json"
        return json.loads(p.read_text()) if p.exists() else None

    def test_rules_follow_the_division_and_keep_your_own(self):
        import fundaccess
        m = self.member("The Forge")
        fundaccess.sync_settings(m, self.cfg)
        self.assertIsNone(self.settings())  # nothing written for agents outside the Fund
        m["division"] = fundaccess.FUND_DIVISION
        fundaccess.sync_settings(m, self.cfg)
        allow = self.settings()["permissions"]["allow"]
        cli = str(self.tmp / "fund" / "fund.py")
        self.assertIn("Bash(python3 %s report:*)" % cli, allow)
        self.assertIn("Bash(python3 %s rec note:*)" % cli, allow)
        self.assertFalse([r for r in allow if " balance" in r or " spend" in r or " fx" in r or " budget" in r])
        # your own rule survives a move out; ours go
        data = self.settings()
        data["permissions"]["allow"].append("Bash(git status)")
        data["model"] = "sonnet"
        (self.tmp / "agent" / ".claude" / "settings.json").write_text(json.dumps(data))
        m["division"] = "The Forge"
        fundaccess.sync_settings(m, self.cfg)
        self.assertEqual(self.settings(), {"permissions": {"allow": ["Bash(git status)"]}, "model": "sonnet"})

    def test_grok_members_are_left_out(self):
        import fundaccess
        self.assertFalse(fundaccess.in_fund(self.member(fundaccess.FUND_DIVISION, model="grok")))

    def test_brief_names_the_agent(self):
        import fundaccess
        text = fundaccess.brief_section(self.cfg, "Sable")
        self.assertIn('--by "Sable"', text)
        self.assertIn("Think critically", text)


if __name__ == "__main__":
    unittest.main()
