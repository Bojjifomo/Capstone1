"""Sky over the guild town, from Bitcoin's last 24 hours.

CoinGecko's public price is fetched at most once every 10 minutes, server-side,
and cached in weather.json. A failure keeps the last sky and never raises:
the page just stays on a clear day.

    change >= -0.5   sunny   (green, or flat)
    -3 <= change     cloudy  (mildly red)
    change < -3      rain    (strongly red)
"""
import json
import threading
import time
import urllib.request

import store

URL = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
TTL = 600


def sky_for(change):
    if change is None:
        return "sunny"
    try:
        change = float(change)
    except (TypeError, ValueError):
        return "sunny"
    if change >= -0.5:
        return "sunny"
    if change >= -3:
        return "cloudy"
    return "rain"


class Weather:
    def __init__(self):
        saved = store.load("weather.json", {})
        self.cache = saved if isinstance(saved, dict) else {}
        self.lock = threading.Lock()

    def public(self):
        with self.lock:
            data = dict(self.cache)
        if not data.get("sky"):
            return {"sky": "sunny", "change": None, "ok": False, "at": 0}
        return {"sky": data.get("sky") or "sunny", "change": data.get("change"),
                "ok": bool(data.get("ok")), "at": data.get("at") or 0}

    def _fetch(self, opener):
        if opener is None:
            req = urllib.request.Request(URL, headers={"User-Agent": "agent-office", "Accept": "application/json"})

            def opener(target):
                with urllib.request.urlopen(req if target == URL else urllib.request.Request(target), timeout=4) as res:
                    return res.read().decode("utf-8", "replace")

        payload = json.loads(opener(URL))
        return float(payload["bitcoin"]["usd_24h_change"])

    def refresh(self, opener=None, now=None):
        """Fetch if the cache is stale. Network errors are ignored."""
        now = time.time() if now is None else now
        with self.lock:
            fresh = bool(self.cache.get("ok")) and now - (self.cache.get("at") or 0) < TTL
        if fresh:
            return self.public()
        try:
            change = self._fetch(opener)
            sky = sky_for(change)
            with self.lock:
                self.cache = {"sky": sky, "change": change, "ok": True, "at": now}
                store.save("weather.json", self.cache)
        except Exception:
            with self.lock:
                if not self.cache.get("sky"):
                    self.cache = {"sky": "sunny", "change": None, "ok": False, "at": 0}
        return self.public()
