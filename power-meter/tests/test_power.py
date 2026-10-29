import json
import os
import plistlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import meter  # noqa: E402
import server  # noqa: E402
import sources  # noqa: E402

MACMON_LINE = json.dumps({
    "timestamp": "2026-10-29T10:00:00Z", "temp": {"cpu_temp_avg": 44.5, "gpu_temp_avg": 38.0},
    "memory": {"ram_total": 1, "ram_usage": 1}, "ecpu_usage": [1181, 0.12], "pcpu_usage": [3200, 0.55],
    "gpu_usage": [900, 0.2], "cpu_power": 6.1, "gpu_power": 1.4, "ane_power": 0.0, "all_power": 7.5,
    "sys_power": 14.2, "ram_power": 0.4, "gpu_ram_power": 0.01,
})


class Parsers(unittest.TestCase):
    def test_macmon_uses_system_power(self):
        s = sources.parse_macmon(MACMON_LINE)
        self.assertEqual(s["watts"], 14.2)
        self.assertEqual(s["chip_w"], 7.5)
        self.assertEqual(s["cpu_w"], 6.1)
        self.assertEqual(s["cpu_usage"], 0.55)
        self.assertEqual(s["gpu_usage"], 0.2)
        self.assertEqual(s["cpu_temp"], 44.5)

    def test_macmon_without_sys_power_falls_back_to_chip(self):
        d = json.loads(MACMON_LINE)
        del d["sys_power"], d["all_power"]
        s = sources.parse_macmon(json.dumps(d))
        self.assertAlmostEqual(s["watts"], 7.5)

    def test_macmon_junk(self):
        self.assertIsNone(sources.parse_macmon("not json"))
        self.assertIsNone(sources.parse_macmon("[1,2]"))
        self.assertIsNone(sources.parse_macmon("{}"))

    def test_powermetrics_milliwatts(self):
        doc = plistlib.dumps({"processor": {"cpu_power": 3210.0, "gpu_power": 450.0, "ane_power": 0.0,
                                            "combined_power": 3660.0}})
        s = sources.parse_powermetrics(b"\n" + doc + b"\0")
        self.assertAlmostEqual(s["watts"], 3.66)
        self.assertAlmostEqual(s["cpu_w"], 3.21)
        self.assertIsNone(s["system_w"])
        self.assertIsNone(sources.parse_powermetrics(b"\0"))


class MeterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = meter.Meter(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_energy_integrates_and_survives_flush(self):
        t0 = (time.time() // 60) * 60 - 600
        for i in range(121):                     # two minutes at 36 W = 1.2 Wh
            self.m.add({"watts": 36.0, "cpu_w": 20.0, "gpu_w": 4.0}, now=t0 + i)
        self.assertAlmostEqual(self.m.energy_since(t0), 1.2, places=6)
        self.m.flush()
        self.assertAlmostEqual(self.m.energy_since(t0), 1.2, places=6)
        h = self.m.history(t0, 60)
        self.assertEqual([r[0] for r in h], [t0, t0 + 60, t0 + 120])
        self.assertEqual(h[0][1], 36.0)

    def test_gap_counts_no_energy(self):
        t0 = time.time() - 1000
        self.m.add({"watts": 50.0}, now=t0)
        self.m.add({"watts": 50.0}, now=t0 + 600)    # Mac slept for 10 minutes
        self.assertEqual(self.m.energy_since(t0 - 1), 0.0)

    def test_partial_minute_flush_merges(self):
        t0 = (time.time() // 60) * 60 - 300
        for i in range(30):
            self.m.add({"watts": 10.0}, now=t0 + i)
        self.m.flush()
        for i in range(30, 60):
            self.m.add({"watts": 20.0}, now=t0 + i)
        self.m.flush()
        row = self.m.db.execute("SELECT avg_w, max_w, min_w, n FROM minutes WHERE ts=?", (t0,)).fetchone()
        self.assertAlmostEqual(row[0], 15.0)
        self.assertEqual((row[1], row[2], row[3]), (20.0, 10.0, 60))

    def test_daily_has_today(self):
        now = time.time()
        for i in range(10, 0, -1):
            self.m.add({"watts": 36.0}, now=now - i)
        days = self.m.daily(3)
        self.assertEqual(len(days), 3)
        self.assertGreater(days[-1][1], 0)


class Http(unittest.TestCase):
    def start(self, token=""):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.m = meter.Meter(os.path.join(self.tmp.name, "t.db"))
        now = time.time()
        for i in range(5):
            self.m.add({"watts": 22.0, "cpu_w": 15.0, "gpu_w": 2.0}, now=now - 5 + i)
        cfg = dict(server.DEFAULT_CONFIG, token=token)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.m, cfg))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        self.base = "http://127.0.0.1:%d" % httpd.server_address[1]

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_now_live_and_page(self):
        self.start()
        code, body, _ = self.get("/api/now")
        d = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(d["state"], "busy")
        self.assertEqual(d["sample"]["watts"], 22.0)
        self.assertGreater(d["today_wh"], 0)
        code, body, _ = self.get("/api/live?seconds=60")
        self.assertEqual(len(json.loads(body)["points"]), 5)
        code, body, _ = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Mac mini", body)
        self.assertEqual(self.get("/../server.py")[0], 404)
        self.assertEqual(json.loads(self.get("/api/daily?days=7")[1])["days"].__len__(), 7)
        self.assertEqual(self.get("/api/history?hours=24")[0], 200)

    def test_token(self):
        self.start(token="s3cret")
        self.assertEqual(self.get("/api/now")[0], 401)
        self.assertEqual(self.get("/api/now?t=nope")[0], 401)
        code, _, h = self.get("/?t=s3cret")
        self.assertEqual(code, 200)
        self.assertIn("pm_token=s3cret", h["Set-Cookie"])
        self.assertEqual(self.get("/api/now", {"Cookie": "pm_token=s3cret"})[0], 200)
        self.assertEqual(self.get("/api/now", {"Authorization": "Bearer s3cret"})[0], 200)

    def test_offline_when_stale(self):
        self.start()
        d = server.status_of(self.m, server.DEFAULT_CONFIG, now=time.time() + 60)
        self.assertEqual(d["state"], "offline")


if __name__ == "__main__":
    unittest.main()
