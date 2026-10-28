#!/usr/bin/env python3
"""Agent Office: your Claude Code sessions as adventurers in a guild hall.

Standard library only. Binds to 127.0.0.1 and the Tailscale IP, never 0.0.0.0.
"""
import argparse
import hashlib
import hmac
import json
import mimetypes
import os
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
import haiku  # noqa: E402
import panels  # noqa: E402
import petitions  # noqa: E402
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
    "external_hosts": [],       # Funnel names that accept a bearer token and nothing else
    "external_token": "",       # prefer AGENT_OFFICE_TOKEN in the environment; never commit a real token
    "external_origin": "grokbot",
    "agents_dir": "~/Agents",
    "project_roots": ["~/Projects"],
    "claude_bin": "",           # empty = look in the usual places
    "push_subject": "mailto:bojjifomo@users.noreply.github.com",
    "default_mode": "acceptEdits",
    "webhooks": [],                 # [{url, secret?, header?, events?}] see webhooks.py
    "webhook_timeout": 5,
    "petition_expire_days": 14,     # pending petitions older than this expire; 0 disables
    "pr_poll_seconds": 300,         # how often to ask GitHub about open petition PRs; 0 disables
    "orchestrator_name": "Quill",
    "orchestrator_root": "",        # repo that contains orchestrator/; empty = sibling of agent-office
    "orchestrator_timeout": 600,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    saved = store.load("config.json", None)
    if saved is None:
        store.save("config.json", DEFAULT_CONFIG)
    else:
        cfg.update(saved)
    return cfg


def configured_token(cfg):
    """Bearer token for external callers. The environment wins; blank means that door is shut."""
    env = os.environ.get("AGENT_OFFICE_TOKEN", "")
    if isinstance(env, str) and env.strip():
        return env.strip()
    saved = cfg.get("external_token", "")
    if isinstance(saved, str) and saved.strip():
        return saved.strip()
    return ""


def tokens_match(presented, expected):
    """Constant-time compare. Both sides are hashed so a different length cannot skip compare_digest."""
    if not presented or not expected:
        return False
    if len(presented) > 512 or len(expected) > 512:
        return False
    got = hashlib.sha256(presented.encode("utf-8")).digest()
    want = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(got, want)


def remember_host(bucket, raw, port):
    h = str(raw).strip().lower().rstrip(".")
    if not h:
        return
    bucket.add(h)
    bare, sep, maybe = h.rpartition(":")
    if sep and maybe.isdigit() and bare:
        bucket.add(bare)
    elif ":" not in h:
        bucket.add("%s:%d" % (h, port))


def external_origin_name(cfg):
    name = str(cfg.get("external_origin") or "grokbot").strip()
    if re.match(r"^[A-Za-z0-9_.:-]{1,40}$", name):
        return name
    return "grokbot"


class TokenFailures:
    """Wrong bearer attempts, per client address. A correct token clears the count."""

    WINDOW = 600
    MAX_FAILS = 8

    def __init__(self):
        self.lock = threading.Lock()
        self.events = {}

    def fail(self, key):
        now = time.time()
        with self.lock:
            recent = [t for t in self.events.get(key, []) if now - t < self.WINDOW]
            recent.append(now)
            self.events[key] = recent[-32:]
            count = len(self.events[key])
            if len(self.events) > 1024:  # a forged X-Forwarded-For must not grow without bound
                stale = [k for k, v in self.events.items() if k != key and (not v or now - v[-1] >= self.WINDOW)]
                for k in stale:
                    self.events.pop(k, None)
                overflow = len(self.events) - 1024
                if overflow > 0:
                    for k in [k for k in self.events if k != key][:overflow]:
                        self.events.pop(k, None)
            return count

    def ok(self, key):
        with self.lock:
            self.events.pop(key, None)


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
        self.external_hosts = set()
        self.token = configured_token(cfg)
        self.origin_name = external_origin_name(cfg)
        self.failures = TokenFailures()
        self.started = time.time()

    def allow_hosts(self, binds, port):
        for b in binds:
            self.hosts.add("%s:%d" % (b, port))
        self.hosts.add("localhost:%d" % port)
        for h in self.cfg.get("extra_hosts", []) or []:
            h = str(h).lower()
            self.hosts.add(h)
            self.hosts.add("%s:%d" % (h, port))
        for h in self.cfg.get("external_hosts", []) or []:
            remember_host(self.external_hosts, h, port)


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
        "cost_today": panels.cost_today(APP.office),
        "disk": APP.office.vault.free(),
        "petitions": APP.office.petitions.pending_count(),
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
    sid = m.group("sid")
    result = APP.office.send(sid, text_field(b, "text", runner.MAX_TEXT + 1), text_field(b, "mode"),
                             model=text_field(b, "model"), code_id=text_field(b, "code_id"),
                             code=text_field(b, "code"), now_please=bool(b.get("now")))
    APP.office.ledger.add("queue" if "queued" in result else "send", text_field(b, "text"), sid=sid, mode=text_field(b, "mode"))
    return result


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
    result = APP.office.new_job(text_field(b, "project"), text_field(b, "text", runner.MAX_TEXT + 1),
                                text_field(b, "mode"), model=text_field(b, "model"),
                                code_id=text_field(b, "code_id"), code=text_field(b, "code"), system=system)
    APP.office.ledger.add("job", text_field(b, "text"), sid=result["sid"], mode=text_field(b, "mode"), pm=bool(system))
    return result


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
        cid = APP.office.codes.request(purpose, label)
        APP.office.ledger.add("seal", "sent a seal to " + label)
        return {"code_id": cid, "expires_in": 120}
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
    return {"sent": ok, "error": "" if ok else (APP.office.pusher.last_error or "no phone is subscribed")}


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
    APP.office.ledger.add("hire", "%s the %s" % (member["name"], member["title"]))
    return {"staff": {k: member[k] for k in ("id", "name", "title", "division", "bio")}, "renamed_from": renamed}


@route("POST", "/api/staff/" + STAFF + "/dismiss")
def api_dismiss(req, m):
    member = APP.office.team.find(m.group("staff"))
    done = APP.office.team.dismiss(m.group("staff"))
    if done:
        APP.office.ledger.add("dismiss", member["name"])
    return {"dismissed": done}


@route("POST", "/api/team/ask")
def api_team_ask(req, m):
    try:
        member = APP.office.team.find(text_field(req.body, "name"))
        if req.external and member and member.get("builtin"):
            raise HttpError(403, "token callers do not start briefs")
        result = APP.office.team.ask(text_field(req.body, "name"), text_field(req.body, "brief", runner.MAX_TEXT + 1))
        if result.get("briefing"):
            return result
        APP.office.ledger.add("errand", "%s: %s" % (result["name"], text_field(req.body, "brief")), sid=result["sid"])
        return result
    except LookupError as e:
        raise HttpError(404, str(e))
    except ValueError as e:
        raise HttpError(400, str(e))


# ---- stage 4: panels -----------------------------------------------------------

@route("GET", "/api/usage")
def api_usage(req, m):
    return panels.usage(APP.office)


@route("GET", "/api/bots")
def api_bots(req, m):
    return {"jobs": APP.office.familiars.jobs(), "mac": sys.platform == "darwin"}


@route("POST", r"/api/bots/(?P<job>[0-9a-f]{10})/run")
def api_bot_run(req, m):
    try:
        label = APP.office.familiars.run_now(m.group("job"))
    except LookupError as e:
        raise HttpError(404, str(e))
    APP.office.ledger.add("bot", "started " + label)
    return {"started": label}


@route("GET", "/api/disk")
def api_disk(req, m):
    return APP.office.vault.report(rescan=bool(req.query.get("rescan")))


@route("POST", r"/api/disk/clean/(?P<cleaner>[a-z0-9_]{1,32})")
def api_disk_clean(req, m):
    cid = m.group("cleaner")
    if cid not in panels.CLEANERS:
        raise HttpError(404, "no such cleaner")
    if not APP.office.codes.verify(text_field(req.body, "code_id"), text_field(req.body, "code"), "clean:" + cid):
        APP.office.ledger.add("seal", "wrong or expired seal for cleaner " + cid)
        raise core.Refused("this needs a fresh guild seal from your phone", needs_code="clean:" + cid)
    result = APP.office.vault.clean(cid)
    APP.office.ledger.add("clean", "%s freed %.1f GB" % (result["title"], result["freed"] / 1e9))
    return result


@route("GET", "/api/search")
def api_search(req, m):
    return panels.search(APP.office, (req.query.get("q") or [""])[0])


@route("GET", "/api/ledger")
def api_ledger(req, m):
    return {"entries": APP.office.ledger.recent()}


@route("GET", "/api/orchestrator")
def api_orchestrator(req, m):
    """Quill's latest read-only brief. Starting one is local-only."""
    return APP.office.briefing.public()


@route("POST", "/api/orchestrator/brief")
def api_orchestrator_brief(req, m):
    if req.external:
        raise HttpError(403, "token callers do not start briefs")
    try:
        result = APP.office.briefing.start()
    except RuntimeError as e:
        raise HttpError(409, str(e))
    return result


@route("POST", "/api/ledger")
def api_ledger_post(req, m):
    """A line in the town log, for example from the external assistant."""
    kind = " ".join(text_field(req.body, "kind", 20).split()).lower()
    if kind not in ("guild", "chatter"):
        raise HttpError(400, "kind must be guild or chatter")
    text = " ".join(text_field(req.body, "text", 400).split())
    if not text:
        raise HttpError(400, "text is required")
    by = " ".join(text_field(req.body, "by", 40).split())[:40] or "Guild Hall"
    APP.office.ledger.add(kind, text[:300], by=by)
    return {"ok": True}


# ---- profiles: division, Haiku summaries and quick replies ----------------------

@route("POST", "/api/session/" + SID + "/division")
def api_assign(req, m):
    APP.office.assign(m.group("sid"), text_field(req.body, "division"))
    return {"ok": True}


def last_reply(sid):
    t = APP.office.transcript(sid)
    if not t:
        raise HttpError(404, "no such session")
    if not t.last_text:
        raise HttpError(400, "they haven't said anything yet")
    return t.last_text


@route("POST", "/api/session/" + SID + "/summarize")
def api_summarize(req, m):
    try:
        return {"summary": haiku.summarize(APP.cfg, last_reply(m.group("sid")))}
    except RuntimeError as e:
        raise HttpError(502, str(e))


@route("POST", "/api/session/" + SID + "/suggest")
def api_suggest(req, m):
    try:
        return {"replies": haiku.replies(APP.cfg, last_reply(m.group("sid")))}
    except RuntimeError as e:
        raise HttpError(502, str(e))


@route("POST", "/api/client-error")
def api_client_error(req, m):
    """The page reports its own JavaScript errors here, so phone-only bugs end up in server.log."""
    b = req.body
    sys.stderr.write("client error [%s] %s | %s | %s\n" % (text_field(b, "where")[:40], text_field(b, "message", 400)[:300].replace("\n", " / "),
                                                       text_field(b, "ua", 400)[:160], text_field(b, "stack", 2100)[:1500].replace("\n", " / ")))
    return {"ok": True}


# ---- petitions: updates that wait for your yes ---------------------------------

PETITION = r"(?P<petition>[0-9a-f]{8})"


@route("GET", "/api/petitions")
def api_petitions(req, m):
    return {"petitions": APP.office.petitions.listing()}


@route("POST", "/api/petitions")
def api_petition_add(req, m):
    b = req.body
    try:
        item = APP.office.petitions.add(text_field(b, "by"), text_field(b, "title"), text_field(b, "app"),
                                        text_field(b, "summary", 1000), text_field(b, "plan", 20001),
                                        text_field(b, "impact"), text_field(b, "effort"))
    except ValueError as e:
        raise HttpError(400, str(e))
    APP.office.ledger.add("petition", "%s: %s" % (item["by"], item["title"]))
    APP.office.emit("petition.added", petitions.public(item))
    APP.office.notify("%s petitions the guild" % item["by"], item["title"] + (" (" + item["app"] + ")" if item["app"] else ""),
                      tag="petition-" + item["id"], url="/#p=" + item["id"])
    return {"petition": item}


def build_petition(item, b):
    """Start a quest that carries out an approved petition."""
    result = APP.office.new_job(text_field(b, "project"), APP.office.petitions.brief(item), text_field(b, "mode"),
                                model=text_field(b, "model"), code_id=text_field(b, "code_id"),
                                code=text_field(b, "code"))
    try:
        APP.office.petitions.mark_building(item["id"], result["sid"])
    except LookupError as e:
        raise HttpError(404, str(e))
    APP.office.rename(result["sid"], "Petition: " + item["title"][:50])
    APP.office.ledger.add("build", item["title"], sid=result["sid"])
    APP.office.emit("build.started", {"id": item["id"], "title": item["title"], "sid": result["sid"], "by": item.get("by") or ""})
    return result


@route("POST", "/api/petitions/" + PETITION + "/decide")
def api_petition_decide(req, m):
    b = req.body
    if req.external and b.get("build"):
        raise HttpError(403, "token callers approve or reject; they do not start the build")
    decision = text_field(b, "decision")
    try:
        item = APP.office.petitions.decide(m.group("petition"), decision, text_field(b, "note", 1001))
    except LookupError as e:
        raise HttpError(404, str(e))
    except ValueError as e:
        raise HttpError(400, str(e))
    APP.office.ledger.add("approve" if decision == "approve" else "reject", item["title"])
    APP.office.emit("petition.decided", dict(petitions.public(item), decision=decision))
    if decision == "approve" and b.get("build"):
        return dict(build_petition(item, b), petition=item["id"])
    return {"petition": item["id"], "status": item["status"]}


@route("POST", "/api/petitions/" + PETITION + "/build")
def api_petition_build(req, m):
    item = APP.office.petitions.get(m.group("petition"))
    if not item:
        raise HttpError(404, "no such petition")
    if item["status"] != "approved":
        raise HttpError(400, "approve it first")
    return dict(build_petition(item, req.body), petition=item["id"])


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
    def __init__(self, handler, query, body, external=False):
        self.handler = handler
        self.query = query
        self.body = body
        self.external = external


# Routes a bearer token may call. Session send, seals, cleaners, launchd, hiring and the
# static UI stay closed. Risky quest modes still go through the phone seal inside new_job.
TOKEN_ROUTES = (
    ("GET", re.compile(r"^/api/state$")),
    ("GET", re.compile(r"^/api/petitions$")),
    ("GET", re.compile(r"^/api/team$")),
    ("GET", re.compile(r"^/api/team/who$")),
    ("GET", re.compile(r"^/api/ledger$")),
    ("GET", re.compile(r"^/api/usage$")),
    ("GET", re.compile(r"^/api/bots$")),
    ("GET", re.compile(r"^/api/orchestrator$")),
    ("POST", re.compile(r"^/api/petitions$")),
    ("POST", re.compile(r"^/api/petitions/[0-9a-f]{8}/decide$")),
    ("POST", re.compile(r"^/api/team/ask$")),
    ("POST", re.compile(r"^/api/job$")),
    ("POST", re.compile(r"^/api/ledger$")),
)


def token_route_allowed(method, path):
    return any(m == method and rx.match(path) for m, rx in TOKEN_ROUTES)


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentOffice"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if APP.dev:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        self._last_status = status
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

    def request_host(self):
        return self.headers.get("Host", "").strip().lower().rstrip(".")

    def origin_ok(self):
        host = self.request_host()
        origin = self.headers.get("Origin", "").strip().lower()
        return origin in ("http://" + host, "https://" + host)

    def from_loopback(self):
        peer = self.client_address[0] if self.client_address else ""
        return peer in ("127.0.0.1", "::1")

    def funnel_request(self):
        """Public Funnel ingress. Tailscale sets this on its loopback proxy and strips a forged copy."""
        if not self.from_loopback():
            return False
        return self.headers.get("Tailscale-Funnel-Request", "").strip() == "?1"

    def client_key(self):
        """Who to count failed tokens against. Funnel's proxy is loopback and puts the client in X-Forwarded-For."""
        peer = self.client_address[0] if self.client_address else ""
        if peer in ("127.0.0.1", "::1"):
            parts = [p.strip() for p in self.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
            if parts and len(parts[-1]) <= 64 and re.match(r"^[0-9A-Fa-f:.]+$", parts[-1]):
                return parts[-1]
        return peer or "unknown"

    def presented_token(self):
        raw = self.headers.get("Authorization")
        if raw is None:
            return None
        parts = raw.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return ""
        return parts[1].strip()

    def reject_token(self, key):
        n = APP.failures.fail(key)
        path = urlsplit(self.path).path
        sys.stderr.write("agent-office: rejected bearer token from %s for %s %s\n" % (key, self.command, path))
        sys.stderr.flush()
        if n >= TokenFailures.MAX_FAILS:
            self.send(429, {"error": "too many attempts"})
        else:
            self.send(401, {"error": "unauthorized"})

    def token_gate(self, required):
        """'token' (route allowlist), 'open' (today's rules), or None when the response is already sent.

        A bearer token is required for external_hosts and for real Funnel requests. On an ordinary
        host a correct token changes nothing; a wrong one is rejected so a mismatch fails closed.
        """
        presented = self.presented_token()
        key = self.client_key()
        if not APP.token:
            if required:
                self.reject_token(key)
                return None
            return "open"
        if presented is not None and tokens_match(presented, APP.token):
            APP.failures.ok(key)
            return "token" if required else "open"
        if required or presented is not None:
            self.reject_token(key)
            return None
        return "open"

    def audit_external(self, method, path):
        status = getattr(self, "_last_status", 0)
        try:
            APP.office.ledger.add("external", "%s %s -> %s" % (method, path[:180], status), status=status)
        except Exception:
            traceback.print_exc()

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
        host = self.request_host()
        if host not in APP.hosts and host not in APP.external_hosts:
            return self.deny("host")
        # external_hosts always need a token. A Funnel request to a normal host (the phone's
        # ts.net name is already in extra_hosts) needs one too. Tailnet Serve has no such header.
        required = host in APP.external_hosts or self.funnel_request()
        gate = self.token_gate(required)
        if gate is None:
            return
        token_mode = gate == "token"
        parts = urlsplit(self.path)
        body = {}
        try:
            if token_mode:
                panels.set_ledger_origin(APP.origin_name)
            if method == "POST":
                if not token_mode and (self.headers.get("X-Agent-Office") != "1" or not self.origin_ok()):
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
                if token_mode and not token_route_allowed(method, parts.path):
                    return self.send(403, {"error": "forbidden", "why": "route"})
                try:
                    result = fn(Request(self, parse_qs(parts.query), body, external=token_mode), m)
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
            if token_mode:
                return self.send(403, {"error": "forbidden", "why": "route"})
            if method == "GET":
                return self.static(parts.path)
            self.send(404, {"error": "not found"})
        finally:
            if token_mode:
                try:
                    self.audit_external(method, parts.path)
                finally:
                    panels.set_ledger_origin(None)

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
    # The default backlog is 5. Through `tailscale serve` a phone opens a connection per file at
    # once; on macOS the extra ones are refused and those scripts never load.
    request_queue_size = 128


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
