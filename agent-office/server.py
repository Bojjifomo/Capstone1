#!/usr/bin/env python3
"""Agent Office: your Claude Code sessions as adventurers in a guild hall.

Standard library only. Binds to 127.0.0.1 and the Tailscale IP, never 0.0.0.0.
"""
import argparse
import hashlib
import json
import mimetypes
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402
import store  # noqa: E402

STATIC = HERE / "static"
MAX_BODY = 64 * 1024

DEFAULT_CONFIG = {
    "name": "bojjifomo",
    "port": 7777,
    "bind": ["127.0.0.1", "100.89.176.76"],
    "extra_hosts": [],          # e.g. your-mac.tailnet-name.ts.net when using `tailscale serve`
    "agents_dir": "~/Agents",
    "project_roots": ["~/Projects"],
    "claude_bin": "",           # empty = look in the usual places
    "push_subject": "mailto:agent-office@localhost",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    saved = store.load("config.json", None)
    if saved is None:
        store.save("config.json", DEFAULT_CONFIG)
    else:
        cfg.update(saved)
    return cfg


def compute_version():
    h = hashlib.sha1()
    files = sorted(HERE.glob("*.py")) + sorted(p for p in STATIC.rglob("*") if p.is_file())
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


SID = r"(?P<sid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"


class App:
    """Everything the handlers need; one per server process."""

    def __init__(self, cfg, dev=False):
        self.cfg = cfg
        self.dev = dev
        self.version = compute_version()
        self.office = core.Office(cfg)
        self.hosts = set()
        self.started = time.time()

    def allow_hosts(self, binds, port):
        for b in binds:
            self.hosts.add("%s:%d" % (b, port))
        self.hosts.add("localhost:%d" % port)
        for h in self.cfg.get("extra_hosts", []):
            h = str(h).lower()
            self.hosts.add(h)
            self.hosts.add("%s:%d" % (h, port))


APP = None


# ---- routes -------------------------------------------------------------

@route("GET", "/api/state")
def api_state(req, m):
    return {
        "version": APP.version,
        "name": APP.cfg.get("name", ""),
        "now": time.time(),
        "sessions": APP.office.all_sessions(),
    }


@route("GET", "/api/session/" + SID)
def api_session(req, m):
    info = APP.office.detail(m.group("sid"))
    if not info:
        raise HttpError(404, "no such session")
    return info


@route("POST", "/api/session/" + SID + "/rename")
def api_rename(req, m):
    APP.office.rename(m.group("sid"), req.body.get("name", ""))
    return {"ok": True}


@route("POST", "/api/session/" + SID + "/archive")
def api_archive(req, m):
    APP.office.set_archived(m.group("sid"), bool(req.body.get("archived", True)))
    return {"ok": True}


@route("POST", "/api/hook/notify")
def api_hook_notify(req, m):
    """Called by `tools/office hook` from Claude Code's Notification hook."""
    sid = str(req.body.get("session_id", ""))
    if not re.match(r"^[0-9a-f-]{36}$", sid):
        raise HttpError(400, "bad session id")
    kind = str(req.body.get("notification_type", ""))
    message = str(req.body.get("message", ""))
    if kind == "permission_prompt" or "permission" in message.lower() or "waiting for your input" in message.lower():
        APP.office.flag(sid, message)
    return {"ok": True}


# ---- HTTP plumbing ------------------------------------------------------

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                "connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

STATIC_ALIASES = {"/": "index.html", "/sw.js": "sw.js", "/manifest.webmanifest": "manifest.webmanifest",
                  "/favicon.ico": "icons/icon-192.png", "/apple-touch-icon.png": "icons/apple-touch-icon.png"}


class Request:
    def __init__(self, handler, query, body):
        self.handler = handler
        self.query = query
        self.body = body


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentOffice"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if APP.dev:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def deny(self, why):
        self.send(403, {"error": "forbidden", "why": why})

    def host_ok(self):
        return self.headers.get("Host", "").strip().lower() in APP.hosts

    def origin_ok(self):
        host = self.headers.get("Host", "").strip().lower()
        origin = self.headers.get("Origin", "").strip().lower()
        return origin in ("http://" + host, "https://" + host)

    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.deny("method")

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT

    def dispatch(self, method):
        if not self.host_ok():
            return self.deny("host")
        parts = urlsplit(self.path)
        body = {}
        if method == "POST":
            if self.headers.get("X-Agent-Office") != "1" or not self.origin_ok():
                return self.deny("origin")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                return self.send(413, {"error": "too large"})
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self.send(400, {"error": "bad json"})
            if not isinstance(body, dict):
                return self.send(400, {"error": "bad json"})
        for m_, pattern, fn in ROUTES:
            if m_ != method:
                continue
            m = pattern.match(parts.path)
            if not m:
                continue
            try:
                result = fn(Request(self, parse_qs(parts.query), body), m)
            except HttpError as e:
                return self.send(e.status, {"error": e.message})
            except Exception:
                traceback.print_exc()
                return self.send(500, {"error": "server error"})
            if result is None:  # the route already wrote its own response
                return
            status = 200
            if isinstance(result, tuple):
                status, result = result
            return self.send(status, result, extra={"Cache-Control": "no-store"})
        if method == "GET":
            return self.static(parts.path)
        self.send(404, {"error": "not found"})

    def static(self, path):
        name = STATIC_ALIASES.get(path)
        if name is None and path.startswith("/static/"):
            name = path[len("/static/"):]
        if not name or not re.match(r"^[A-Za-z0-9_\-/]+\.[A-Za-z0-9]+$", name) or ".." in name:
            return self.send(404, {"error": "not found"})
        file = (STATIC / name).resolve()
        if STATIC.resolve() not in file.parents or not file.is_file():
            return self.send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        if file.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        extra = {"Cache-Control": "no-cache"}
        if name == "sw.js":
            extra["Service-Worker-Allowed"] = "/"
        self.send(200, file.read_bytes(), ctype, extra)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(addr, port):
    httpd = Server((addr, port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http-" + addr).start()
    print("Agent Office listening on http://%s:%d" % (addr, port), flush=True)
    return httpd


def bind_later(addr, port):
    """The Tailscale IP may not exist yet at login; keep trying in the background."""
    while True:
        try:
            serve(addr, port)
            return
        except OSError:
            time.sleep(30)


def start(cfg, dev=False):
    global APP
    APP = App(cfg, dev=dev)
    port = int(cfg["port"])
    binds = [b for b in cfg["bind"] if b not in ("0.0.0.0", "::", "")]
    APP.allow_hosts(binds, port)
    for addr in binds:
        if addr == "127.0.0.1":
            serve(addr, port)
        else:
            threading.Thread(target=bind_later, args=(addr, port), daemon=True).start()
    return APP


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int)
    ap.add_argument("--bind", action="append", help="address to bind (repeatable); never 0.0.0.0")
    ap.add_argument("--dev", action="store_true", help="log requests, print phone codes to stderr")
    args = ap.parse_args()
    cfg = load_config()
    if args.port:
        cfg["port"] = args.port
    if args.bind:
        cfg["bind"] = args.bind
    start(cfg, dev=args.dev)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
