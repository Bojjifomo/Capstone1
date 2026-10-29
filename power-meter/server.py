#!/usr/bin/env python3
"""Power Meter: how many watts the Mac mini is pulling right now, from anywhere.

Standard library only. Binds to 127.0.0.1 and the Tailscale IP, never 0.0.0.0.
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

import meter  # noqa: E402
import sources  # noqa: E402

STATIC = HERE / "static"
CONFIG_PATH = HERE / "config.json"

DEFAULT_CONFIG = {
    "port": 7788,
    "bind": ["127.0.0.1", "tailscale"],   # "tailscale" = whatever `tailscale ip -4` says
    "source": "auto",                     # auto | macmon | powermetrics | fake
    "interval_ms": 1000,
    "busy_watts": 15,                     # at or above this the Mac counts as working
    "price_per_kwh": 1444.70,             # PLN R-1 > 1300 VA, Rp per kWh
    "currency": "Rp",
    "token": "",                          # optional; POWER_METER_TOKEN in the env wins
    "db": "power.db",
    "keep_days": 90,
}

TS_NET = ipaddress.ip_network("100.64.0.0/10")
TS_PATHS = ["tailscale", "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except ValueError as e:
            print("config.json ignored: %s" % e, file=sys.stderr)
    cfg["token"] = os.environ.get("POWER_METER_TOKEN", cfg.get("token") or "")
    return cfg


def ip_from_ifconfig(text):
    """First address in Tailscale's 100.64.0.0/10 range in `ifconfig` output."""
    for m in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)", text):
        try:
            if ipaddress.ip_address(m.group(1)) in TS_NET:
                return m.group(1)
        except ValueError:
            continue
    return None


def tailscale_ip():
    """The Mac's Tailscale IPv4. Asks the CLI first; launchd often cannot run it (not on its
    PATH, or the app binary refuses), so fall back to reading the network interfaces."""
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


def status_of(m, cfg, now=None):
    now = time.time() if now is None else now
    s = m.latest
    today0 = meter.local_midnight(now)
    wh_today = m.energy_since(today0)
    hour = m.live_since(now - 3600)
    avg_1h = sum(p[1] for p in hour) / len(hour) if hour else None
    price = float(cfg["price_per_kwh"])
    stale = s is None or now - s["ts"] > max(10, cfg["interval_ms"] / 1000.0 * 5)
    if stale:
        state = "offline"
    elif s["watts"] >= float(cfg["busy_watts"]):
        state = "busy"
    else:
        state = "idle"
    return {
        "now": now,
        "state": state,
        "sample": s,
        "source": m.source,
        "error": m.error,
        "avg_1h_w": round(avg_1h, 2) if avg_1h is not None else None,
        "today_wh": round(wh_today, 2),
        "today_cost": round(wh_today / 1000.0 * price, 0),
        "month_projection_kwh": round(avg_1h * 24 * 30 / 1000.0, 2) if avg_1h is not None else None,
        "price_per_kwh": price,
        "currency": cfg["currency"],
        "busy_watts": cfg["busy_watts"],
    }


def sampler(m, cfg):
    while True:
        try:
            name, stream = sources.open_stream(cfg["source"], int(cfg["interval_ms"]))
            m.source = name
            print("reading power from %s" % name, file=sys.stderr)
            for s in stream:
                m.add(s)
        except Exception as e:  # keep the page up and tell it why there is no data
            m.error = str(e) or e.__class__.__name__
            print("source failed: %s" % m.error, file=sys.stderr)
        time.sleep(5)


def make_handler(m, cfg):
    token = cfg["token"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "PowerMeter/1"

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
                if k == "pm_token":
                    given = given or v
            ok = bool(given) and hmac.compare_digest(given.encode(), token.encode())
            cookie = None
            if ok and (q.get("t") or [""])[0]:
                cookie = "pm_token=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict" % token
            return ok, cookie

        def do_GET(self):
            try:
                self._get()
            except BrokenPipeError:
                pass
            except Exception:
                traceback.print_exc()
                self._send(500, {"error": "server error"})

        def _get(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            ok, cookie = self._authed(q)
            if not ok:
                return self._send(401, b"401: add ?t=<token> to the URL once", "text/plain")
            extra = {"Set-Cookie": cookie} if cookie else None
            p = u.path
            now = time.time()
            if p == "/api/now":
                return self._send(200, status_of(m, cfg, now), extra=extra)
            if p == "/api/live":
                secs = min(3600, max(10, int((q.get("seconds") or ["600"])[0])))
                return self._send(200, {"points": m.live_since(now - secs)}, extra=extra)
            if p == "/api/window":
                secs = min(3600, max(2, int((q.get("seconds") or ["300"])[0])))
                return self._send(200, m.window_stats(secs, now), extra=extra)
            if p == "/api/history":
                hours = min(24 * 90, max(1, int((q.get("hours") or ["24"])[0])))
                bucket = 60 if hours <= 6 else 300 if hours <= 48 else 3600
                return self._send(200, {"bucket": bucket, "points": m.history(now - hours * 3600, bucket)},
                                  extra=extra)
            if p == "/api/daily":
                days = min(90, max(1, int((q.get("days") or ["14"])[0])))
                price = float(cfg["price_per_kwh"])
                rows = [r + [round(r[1] / 1000.0 * price, 0)] for r in m.daily(days)]
                return self._send(200, {"days": rows, "currency": cfg["currency"]}, extra=extra)
            name = "index.html" if p in ("/", "") else p.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype, extra=extra)

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
    ap.add_argument("--source", help="auto | macmon | powermetrics | fake")
    ap.add_argument("--port", type=int)
    ap.add_argument("--bind", action="append", help="address to listen on (repeatable)")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.source:
        cfg["source"] = args.source
    if args.port:
        cfg["port"] = args.port
    if args.bind:
        cfg["bind"] = args.bind

    db = Path(cfg["db"])
    m = meter.Meter(str(db if db.is_absolute() else HERE / db), keep_days=int(cfg["keep_days"]))
    threading.Thread(target=sampler, args=(m, cfg), daemon=True).start()
    handler = make_handler(m, cfg)

    threads = []
    for addr in cfg["bind"]:
        retry = addr not in ("127.0.0.1", "localhost", "::1")   # a Tailscale IP may not exist yet at login
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
        threads.append(threading.Thread(target=serve_on, args=(addr, cfg["port"], handler, retry),
                                        daemon=True))
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(60)
            m.flush()
    except KeyboardInterrupt:
        pass
    finally:
        m.flush()


if __name__ == "__main__":
    main()
