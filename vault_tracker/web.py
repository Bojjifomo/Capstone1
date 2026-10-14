"""Local web dashboard: start/stop the tracker, edit settings, see allocation, positions and alerts.

Runs on this computer only (127.0.0.1) unless --host is given; then every request needs the
access token printed at startup.
"""

import ctypes
import errno
import http.server
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http import cookies
from urllib.parse import parse_qs, urlparse

from solders.pubkey import Pubkey

from . import config, copytrade, dashboard
from .alerts import DEFAULT_TYPES, Notifier, plain
from .runner import Session

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
ALL_TYPES = sorted(DEFAULT_TYPES | {"CLAIM", "TRANSFER_IN", "TRANSFER_OUT"})


class StopRequested(Exception):
    pass


class KeepAwake:
    """Stop the computer from idle-sleeping while the tracker runs (macOS, Windows)."""

    supported = sys.platform == "darwin" or os.name == "nt"

    def __init__(self):
        self.proc = None
        self.active = False

    def acquire(self):
        try:
            if sys.platform == "darwin":
                self.proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
                self.active = True
            elif os.name == "nt":  # per-thread: call from the worker thread
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
                self.active = True
        except Exception:
            self.active = False
        return self.active

    def release(self):
        try:
            if self.proc:
                self.proc.terminate()
            elif os.name == "nt" and self.active:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass
        self.proc, self.active = None, False


def _addr_error(value, what, required):
    if not value:
        return f"{what} wajib diisi" if required else None
    try:
        Pubkey.from_string(value)
    except ValueError:
        return f"{what} bukan alamat Solana yang valid (isi alamat base58, bukan slug Hedgin)"
    return None


def validate(cfg):
    errors = {}
    for key, what, required in (("VAULT_ADDRESS", "Alamat vault", True), ("FOLLOWER_ADDRESS", "Wallet kamu", False)):
        e = _addr_error(cfg.get(key, ""), what, required)
        if e:
            errors[key] = e
    try:
        copytrade.parse_mode(cfg.get("COPY_MODE") or "mirror")
    except ValueError as e:
        errors["COPY_MODE"] = str(e)
    for key, lo in (("POLL_INTERVAL", 5), ("HISTORY_LIMIT", 10), ("MIN_ALERT_USD", 0)):
        try:
            if float(cfg.get(key) or 0) < lo:
                errors[key] = f"minimal {lo}"
        except ValueError:
            errors[key] = "harus angka"
    bad = [t for t in (cfg.get("ALERT_TYPES") or "").split(",") if t and t not in ALL_TYPES]
    if bad:
        errors["ALERT_TYPES"] = f"tipe tidak dikenal: {', '.join(bad)}"
    return errors


def _mask(key, value):
    if not value:
        return ""
    if key == "SOLANA_RPC_URL":
        host = urlparse(value).hostname or "RPC"
        return f"{host} (tersimpan)"
    return "tersimpan"


def build_view(sess):
    """JSON-safe picture of the session for the page."""
    L, F = sess.leader, sess.follower

    def rows(tr, with_copy):
        out = []
        for r in (tr.snapshot or {}).get("positions", []):
            d = dict(r)
            d["meteora"] = f"https://app.meteora.ag/dlmm/{r['lb_pair']}"
            if with_copy:
                try:
                    d["copy"] = copytrade.suggest_range(r, sess.copy_mode)
                except ValueError:
                    d["copy"] = None
                d["copied"] = bool(F and copytrade.follower_positions_in_pool(F.snapshot, r["lb_pair"]))
            out.append(d)
        return sorted(out, key=lambda d: -(d.get("value_usd") or 0))

    def alloc(by):
        rs = dashboard.allocation_rows(L, by) if L.snapshot else []
        rs.sort(key=lambda r: -(r["value"] or 0))
        return {"main": dashboard.collapse_dust(rs), "all": rs}

    events = sorted(L.state["events"] + (F.state["events"] if F else []), key=lambda e: e.get("ts") or 0)
    shown = []
    for ev in reversed(events):
        if len(shown) >= 40:
            break
        try:
            title, lines = sess.formatter.format(ev)
        except Exception:
            continue
        if not sess.notifier.wants(ev, sess.follower_label):
            continue
        shown.append({"ts": ev.get("ts"), "type": ev["type"], "title": title, "historical": bool(ev.get("historical")),
                      "lines": [plain(l) for l in lines if not l.startswith(("🔗", "🔍"))],
                      "sig": ev.get("sig"), "wallet": ev.get("wallet")})

    sync = []
    if F:
        for p in copytrade.sync_status(L.snapshot, F.snapshot):
            sample = (p["leader"] or p["follower"])[0]
            sync.append({"pair": p["pair"], "lb_pair": p["lb_pair"], "status": p["status"],
                         "bin_step": sample["bin_step"],
                         "leader": len([r for r in p["leader"] if r["has_liquidity"]]),
                         "follower": len([r for r in p["follower"] if r["has_liquidity"]]),
                         "follower_in_range": p["follower_in_range"]})

    snap = L.snapshot or {}
    return {
        "leader": {"address": L.address, "display": L.display, "total_usd": snap.get("total_usd", 0),
                   "positions": rows(L, True), "holders": L.state["holders"],
                   "alloc": {"position": alloc("position"), "token": alloc("token")}},
        "follower": None if not F else {"address": F.address, "total_usd": (F.snapshot or {}).get("total_usd", 0),
                                        "positions": rows(F, False)},
        "sync": sync,
        "events": shown,
        "managers": [m for m in L.managers() if m["trades"]][:5],
        "price_source": L.prices.source,
        "rpc_host": urlparse(sess.rpc.url).hostname,
        "channels": sess.notifier.channels,
        "copy_mode": sess.copy_mode,
    }


class Controller:
    def __init__(self, env_path=".env", state_dir=".vault_tracker"):
        self.env_path = env_path
        self.state_dir = state_dir
        self.lock = threading.Lock()
        self.thread = None
        self.stop_evt = threading.Event()
        self.status = "stopped"   # stopped | starting | syncing | running | error
        self.message = ""
        self.last_check = None
        self.next_check = None
        self.errors = []
        self.view = None
        self.awake = KeepAwake()
        self.started_at = None
        self.session = None
        self.mirrors = {"status": "idle"}

    # ---- settings -------------------------------------------------------------

    def public_config(self):
        cfg = config.load(self.env_path)
        return {k: (_mask(k, v) if k in config.SECRETS else v) for k, v in cfg.items()}

    def save_config(self, updates):
        cfg = config.load(self.env_path)
        clean = {}
        for k, v in updates.items():
            if k not in config.FIELDS:
                continue
            v = str(v).strip()
            if k in config.SECRETS and (v == "" or v == _mask(k, cfg.get(k))):
                continue  # empty secret box = keep the stored value
            clean[k] = v
        for k in updates.get("_clear", []) or []:
            if k in config.SECRETS:
                clean[k] = ""
        merged = {**cfg, **clean}
        errors = validate(merged)
        if errors:
            return {"ok": False, "errors": errors}
        config.save(clean, self.env_path)
        restarted = False
        if self.running:  # apply the new settings: restart in the background
            def restart():
                self.stop(wait=True)
                self.start()
            threading.Thread(target=restart, daemon=True).start()
            restarted = True
        return {"ok": True, "restarted": restarted}

    # ---- lifecycle ------------------------------------------------------------

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return {"ok": True}
        cfg = config.load(self.env_path)
        errors = validate(cfg)
        if errors:
            return {"ok": False, "errors": errors}
        self.stop_evt.clear()
        self.errors = []
        self.status, self.message, self.started_at = "starting", "menyiapkan…", time.time()
        self.thread = threading.Thread(target=self._run, args=(cfg,), daemon=True)
        self.thread.start()
        return {"ok": True}

    def stop(self, wait=False):
        """Ask the worker to stop; it finishes the RPC call in flight first."""
        self.stop_evt.set()
        if self.running:
            self.status, self.message = "stopping", "berhenti setelah langkah ini selesai…"
            if wait:
                self.thread.join(timeout=120)
        return {"ok": True}

    def _error(self, e):
        self.errors = (self.errors + [{"ts": int(time.time()), "text": f"{type(e).__name__}: {e}"}])[-10:]

    def _run(self, cfg):
        sess = None
        try:
            if cfg.get("KEEP_AWAKE", "1") == "1":
                self.awake.acquire()
            sess = Session(
                cfg["VAULT_ADDRESS"], cfg.get("FOLLOWER_ADDRESS") or None,
                rpc_url=cfg.get("SOLANA_RPC_URL") or None, copy_mode=cfg.get("COPY_MODE") or "mirror",
                history=int(float(cfg.get("HISTORY_LIMIT") or 300)), state_dir=self.state_dir,
                alert_types=set(cfg["ALERT_TYPES"].split(",")) if cfg.get("ALERT_TYPES") else None,
                min_usd=float(cfg.get("MIN_ALERT_USD") or 5), name=cfg.get("TRACKER_NAME") or None)
            self.session = sess
            interval = max(5, int(float(cfg.get("POLL_INTERVAL") or 30)))
            self.status, self.message = "syncing", "baca histori transaksi…"

            def progress(t, i, n):
                if self.stop_evt.is_set():
                    raise StopRequested()
                self.message = f"[{t.label}] baca histori transaksi {i}/{n}…"
            sess.cycle(first=True, on_progress=progress)
            sess.announce()
            self._after_cycle(sess, interval)
            while not self.stop_evt.wait(timeout=max(0.0, self.next_check - time.time())):
                self.message = "cek on-chain…"
                try:
                    sess.cycle()
                except Exception as e:
                    self._error(e)
                self._after_cycle(sess, interval)
        except StopRequested:
            pass
        except Exception as e:
            traceback.print_exc()
            self._error(e)
            self.status, self.message = "error", str(e)
        finally:
            self.awake.release()
            self.next_check = None
            if self.status != "error":
                self.status, self.message = "stopped", ""

    def _after_cycle(self, sess, interval):
        view = build_view(sess)
        with self.lock:
            self.view = view
        self.last_check = time.time()
        self.next_check = self.last_check + interval
        self.status, self.message = "running", ""
        for e in sess.notifier.errors[-3:]:
            if not any(x["text"] == e for x in self.errors):
                self.errors = (self.errors + [{"ts": int(time.time()), "text": e}])[-10:]

    def state(self):
        with self.lock:
            view = self.view
        return {
            "status": self.status if (self.running or self.status == "error") else "stopped",
            "message": self.message,
            "last_check": self.last_check, "next_check": self.next_check, "now": time.time(),
            "errors": self.errors[-5:],
            "config": self.public_config(),
            "alert_types": ALL_TYPES, "default_alert_types": sorted(DEFAULT_TYPES),
            "keep_awake": {"supported": KeepAwake.supported, "active": self.awake.active},
            "view": view,
            "mirrors": self.mirrors,
        }

    def find_mirrors(self, body):
        from .mirrors import find_mirrors
        if self.mirrors.get("status") == "running":
            return {"ok": False, "error": "Pencarian masih berjalan."}
        sess = self.session
        if not (self.running and sess and sess.leader.bootstrapped):
            return {"ok": False, "error": "Klik Start dulu dan tunggu sampai RUNNING."}
        try:
            window = max(60, min(7200, int(body.get("window") or 600)))
            events = max(1, min(50, int(body.get("events") or 15)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "window dan events harus angka"}
        self.mirrors = {"status": "running", "progress": "mulai…", "window": window}

        def job():
            try:
                def progress(i, n, ev):
                    self.mirrors["progress"] = f"posisi vault {i}/{n}: baca transaksi pool di sekitar waktu buka…"
                rows, info = find_mirrors(sess.leader, window=window, max_events=events, on_progress=progress)
                self.mirrors = {"status": "done", "rows": rows[:30], "info": info, "finished": time.time()}
            except Exception as e:
                self.mirrors = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        threading.Thread(target=job, daemon=True).start()
        return {"ok": True}

    def test_alert(self):
        n = Notifier(log_path=None)
        if not n.channels:
            return {"ok": False, "error": "Isi Telegram bot token + chat id atau Discord webhook dulu, lalu Simpan."}
        n.send("✅ Test alert vault tracker", ["Kalau pesan ini masuk, alert sudah siap."])
        return {"ok": not n.errors, "error": " | ".join(n.errors) or None, "channels": n.channels}


def make_handler(ctrl, allowed_hosts, token):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "vault-tracker"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            if allowed_hosts and self.headers.get("Host", "") not in allowed_hosts:
                return False  # blocks DNS-rebinding pages from reaching the API
            if not token:
                return True
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            got = jar["vt_token"].value if "vt_token" in jar else ""
            return secrets.compare_digest(got, token)

        def do_GET(self):
            url = urlparse(self.path)
            if token and url.path == "/" and secrets.compare_digest(parse_qs(url.query).get("token", [""])[0], token):
                return self._send(302, b"", "text/plain", {
                    "Location": "/", "Set-Cookie": f"vt_token={token}; HttpOnly; SameSite=Strict; Path=/"})
            if not self._authorized():
                return self._send(403, b"Forbidden: buka link dengan ?token=... yang tampil di terminal",
                                  "text/plain; charset=utf-8")
            if url.path == "/":
                with open(STATIC, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                return self._send(200, ctrl.state())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorized() or self.headers.get("X-Requested-With") != "vault-tracker":
                return self._send(403, {"error": "forbidden"})
            length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            path = urlparse(self.path).path
            if path == "/api/start":
                return self._send(200, ctrl.start())
            if path == "/api/stop":
                return self._send(200, ctrl.stop())
            if path == "/api/config":
                return self._send(200, ctrl.save_config(body if isinstance(body, dict) else {}))
            if path == "/api/test-alert":
                return self._send(200, ctrl.test_alert())
            if path == "/api/mirrors":
                return self._send(200, ctrl.find_mirrors(body if isinstance(body, dict) else {}))
            self._send(404, {"error": "not found"})

    return Handler


def _already_running(port):
    """True when a Vault Tracker web server already answers on this port."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return "status" in json.loads(r.read())
    except Exception:
        return False


def _lan_ip():
    """This computer's address on the local network (what a phone on the same Wi-Fi uses)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 9))  # no packet is sent for UDP connect
            return s.getsockname()[0]
    except OSError:
        return "<IP-komputer-ini>"


def serve(host="127.0.0.1", port=8787, open_browser=True, autostart=False, env_path=".env",
          state_dir=".vault_tracker"):
    ctrl = Controller(env_path, state_dir)
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    token = None if loopback else secrets.token_urlsafe(18)
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"} if loopback else None
    try:
        httpd = http.server.ThreadingHTTPServer((host, port), make_handler(ctrl, allowed, token))
    except OSError as e:
        if e.errno not in (errno.EADDRINUSE, 10048):  # 10048 = WSAEADDRINUSE on Windows
            raise
        local = f"http://127.0.0.1:{port}/"
        if _already_running(port):
            print(f"Vault Tracker web sudah jalan di {local} (dari terminal lain). Membuka browser ke sana.")
            print("Untuk mematikannya, tekan Ctrl+C di terminal yang menjalankannya.")
            if open_browser:
                webbrowser.open(local)
            sys.exit(0)
        print(f"Port {port} sudah dipakai program lain (atau Vault Tracker lain dengan --host).")
        print(f"Pakai port lain, misalnya: python3 -m vault_tracker web --port {port + 3}")
        print(f"Atau lihat program yang memakainya: lsof -i :{port}")
        sys.exit(1)
    url = f"http://127.0.0.1:{port}/" if loopback else f"http://{_lan_ip()}:{port}/?token={token}"
    print(f"Vault Tracker web jalan di {url}")
    if not loopback:
        print("Dibuka ke jaringan: siapa pun yang punya link + token di atas bisa mengubah pengaturan.")
    print("Biarkan terminal ini terbuka. Ctrl+C untuk mematikan.")
    if autostart:
        r = ctrl.start()
        if not r["ok"]:
            print("Belum bisa start:", "; ".join(r["errors"].values()))
    if open_browser and loopback:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ctrl.stop(wait=True)
        httpd.server_close()
