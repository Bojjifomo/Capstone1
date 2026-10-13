"""Discovery against a local stand-in single-page app (not synths.lol)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from synthmon.discover import classify, shape, templatize

from .conftest import OWN, WALLET

APP = """<!doctype html><html><body><div id="app"></div><script>
const p = location.pathname, app = document.getElementById('app');
const get = u => fetch(u).then(r => r.json());
if (p === '/') { get('/api/feed'); app.innerHTML = '<a href="/bojjibot">bojjibot</a>'; }
else if (p === '/bojjibot') {
  get('/api/synths/bojjibot');
  app.innerHTML = '<h1>@bojjibot</h1><a href="/coin/%(own)s">BOJJI</a>' +
    ['Posts','Bags','Trades','Launched'].map(t => `<button role="tab" onclick="get('/api/synths/bojjibot/${t.toLowerCase()}')">${t}</button>`).join('');
} else if (p.startsWith('/coin/')) { get('/api/coins/' + p.split('/')[2]); app.textContent = 'coin'; }
else if (p === '/leaderboard') { get('/api/leaderboard'); app.textContent = 'leaderboard'; }
</script></body></html>""" % {"own": OWN}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/"):
            body, ctype = json.dumps({"data": [{"path": self.path, "wallet": WALLET}]}).encode(), "application/json"
        elif self.path in ("/", "/bojjibot", "/leaderboard") or self.path.startswith("/coin/"):
            body, ctype = APP.encode(), "text/html"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)


def test_helpers():
    assert classify("https://x/api/synths/bojjibot/posts?limit=20", "profile") == "posts"
    assert classify("https://x/api/v1/agent/abc", "profile") == "profile"
    assert classify("https://x/api/q", "leaderboard") == "leaderboard"
    url = f"https://x/api/coins/{OWN.upper().replace('0X', '0x')}?holder={WALLET}&h=bojjibot"
    assert templatize(url, "bojjibot", WALLET, {OWN}) == "https://x/api/coins/{coin}?holder={wallet}&h={handle}"
    assert shape({"a": [{"b": WALLET, "c": 1.5}]}) == {"a": [{"b": "address", "c": "float"}, "len=1"]}


def test_discovery_end_to_end(tmp_path):
    pytest.importorskip("playwright")
    from synthmon.discover import run_discovery

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        res = run_discovery(base, "bojjibot", str(tmp_path), wallet=WALLET, wait_ms=300)
    finally:
        server.shutdown()
    assert res["profile_found"] and res["coins"] == [OWN]
    s = res["suggestions"]
    assert s["posts"] == f"{base}/api/synths/{{handle}}/posts"
    assert s["bags"] == f"{base}/api/synths/{{handle}}/bags"
    assert s["trades"] == f"{base}/api/synths/{{handle}}/trades"
    assert s["launched"] == f"{base}/api/synths/{{handle}}/launched"
    assert s["coin"] == f"{base}/api/coins/{{coin}}"
    assert s["leaderboard"] == f"{base}/api/leaderboard"
    assert s["profile"] == f"{base}/api/synths/{{handle}}"
    assert (tmp_path / "suggested_config.toml").read_text().startswith("# Suggested")
    assert len(list((tmp_path / "samples").glob("*.json"))) == res["captured"]
