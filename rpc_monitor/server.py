#!/usr/bin/env python3
"""RPC Monitor: one page that shows, for every bot, which RPC it uses and how close each one is to its limit.

    python3 rpc_monitor/server.py            dashboard on http://127.0.0.1:7795 (+ the Tailscale IP)
    python3 rpc_monitor/server.py --once     check once and print the table, no server

Standard library only. Binds to 127.0.0.1 and the Tailscale IP, never 0.0.0.0.
"""
import argparse
import hmac
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import monitor  # noqa: E402

STATIC = HERE / "static"
CONFIG_PATH = HERE / "config.json"

DEFAULT_CONFIG = {
    "port": 7795,
    "bind": ["127.0.0.1", "tailscale"],   # "tailscale" = whatever `tailscale ip -4` says
    "token": "",                          # optional; RPC_MONITOR_TOKEN in the env wins
    "tick_s": 60,                         # how often logs and meter files are read again
    "probe_every_s": 300,                 # one cheap request per RPC this often (Helius: 1 credit)
    "thresholds": {"warn_pct": 70, "near_pct": 90, "limited_near": 10},
    "plans": [],                          # your plans, checked before the built-in ones (see README)
    "endpoints": {},                      # per-RPC overrides by id ("host#hash" from the page)
    "extra_bots": [
        {"id": "sol_copy_bot", "name": "Sol Copy Bot", "chain": "solana", "labels": ["copy"],
         "keychain": [{"service": "Sol Copy Bot", "accounts": ["rpcs", "rpc", "RPC_URLS", "SOLANA_RPC_URL"]}]},
    ],
    "lp_exit_watcher_dir": "",            # empty: found from its launchd plist, ~/lp_exit_watcher or ~/Projects/
    "alert_every_s": 3 * 3600,            # the same RPC is reported to Telegram at most this often
}

TS_NET = ipaddress.ip_network("100.64.0.0/10")
TS_PATHS = ["tailscale", "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]


def load_config(path=CONFIG_PATH):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if Path(path).exists():
        try:
            cfg.update(json.loads(Path(path).read_text()))
        except ValueError as e:
            print("config.json ignored: %s" % e, file=sys.stderr)
    env = monitor.read_env(HERE / ".env")
    cfg["token"] = os.environ.get("RPC_MONITOR_TOKEN") or env.get("RPC_MONITOR_TOKEN") or cfg.get("token") or ""
    cfg["telegram_token"] = os.environ.get("RPC_MONITOR_TELEGRAM_TOKEN") or env.get("RPC_MONITOR_TELEGRAM_TOKEN", "")
    cfg["telegram_chat"] = os.environ.get("RPC_MONITOR_TELEGRAM_CHAT_ID") or env.get("RPC_MONITOR_TELEGRAM_CHAT_ID", "")
    return cfg


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


# ------------------------------------------------------------------ alerts ----
class Alerts:
    """Telegram message when an RPC gets to "hampir limit" or "LIMIT", and once when it is fine again."""

    def __init__(self, cfg):
        self.cfg, self.sent, self.level = cfg, {}, {}

    def check(self, snap):
        if not (self.cfg.get("telegram_token") and self.cfg.get("telegram_chat")):
            return
        now = time.time()
        for ep in snap["endpoints"]:
            lv, prev = ep["level"], self.level.get(ep["id"])
            self.level[ep["id"]] = lv
            bad = lv in ("hampir", "limit")
            if bad and (prev != lv or now - self.sent.get(ep["id"], 0) >= self.cfg["alert_every_s"]):
                self.sent[ep["id"]] = now
                icon = "🔴" if lv == "limit" else "🟠"
                bots = ", ".join(sorted({b["name"] for b in ep["bots"]}))
                self.send("%s RPC %s: %s\n%s\nBot: %s\n- %s" % (icon, ep["level_text"], ep["plan"].get("name", ep["host"]),
                          ep["masked"], bots, "\n- ".join(ep["reasons"][:3])))
            elif prev in ("hampir", "limit") and lv == "aman":
                self.send("🟢 RPC aman lagi: %s (%s)" % (ep["plan"].get("name", ep["host"]), ep["masked"]))

    def send(self, text):
        url = "https://api.telegram.org/bot%s/sendMessage" % self.cfg["telegram_token"]
        body = urllib.parse.urlencode({"chat_id": self.cfg["telegram_chat"], "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(url, data=body, timeout=10).read()
        except Exception as e:
            print("telegram failed: %s" % monitor.scrub(str(e)), file=sys.stderr)


# ------------------------------------------------------------------ http ----
def make_handler(mon, cfg):
    token = cfg["token"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "RPCMonitor/1"

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
                             "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
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
                if k == "rm_token":
                    given = given or v
            ok = bool(given) and hmac.compare_digest(given.encode(), token.encode())
            cookie = "rm_token=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict" % token \
                if ok and (q.get("t") or [""])[0] else None
            return ok, cookie

        def _route(self, post):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            ok, cookie = self._authed(q)
            if not ok:
                return self._send(401, b"401: add ?t=<token> to the URL once", "text/plain")
            extra = {"Set-Cookie": cookie} if cookie else None
            if u.path == "/api/status" and not post:
                return self._send(200, mon.snapshot or mon.tick(), extra=extra)
            if u.path == "/api/check" and post:
                return self._send(200, mon.tick(probe_now=True), extra=extra)
            if u.path in ("/", "/index.html") and not post:
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8", extra=extra)
            return self._send(404, {"error": "not found"})

        def do_GET(self):
            self._safe(False)

        def do_POST(self):
            self._safe(True)

        def _safe(self, post):
            try:
                self._route(post)
            except BrokenPipeError:
                pass
            except Exception:
                traceback.print_exc()
                self._send(500, {"error": "server error"})

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


# ------------------------------------------------------------------ text ----
ICON = {"?": "⚪", "aman": "🟢", "waspada": "🟡", "hampir": "🟠", "limit": "🔴"}


def table(snap):
    lines = ["RPC", "==="]
    for ep in snap["endpoints"]:
        n = ep["nums"]
        bits = []
        if "used_pct" in n:
            bits.append("bulan ini %s%% (proyeksi %s%%)" % (n["used_pct"], n["proj_pct"]))
        if "daily_pct" in n:
            bits.append("hari ini %s%%" % n["daily_pct"])
        if "calls_24h" in n:
            bits.append("24j %s req" % monitor.fmt(n["calls_24h"]))
        if n.get("limited_1h"):
            bits.append("429 1j: %d" % n["limited_1h"])
        pr = ep.get("probe")
        if pr:
            bits.append("probe %s %sms" % (pr["outcome"], pr["ms"]))
        lines.append("%s %-12s %-18s %s" % (ICON[ep["level"]], ep["level_text"], ep["plan"].get("name", ep["host"])[:18], ep["masked"]))
        lines.append("    bot: %s" % ", ".join("%s (%s)" % (b["name"], b["role"]) if b["role"] else b["name"] for b in ep["bots"]))
        if bits:
            lines.append("    " + " · ".join(bits))
        for r in ep["reasons"]:
            lines.append("    - " + r)
    lines += ["", "Bot", "==="]
    for b in snap["bots"]:
        run = {True: "jalan", False: "MATI", None: "-"}[b["running"]]
        last = monitor.ago(b["last_call"], snap["now"]) if b["last_call"] else "belum kehitung"
        lines.append("%s %-24s %-6s RPC terakhir: %s" % (ICON[b["level"]], b["name"][:24], run, last))
        for note in b["notes"]:
            lines.append("    ! " + note)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="check once (probe every RPC), print, exit")
    ap.add_argument("--json", action="store_true", help="with --once: JSON instead of the table")
    ap.add_argument("--port", type=int)
    ap.add_argument("--bind", action="append", help="address to listen on (repeatable)")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.port:
        cfg["port"] = args.port
    if args.bind:
        cfg["bind"] = args.bind
    mon = monitor.Monitor(cfg)
    if args.once:
        snap = mon.tick(probe_now=True)
        print(json.dumps(snap, indent=1) if args.json else table(snap))
        return

    alerts = Alerts(cfg)
    handler = make_handler(mon, cfg)
    for addr in cfg["bind"]:
        retry = addr not in ("127.0.0.1", "localhost", "::1")
        if addr == "tailscale":
            def later(port=cfg["port"]):
                while True:
                    ip = tailscale_ip()
                    if ip:
                        return serve_on(ip, port, handler, True)
                    time.sleep(30)
            threading.Thread(target=later, daemon=True).start()
            continue
        threading.Thread(target=serve_on, args=(addr, cfg["port"], handler, retry), daemon=True).start()
    try:
        while True:
            try:
                alerts.check(mon.tick())
            except Exception:
                traceback.print_exc()
            time.sleep(float(cfg["tick_s"]))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
