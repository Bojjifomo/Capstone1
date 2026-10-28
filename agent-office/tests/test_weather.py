"""Living town: Bitcoin sky, cached, and quiet when the network is down."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
import store  # noqa: E402
import weather  # noqa: E402


class Sky(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(weather.sky_for(2.4), "sunny")
        self.assertEqual(weather.sky_for(0), "sunny")
        self.assertEqual(weather.sky_for(-0.5), "sunny")
        self.assertEqual(weather.sky_for(-1.2), "cloudy")
        self.assertEqual(weather.sky_for(-3), "cloudy")
        self.assertEqual(weather.sky_for(-3.1), "rain")
        self.assertEqual(weather.sky_for(None), "sunny")
        self.assertEqual(weather.sky_for("nope"), "sunny")


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ao-sky-"))
        self.old = store.DATA_DIR
        store.DATA_DIR = self.tmp

    def tearDown(self):
        store.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_caches_and_a_failure_keeps_the_sky(self):
        sky = weather.Weather()
        body = json.dumps({"bitcoin": {"usd": 60000, "usd_24h_change": -4.2}})
        calls = {"n": 0}

        def opener(url):
            calls["n"] += 1
            self.assertIn("coingecko", url)
            return body

        got = sky.refresh(opener=opener, now=1000)
        self.assertEqual((got["sky"], round(got["change"], 1)), ("rain", -4.2))
        sky.refresh(opener=opener, now=1200)
        self.assertEqual(calls["n"], 1)

        def down(url):
            calls["n"] += 1
            raise OSError("offline")

        kept = sky.refresh(opener=down, now=1000 + weather.TTL + 5)
        self.assertEqual(kept["sky"], "rain")
        self.assertEqual(calls["n"], 2)
        saved = json.loads((self.tmp / "weather.json").read_text())
        self.assertEqual(saved["sky"], "rain")


class Route(unittest.TestCase):
    def test_weather_is_readable_by_a_token(self):
        self.assertTrue(server.token_route_allowed("GET", "/api/weather"))
