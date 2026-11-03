"""The Fund app (../fund) behind the Guild Hall's own door, at /fund/.

Fund keeps running as its own program with its own database; the Guild Hall only forwards
/fund/... to it on the Mac, so the phone needs one address and one home-screen app. The Guild
Hall's host allowlist, https (tailscale serve) and POST origin check apply on the way in.
External bearer-token callers never get here: /fund is not in TOKEN_ROUTES.
"""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

REST_RE = re.compile(r"^/[A-Za-z0-9_\-./]*$")
MAX_REPLY = 4 * 1024 * 1024

DOWN_PAGE = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Fund</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body><main><h1>Fund belum jalan</h1>
<p>Guild Hall nggak bisa nyambung ke app Fund di %s.</p>
<p>Di Mac: <code>cd Capstone1/fund &amp;&amp; sh tools/install.sh</code>, terus buka lagi.</p>
<p><a href="/">&larr; Balik ke Guild Hall</a></p></main></body></html>"""


def base_url(cfg):
    url = str(cfg.get("fund_url") or "http://127.0.0.1:7799").strip().rstrip("/")
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("fund_url must be http(s)")
    return url


def forward(cfg, method, rest, query="", body=None, timeout=15):
    """(status, content_type, bytes) from Fund for /fund<rest>?<query>."""
    if not rest or not REST_RE.match(rest) or ".." in rest or "//" in rest:
        return 404, "application/json", b'{"error":"not found"}'
    base = base_url(cfg)
    url = base + rest + ("?" + query if query else "")
    headers = {"Accept": "*/*"}
    token = str(cfg.get("fund_token") or "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if method == "POST":
        data = json.dumps(body or {}).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read(MAX_REPLY)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "application/json"), e.read(MAX_REPLY)
    except (urllib.error.URLError, OSError):
        if rest.startswith("/api/"):
            return 502, "application/json", json.dumps({"error": "Fund belum jalan (%s)" % base}).encode()
        return 502, "text/html; charset=utf-8", (DOWN_PAGE % base).encode()
