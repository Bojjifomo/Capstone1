"""Offline tests: python3 -m unittest discover -s rpc_monitor/tests"""
import calendar
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import meter  # noqa: E402
import monitor  # noqa: E402

NOW = calendar.timegm((2026, 11, 11, 12, 0, 0))  # 10.5 days into a 30-day month


def write_usage(folder, rows):
    folder.mkdir(parents=True, exist_ok=True)
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(r["t"]))
        with open(folder / ("usage-%s.jsonl" % day), "a") as f:
            f.write(json.dumps(r) + "\n")


class MeterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"RPC_MONITOR_DIR": self.tmp.name})
        self.env.start()
        meter.IN_TESTS = True

    def tearDown(self):
        meter.IN_TESTS = None
        self.env.stop()
        self.tmp.cleanup()

    def test_counts_are_flushed_without_the_key(self):
        url = "https://mainnet.helius-rpc.com/?api-key=SECRETSECRETSECRET1234"
        for _ in range(5):
            meter.hit("dlmm_terminal", url)
        meter.hit("dlmm_terminal", url, "limited", 2)
        meter.flush()
        files = list(Path(self.tmp.name).glob("usage-*.jsonl"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text()
        self.assertNotIn("SECRET", text)
        row = json.loads(text.splitlines()[0])
        self.assertEqual((row["ok"], row["limited"], row["bot"]), (5, 2, "dlmm_terminal"))
        self.assertEqual(row["ep"], meter.endpoint_id(url + "/"))

    def test_silent_inside_test_runs_by_default(self):
        meter.IN_TESTS = None
        meter.hit("x", "https://a.example/rpc")
        meter.flush()
        self.assertEqual(list(Path(self.tmp.name).glob("*")), [])


class HelpersTest(unittest.TestCase):
    def test_mask_and_scrub(self):
        self.assertEqual(monitor.mask("https://rpc.solidrpc.io/ak_1234567890abcdefghij/evm/4663"),
                         "https://rpc.solidrpc.io/ak_1234…ghij/evm/4663")
        self.assertNotIn("abcdefghijklmnop", monitor.mask("https://mainnet.helius-rpc.com/?api-key=abcdefghijklmnopqrst"))
        self.assertNotIn("abcdefghijklmnopqrstuv", monitor.scrub("429 at https://x/v2/abcdefghijklmnopqrstuv"))

    def test_bare_alchemy_key_is_the_same_endpoint_everywhere(self):
        key = "AbCdEfGhIjKlMnOpQrSt"
        self.assertEqual(meter.endpoint_id(monitor.rpc_link(key)),
                         meter.endpoint_id(monitor.ALCHEMY_RPC.format(key=key)))

    def test_plan_matching(self):
        self.assertEqual(monitor.plan_for("https://mainnet.helius-rpc.com/?api-key=x", {})["name"], "Helius Free")
        mine = {"plans": [{"name": "Helius Dev", "match": "helius", "monthly": 10_000_000}]}
        self.assertEqual(monitor.plan_for("https://mainnet.helius-rpc.com/?api-key=x", mine)["name"], "Helius Dev")


class DiscoverTest(unittest.TestCase):
    def test_shared_key_groups_bots(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            for sub, text in {
                "rh_wallet_finder/.env": "RPC_URL=https://rpc.solidrpc.io/ak_1234567890abcdefghij/evm/4663\nCALL_RPC_URL=AbCdEfGhIjKlMnOpQrSt\n",
                "lp_call_bot/.env": "RPC_URL=\nBACKUP_RPC_URLS=https://rpc.solidrpc.io/ak_1234567890abcdefghij/evm/4663,AbCdEfGhIjKlMnOpQrSt\n",
                "rh_terminal/.env": "ALCHEMY_API_KEY=AbCdEfGhIjKlMnOpQrSt\n",
                "dlmm_terminal/.env": "SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=abcdefabcdef\n",
            }.items():
                (repo / sub).parent.mkdir(parents=True, exist_ok=True)
                (repo / sub).write_text(text)
            with mock.patch.object(monitor, "REPO", repo), mock.patch.object(monitor, "SCRIPTABLE", repo / "none"), \
                    mock.patch.object(monitor, "keychain", return_value=""), \
                    mock.patch.object(Path, "home", return_value=repo / "home"):
                bots = monitor.discover({"extra_bots": []}, agents=[])
                m = monitor.Monitor({"data_dir": str(repo / "m")})
                m.bots = bots
                eps = m.endpoints()
        ids = {b["id"] for b in bots}
        self.assertEqual(ids, {"rh_wallet_finder", "lp_call_bot", "rh_terminal", "dlmm_terminal"})
        alchemy = [e for e in eps.values() if "alchemy" in e["host"]]
        self.assertEqual(len(alchemy), 1)
        self.assertEqual({b["id"] for b in alchemy[0]["bots"]}, {"rh_wallet_finder", "lp_call_bot", "rh_terminal"})
        public = [e for e in eps.values() if e["host"] == "rpc.mainnet.chain.robinhood.com"][0]
        self.assertEqual({b["id"] for b in public["bots"]}, {"lp_call_bot", "rh_terminal"})
        self.assertTrue(all("abcdefghij" not in e["masked"] for e in eps.values()))


class AssessTest(unittest.TestCase):
    def ep(self, rows, plan, probe=None, logs=()):
        with tempfile.TemporaryDirectory() as d:
            write_usage(Path(d), rows)
            u = monitor.read_usage(NOW, d).get("h#1")
        ep = {"id": "h#1", "plan": plan, "usage": u, "probe": probe, "log_events": list(logs)}
        return monitor.assess(ep, {}, NOW)

    def row(self, t, ok=0, limited=0, quota=0, bot="b"):
        return {"t": t, "bot": bot, "ep": "h#1", "host": "h", "ok": ok, "limited": limited, "quota": quota, "error": 0}

    def test_projection_near_monthly_limit(self):
        # 85k used, 9k in the last day, 19.5 days to go -> ~260k by month end on a 280k plan
        rows = [self.row(NOW - 10 * 86400, ok=76_000), self.row(NOW - 3600, ok=9_000)]
        ep = self.ep(rows, {"monthly": 280_000, "per_call": 1, "unit": "credit"})
        self.assertEqual(ep["level"], "hampir", ep["reasons"])
        self.assertGreater(ep["nums"]["proj_pct"], 90)

    def test_light_use_is_fine(self):
        ep = self.ep([self.row(NOW - 3600, ok=100)], {"monthly": 1_000_000, "per_call": 1, "rps": 10})
        self.assertEqual(ep["level"], "aman", ep["reasons"])

    def test_quota_reply_is_limit(self):
        ep = self.ep([self.row(NOW - 600, ok=10, quota=1)], {"monthly": 1_000_000})
        self.assertEqual(ep["level"], "limit")

    def test_many_429s_and_busy_minute(self):
        ep = self.ep([self.row(NOW - 600, ok=600, limited=12)], {"monthly": 0, "rps": 10})
        self.assertEqual(ep["level"], "hampir")
        self.assertEqual(ep["nums"]["rps_pct"], 102)

    def test_log_lines_and_probe(self):
        logs = [{"t": NOW - 60, "bot": "b", "kind": "limited", "line": "HTTP 429"}]
        probe = {"t": NOW - 30, "outcome": "ok", "headers": {"x-ratelimit-remaining": "3", "x-ratelimit-limit": "100"}, "msg": ""}
        ep = self.ep([], {"public": True}, probe=probe, logs=logs)
        self.assertEqual(ep["level"], "hampir")
        self.assertEqual(ep["nums"]["header_room_pct"], 3)


class LogWatchTest(unittest.TestCase):
    def test_only_new_lines_count(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "bot.log"
            log.write_text("old 429 Too Many Requests\n")
            bots = [{"id": "b", "logs": [str(log)], "endpoints": [{"url": "https://rpc.solidrpc.io/x"}]}]
            w = monitor.LogWatch(Path(d) / "state.json")
            w.scan(bots, NOW)
            self.assertEqual(w.events, [])
            with open(log, "a") as f:
                f.write("rpc.solidrpc.io: rate limited (429)\nall good\nMonthly capacity limit exceeded\n")
            w.scan(bots, NOW)
            self.assertEqual([e["kind"] for e in w.events], ["limited", "quota"])
            self.assertEqual(w.events[0]["host"], "rpc.solidrpc.io")
            again = monitor.LogWatch(Path(d) / "state.json")
            again.scan(bots, NOW)
            self.assertEqual(len(again.events), 2)


class ProbeTest(unittest.TestCase):
    def serve(self, code, body, headers=None):
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length", 0)))
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d/" % srv.server_port

    def test_ok_with_headers(self):
        url = self.serve(200, '{"jsonrpc":"2.0","id":1,"result":"0x1237"}', {"X-RateLimit-Remaining": "40"})
        r = monitor.probe(url, "evm")
        self.assertEqual((r["outcome"], r["headers"]), ("ok", {"x-ratelimit-remaining": "40"}))

    def test_429(self):
        self.assertEqual(monitor.probe(self.serve(429, "Too Many Requests"), "evm")["outcome"], "limited")

    def test_quota(self):
        url = self.serve(429, '{"error":{"code":429,"message":"Monthly capacity limit exceeded"}}')
        self.assertEqual(monitor.probe(url, "evm")["outcome"], "quota")


if __name__ == "__main__":
    unittest.main()
