"""Offline tests: python3 -m unittest discover -s orchestrator/tests -t ."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from orchestrator import agent as A
from orchestrator import tools as T

sys.path.insert(0, str(T.RH_DIR))
import db  # noqa: E402


class SeededDB(unittest.TestCase):
    """A Wallet Finder database with one scored wallet and one Smart LP Finder placing."""
    W = "0x" + "ab" * 20

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "wallets.db"
        con = db.connect(self.path)
        con.execute("INSERT INTO wallets(addr, score) VALUES(?, 71.5)", (self.W,))
        con.execute("""INSERT INTO slf_ranks(addr, pool, from_block, to_block, rank, total, pnl_usd, computed_at)
                       VALUES(?, '0xpool', 1, 2, 3, 76, 512.0, 1700000000)""", (self.W,))
        con.commit()
        con.close()
        self.env = mock.patch.dict(os.environ, {"RHWF_DB": str(self.path)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class SqlTest(SeededDB):
    def test_select_runs_read_only(self):
        rows = json.loads(T.rh_sql({"sql": "SELECT addr, score FROM wallets"}))
        self.assertEqual(rows, [{"addr": self.W, "score": 71.5}])

    def test_writes_and_stacked_statements_are_refused(self):
        for sql in ("DELETE FROM wallets", "SELECT 1; DROP TABLE wallets", "PRAGMA table_info(wallets)"):
            with self.assertRaises(T.ToolError):
                T.rh_sql({"sql": sql})
        with self.assertRaises(T.ToolError):  # a write hidden in a CTE still hits the read-only connection
            T.rh_sql({"sql": "WITH x AS (SELECT 1) DELETE FROM wallets"})
        self.assertEqual(sqlite3.connect(self.path).execute("SELECT COUNT(*) FROM wallets").fetchone()[0], 1)

    def test_limit(self):
        out = T.rh_sql({"sql": "SELECT 1 UNION ALL SELECT 2", "limit": 1})
        self.assertIn("more rows", out)


class CliTest(SeededDB):
    def test_smart_lp_summary_next_to_our_score(self):
        out = json.loads(T.slf_summary({}))
        self.assertEqual(out["best"][0]["addr"], self.W)
        self.assertEqual(out["best"][0]["rank"], 3)
        self.assertEqual(out["best"][0]["our_score"], 71.5)
        self.assertEqual(out["pools"][0]["ranked"], 1)

    def test_status_and_web_check_are_json(self):
        self.assertEqual(json.loads(T.rh_status({}))["discovered"], 1)
        with mock.patch.dict(os.environ, {"VERCEL_PUBLISH_URL": ""}):
            self.assertIn("error", json.loads(T.web_check({}))["live"])

    def test_pools_is_json_and_checks_its_numbers(self):
        self.assertEqual(json.loads(T.rh_pools({"days": 7, "sort": "net_apr"})), [])
        with self.assertRaises(T.ToolError):
            T.rh_pools({"days": 0})

    def test_bad_wallet_never_reaches_the_cli(self):
        with mock.patch.object(T, "_rh") as rh:
            with self.assertRaises(T.ToolError):
                T.rh_wallet({"wallet": "0x123; rm -rf /"})
            rh.assert_not_called()


class Block(NS):
    pass


def msg(stop, *content):
    return NS(stop_reason=stop, content=list(content))


class AgentLoopTest(unittest.TestCase):
    def make(self, replies, auto_yes=False):
        ag = A.Agent.__new__(A.Agent)
        ag.client = mock.MagicMock()
        ag.client.beta.messages.create.side_effect = replies
        ag.tools = {t.name: t for t in T.all_tools()}
        ag.auto_yes, ag.verbose, ag.messages, ag.out = auto_yes, False, [], lambda *a: None
        return ag

    def test_tool_round_trip(self):
        use = Block(type="tool_use", id="t1", name="read_docs", input={"topic": "rh_wallet_finder"})
        ag = self.make([msg("tool_use", use), msg("end_turn", Block(type="text", text="beres"))])
        self.assertEqual(ag.ask("cek"), "beres")
        result = ag.messages[2]["content"][0]
        self.assertEqual((result["tool_use_id"], result["is_error"]), ("t1", False))
        self.assertIn("RH Wallet Finder", result["content"])
        kw = ag.client.beta.messages.create.call_args.kwargs
        self.assertEqual((kw["model"], kw["fallbacks"]), (A.MODEL, "default"))

    def test_write_tool_needs_a_yes(self):
        use = Block(type="tool_use", id="t1", name="web_publish", input={})
        with mock.patch.object(T, "_rh") as rh, mock.patch("sys.stdin.isatty", return_value=False):
            ag = self.make([msg("tool_use", use), msg("end_turn", Block(type="text", text="ok"))])
            ag.ask("publish")
            rh.assert_not_called()
            self.assertTrue(ag.messages[2]["content"][0]["is_error"])

    def test_bad_input_is_an_error_result_not_a_crash(self):
        use = Block(type="tool_use", id="t1", name="rh_wallet", input={"wallet": "nope"})
        ag = self.make([msg("tool_use", use), msg("end_turn", Block(type="text", text="ok"))])
        ag.ask("x")
        res = ag.messages[2]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertIn("not an EVM wallet", res["content"])


if __name__ == "__main__":
    unittest.main()
