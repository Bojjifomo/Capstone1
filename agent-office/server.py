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
import runner  # noqa: E402
import store  # noqa: E402
import team  # noqa: E402

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
    "default_mode": "acceptEdits",
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
        cfg["_dev"] = dev
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
        "team": APP.office.team.roster(),
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


# ---- stage 2: talk to sessions ---------------------------------------------

RID = r"(?P<rid>[0-9a-f]{12})"
QID = r"(?P<qid>[0-9a-f]{8})"
PURPOSE_RE = re.compile(r"^(bypass:(new|[0-9a-f-]{36})|clean:[a-z0-9_]{1,32})$")


def text_field(body, key, limit=200):
    value = body.get(key, "")
    return value if isinstance(value, str) else str(value)[:limit]


@route("GET", "/api/options")
def api_options(req, m):
    return {"modes": list(runner.MODES), "risky": sorted(runner.RISKY_MODES), "models": list(runner.MODELS),
            "default_mode": APP.cfg.get("default_mode", "acceptEdits"), "claude": bool(runner.find_claude(APP.cfg))}


@route("POST", "/api/session/" + SID + "/send")
def api_send(req, m):
    b = req.body
    return APP.office.send(m.group("sid"), text_field(b, "text", runner.MAX_TEXT + 1), text_field(b, "mode"),
                           model=text_field(b, "model"), code_id=text_field(b, "code_id"),
                           code=text_field(b, "code"), now_please=bool(b.get("now")))


@route("POST", "/api/session/" + SID + "/queue/" + QID + "/now")
def api_queue_now(req, m):
    return APP.office.send_now(m.group("sid"), m.group("qid"))


@route("POST", "/api/session/" + SID + "/queue/" + QID + "/remove")
def api_queue_remove(req, m):
    return {"removed": bool(APP.office.runner.take(m.group("sid"), m.group("qid")))}


@route("GET", "/api/run/" + RID)
def api_run(req, m):
    try:
        after = int((req.query.get("v") or ["-1"])[0])
    except ValueError:
        after = -1
    run = APP.office.runner.wait(m.group("rid"), after, timeout=20)
    if run is None:
        raise HttpError(404, "no such run")
    return run


@route("POST", "/api/run/" + RID + "/stop")
def api_run_stop(req, m):
    return {"stopped": APP.office.runner.stop(m.group("rid"))}


@route("GET", "/api/projects")
def api_projects(req, m):
    return {"projects": [{"id": p["id"], "label": p["label"]} for p in APP.office.projects()]}


@route("POST", "/api/job")
def api_job(req, m):
    b = req.body
    system = PM_PROMPT if b.get("pm") else ""
    return APP.office.new_job(text_field(b, "project"), text_field(b, "text", runner.MAX_TEXT + 1),
                              text_field(b, "mode"), model=text_field(b, "model"),
                              code_id=text_field(b, "code_id"), code=text_field(b, "code"), system=system)


OFFICE_CLI = HERE / "tools" / "office"
PM_PROMPT = """You are the party leader for this quest (project manager mode) in bojjifomo's guild.
Work like this:
1. Split the work into clear, separate pieces before doing any of it.
2. See who is in the guild: `{cli} team`. Find the best fit for a piece: `{cli} who "<piece>"`.
3. Hand a piece to a teammate with a self-contained brief (they cannot see this conversation: include paths,
   context and what "done" means): `{cli} ask <name> "<brief>" --no-wait`, then collect the answer later with
   `{cli} result <session-id>`. Independent pieces can run at the same time.
4. For pieces no teammate fits, use subagents (the Task/Agent tool) and pick the model by need:
   haiku for lookups and searches, sonnet for normal coding, opus for hard problems (design, tricky bugs).
5. Check what comes back yourself before you build on it.
6. Finish with a short report: a table of who did what (teammate or subagent, model, piece, result),
   then what is left open.""".format(cli=OFFICE_CLI)


@route("POST", "/api/code")
def api_code(req, m):
    purpose = text_field(req.body, "purpose")
    if not PURPOSE_RE.match(purpose):
        raise HttpError(400, "unknown purpose")
    label = "run with permissions bypassed" if purpose.startswith("bypass") else "run the " + purpose[6:] + " cleaner"
    try:
        return {"code_id": APP.office.codes.request(purpose, label), "expires_in": 120}
    except RuntimeError as e:
        raise HttpError(409, str(e))


@route("GET", "/api/push/key")
def api_push_key(req, m):
    return {"key": APP.office.pusher.public, "subscribed": APP.office.pusher.subscribed}


@route("POST", "/api/push/subscribe")
def api_push_subscribe(req, m):
    try:
        APP.office.pusher.subscribe(req.body.get("subscription"))
    except (ValueError, TypeError) as e:
        raise HttpError(400, str(e))
    return {"ok": True}


@route("POST", "/api/push/unsubscribe")
def api_push_unsubscribe(req, m):
    APP.office.pusher.unsubscribe()
    return {"ok": True}


@route("POST", "/api/push/test")
def api_push_test(req, m):
    ok = APP.office.notify("A raven from the guild hall", "Notifications work. You'll hear from me when someone needs you.")
    return {"sent": ok}


@route("GET", "/api/push/last")
def api_push_last(req, m):
    return APP.office.pusher.last or {}


# ---- stage 3: the team -------------------------------------------------------

STAFF = r"(?P<staff>[0-9a-f]{8})"


@route("GET", "/api/team")
def api_team(req, m):
    roster = APP.office.team.roster()
    known = sorted({t[3] for t in team.TITLES} | {team.DEFAULT_TITLE[2]} | set(roster["divisions"]))
    return dict(roster, known_divisions=known)


@route("GET", "/api/team/who")
def api_team_who(req, m):
    task = (req.query.get("task") or [""])[0][:2000]
    return {"ranking": APP.office.team.rank(task)}


@route("POST", "/api/staff")
def api_hire(req, m):
    b = req.body
    try:
        member, renamed = APP.office.team.hire(text_field(b, "name"), text_field(b, "role"),
                                               text_field(b, "instructions", 8001), text_field(b, "division"),
                                               text_field(b, "model") or "sonnet")
    except ValueError as e:
        raise HttpError(400, str(e))
    return {"staff": {k: member[k] for k in ("id", "name", "title", "division", "bio")}, "renamed_from": renamed}


@route("POST", "/api/staff/" + STAFF + "/dismiss")
def api_dismiss(req, m):
    return {"dismissed": APP.office.team.dismiss(m.group("staff"))}


@route("POST", "/api/team/ask")
def api_team_ask(req, m):
    try:
        return APP.office.team.ask(text_field(req.body, "name"), text_field(req.body, "brief", runner.MAX_TEXT + 1))
    except LookupError as e:
        raise HttpError(404, str(e))
    except ValueError as e:
        raise HttpError(400, str(e))


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
            except core.Refused as e:
                return self.send(409, e.info)
            except RuntimeError as e:
                return self.send(409, {"error": str(e)})
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
    APP.office.start_watching()
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
