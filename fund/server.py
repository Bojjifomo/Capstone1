#!/usr/bin/env python3
"""Fund: net worth, expenses and the fund manager's recommendations, from your phone.

Standard library only. Binds to 127.0.0.1 and the Tailscale IP, never 0.0.0.0.
Separate from the LP tools: the only link is an optional read-only look at the Wallet
Finder's database for your own LP wallets (lp.py), which costs no RPC.
"""
import argparse
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lp  # noqa: E402
import store  # noqa: E402

STATIC = HERE / "static"
CONFIG_PATH = HERE / "config.json"
MAX_BODY = 64 * 1024

DEFAULT_CONFIG = {
    "port": 7799,
    "bind": ["127.0.0.1", "tailscale"],
    "token": "",                 # optional; FUND_TOKEN in the env wins
    "db": "fund.db",
    "usd_idr": 16500,            # used until you enter a rate in the app
    "lp_wallets": [],            # your own LP wallets, 0x…; empty = LP tracking off
    "lp_db": "",                 # Wallet Finder database; empty = look next door
    "lp_every_minutes": 60,
}

TS_NET = ipaddress.ip_network("100.64.0.0/10")
TS_PATHS = ["tailscale", "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULT_CONFIG)
    if Path(path).exists():
        try:
            cfg.update(json.loads(Path(path).read_text()))
        except ValueError as e:
            print("config.json ignored: %s" % e, file=sys.stderr)
    cfg["token"] = os.environ.get("FUND_TOKEN", cfg.get("token") or "")
    return cfg


def db_path(cfg):
    p = Path(cfg["db"])
    return p if p.is_absolute() else HERE / p


def lp_db_path(cfg):
    """Where the Wallet Finder keeps its database: config, env, or the repo next door."""
    for p in (cfg.get("lp_db"), os.environ.get("RHWF_DB"), HERE.parent / "rh_wallet_finder" / "data" / "wallets.db"):
        if p and Path(p).expanduser().is_file():
            return Path(p).expanduser()
    return Path(cfg.get("lp_db") or HERE.parent / "rh_wallet_finder" / "data" / "wallets.db").expanduser()


def ip_from_ifconfig(text):
    for m in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)", text):
        try:
            if ipaddress.ip_address(m.group(1)) in TS_NET:
                return m.group(1)
        except ValueError:
            continue
    return None


def tailscale_ip():
    for ts in TS_PATHS:
        try:
            out = subprocess.run([ts, "ip", "-4"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        ip = out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
        if ip.startswith("100."):
            return ip
    for cmd in (["/sbin/ifconfig"], ["ifconfig"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        ip = ip_from_ifconfig(out.stdout)
        if ip:
            return ip
    return None


# ── LP: one cheap local read, written as a balance of the "LP (auto)" account ──────────

class LPTracker:
    def __init__(self, books, cfg):
        self.books, self.cfg = books, cfg
        self.last, self.error, self.lock = None, None, threading.Lock()

    @property
    def enabled(self):
        return bool(self.cfg.get("lp_wallets"))

    def refresh(self):
        if not self.enabled:
            return None
        with self.lock, self.books.lock:
            try:
                snap = lp.snapshot(lp_db_path(self.cfg), self.cfg["lp_wallets"])
            except (lp.Unavailable, Exception) as e:  # noqa: BLE001 - keep the app up, show why
                self.error = str(e) or e.__class__.__name__
                return None
            acc = self.books.ensure_auto_account("lp", "LP (auto)", "lp", "USD", 4)
            self.books.set_balance(acc, snap["total_usd"], note="open %s + idle %s USDG" % (
                snap["open_usd"], snap["idle_usd"]), allow_auto=True)
            self.last, self.error = snap, None
            return snap

    def loop(self):
        while True:
            self.refresh()
            time.sleep(max(5, int(self.cfg.get("lp_every_minutes") or 60)) * 60)

    def status(self, days=30):
        if not self.enabled:
            return {"enabled": False}
        out = {"enabled": True, "snapshot": self.last, "error": self.error}
        try:
            out["flows"] = lp.flows(lp_db_path(self.cfg), self.cfg["lp_wallets"], days)
        except (lp.Unavailable, Exception) as e:  # noqa: BLE001
            out["error"] = out["error"] or str(e)
        return out


# ── HTTP ────────────────────────────────────────────────────────────────────────────

def make_handler(books, tracker, cfg):
    token = cfg["token"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "Fund/1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _authed(self, q):
            if not token:
                return True, None
            given = (q.get("t") or [""])[0]
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                given = given or auth[7:]
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "fund_token":
                    given = given or v
            ok = bool(given) and hmac.compare_digest(given.encode(), token.encode())
            cookie = None
            if ok and (q.get("t") or [""])[0]:
                cookie = "fund_token=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict" % token
            return ok, cookie

        def _wrap(self, fn):
            try:
                with books.lock:
                    fn()
            except BrokenPipeError:
                pass
            except store.Invalid as e:
                self._send(400, {"error": str(e)})
            except Exception:
                traceback.print_exc()
                self._send(500, {"error": "server error"})

        def do_GET(self):
            self._wrap(self._get)

        def do_POST(self):
            self._wrap(self._post)

        def _get(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            ok, cookie = self._authed(q)
            if not ok:
                return self._send(401, b"401: add ?t=<token> to the URL once", "text/plain")
            extra = {"Set-Cookie": cookie} if cookie else None
            p = u.path
            arg = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
            if p == "/api/summary":
                return self._send(200, books.summary(arg("day")), extra=extra)
            if p == "/api/meta":
                return self._send(200, {"sleeves": store.SLEEVES, "kinds": store.KINDS,
                                        "categories": sorted({r["category"] for r in books.con.execute(
                                            "SELECT category FROM flows UNION SELECT category FROM budgets")}),
                                        "lp": tracker.enabled}, extra=extra)
            if p == "/api/flows":
                month = arg("month") or store.today()[:7]
                start = store._day(month + "-01", "month")
                y, m = int(start[:4]), int(start[5:7])
                end = "%04d-%02d-01" % (y + (m == 12), m % 12 + 1)
                return self._send(200, {"month": month, "flows": books.flows(start, end)}, extra=extra)
            if p == "/api/recs":
                return self._send(200, {"recs": books.recs(arg("status"))}, extra=extra)
            if p == "/api/balances":
                return self._send(200, {"balances": books.balance_history(arg("account", ""))}, extra=extra)
            if p == "/api/budgets":
                return self._send(200, {"budgets": [dict(r) for r in books.con.execute(
                    "SELECT * FROM budgets ORDER BY category")]}, extra=extra)
            if p == "/api/lp":
                days = min(365, max(1, int(arg("days", "30"))))
                return self._send(200, tracker.status(days), extra=extra)
            name = "index.html" if p in ("/", "") else p.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype, extra=extra)

        def _post(self):
            u = urlsplit(self.path)
            ok, _ = self._authed(parse_qs(u.query))
            if not ok:
                return self._send(401, {"error": "unauthorized"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(415, {"error": "send JSON"})  # forms from other sites can't send JSON
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "too large"})
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            if not isinstance(d, dict):
                return self._send(400, {"error": "send a JSON object"})
            p = u.path
            if p == "/api/accounts":
                return self._send(200, {"id": books.save_account(d)})
            if p == "/api/balances":
                return self._send(200, {"id": books.set_balance(d.get("account"), d.get("amount"), d.get("day"),
                                                                 d.get("note"))})
            if p == "/api/fx":
                books.set_fx(d.get("usd_idr"), d.get("day"))
                return self._send(200, {"ok": True})
            if p == "/api/flows":
                return self._send(200, {"id": books.add_flow(d)})
            if p == "/api/flows/delete":
                books.delete_flow(d.get("id"))
                return self._send(200, {"ok": True})
            if p == "/api/budgets":
                books.set_budget(d.get("category"), d.get("monthly"))
                return self._send(200, {"ok": True})
            if p == "/api/recs":
                if d.get("id"):
                    books.update_rec(d["id"], d)
                    return self._send(200, {"id": d["id"]})
                return self._send(200, {"id": books.add_rec(d, author="me")})
            if p == "/api/recs/note":
                return self._send(200, {"id": books.add_note(d.get("id"), d.get("body"), "me",
                                                             d.get("stance") or "question")})
            if p == "/api/lp/refresh":
                return self._send(200, {"snapshot": tracker.refresh(), "error": tracker.error})
            return self._send(404, {"error": "not found"})

    return Handler


def serve_on(addr, port, handler, retry):
    while True:
        try:
            httpd = ThreadingHTTPServer((addr, port), handler)
            httpd.daemon_threads = True
            print("listening on http://%s:%d" % (addr, port), file=sys.stderr)
            httpd.serve_forever()
        except OSError as e:
            if not retry:
                raise
            print("bind %s:%d failed (%s), retrying in 30s" % (addr, port, e), file=sys.stderr)
            time.sleep(30)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int)
    ap.add_argument("--bind", action="append", help="address to listen on (repeatable)")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.port:
        cfg["port"] = args.port
    if args.bind:
        cfg["bind"] = args.bind

    books = store.Books(db_path(cfg), cfg["usd_idr"])
    tracker = LPTracker(books, cfg)
    if tracker.enabled:
        threading.Thread(target=tracker.loop, daemon=True).start()
    handler = make_handler(books, tracker, cfg)

    threads = []
    for addr in cfg["bind"]:
        retry = addr not in ("127.0.0.1", "localhost", "::1")
        if addr == "tailscale":
            addr = tailscale_ip()
            if not addr:
                def later(port=cfg["port"]):
                    while True:
                        ip = tailscale_ip()
                        if ip:
                            return serve_on(ip, port, handler, True)
                        time.sleep(30)
                threads.append(threading.Thread(target=later, daemon=True))
                print("no Tailscale IP yet, will keep looking", file=sys.stderr)
                continue
        threads.append(threading.Thread(target=serve_on, args=(addr, cfg["port"], handler, retry), daemon=True))
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
