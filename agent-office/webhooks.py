"""Outgoing webhooks. Fired from the office, never on the request thread.

config.json:

    "webhooks": [
      {"url": "https://example.com/guild", "secret": "optional", "header": "X-Guild-Token"}
    ]

`url` is http or https. `secret` is optional. When it is set, every delivery sends

    X-Guild-Signature: sha256=<hmac-sha256 of the raw body, hex>

and, if `header` is set (a token name like `X-Guild-Token`), that header carries the
secret itself. `events` is an optional list; omit it to receive every event.

Payload (`Content-Type: application/json`):

    {
      "event": "petition.added",
      "ts": 1710000000.0,
      "source": "agent-office",
      "data": { }
    }

Events:

    petition.added     a petition was filed (data is the petition, without the plan)
    petition.decided   approve, reject, or expire
    petition.closed    status became merged, closed, or failed (includes pr_url)
    build.started      an approved petition's build quest started (sid, petition id)
    quest.finished     a claude run ended (sid, ok, cost, origin, and petition fields)
    familiar.error     a launchd job grew a new error line
    incident.opened    an intern (one of your programs) failed its health checks
    incident.escalated someone up the chain took an incident (data.who, data.level; "you" means your phone)
    incident.resolved  the intern is healthy again
    ops.down           the operations manager failed or got stuck; the CEO takes over

Delivery uses a daemon thread and a short timeout. Failures are one stderr line
(the URL without its query string) and never raise into the caller.
"""
import hashlib
import hmac
import json
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from urllib.parse import urlsplit

EVENTS = (
    "petition.added",
    "petition.decided",
    "petition.closed",
    "build.started",
    "quest.finished",
    "familiar.error",
    "incident.opened",
    "incident.escalated",
    "incident.resolved",
    "ops.down",
)
HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
DEFAULT_TIMEOUT = 5.0


def _log_default(line):
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def normalize(cfg):
    raw = (cfg or {}).get("webhooks") or []
    if isinstance(raw, str):
        raw = [{"url": raw}]
    hooks = []
    for item in raw:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            continue
        events = item.get("events")
        if isinstance(events, str):
            events = [events]
        if not isinstance(events, list):
            events = None
        else:
            events = [str(e) for e in events if str(e) in EVENTS]
        header = str(item.get("header") or "").strip()
        if header and not HEADER_RE.match(header):
            header = ""
        hooks.append({
            "url": url,
            "secret": str(item.get("secret") or ""),
            "header": header,
            "events": events,
        })
    return hooks


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def safe_url(url):
    parts = urlsplit(url)
    return "%s://%s%s" % (parts.scheme, parts.netloc, parts.path)


class Webhooks:
    def __init__(self, cfg, log=None, timeout=None):
        self.hooks = normalize(cfg)
        self.log = log or _log_default
        try:
            self.timeout = float(timeout if timeout is not None else (cfg or {}).get("webhook_timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            self.timeout = DEFAULT_TIMEOUT
        self.timeout = min(15.0, max(1.0, self.timeout))
        self._inflight = []
        self._lock = threading.Lock()

    def fire(self, event, data):
        if event not in EVENTS:
            return
        body = json.dumps({
            "event": event,
            "ts": time.time(),
            "source": "agent-office",
            "data": data if isinstance(data, dict) else {"value": data},
        }, sort_keys=True).encode("utf-8")
        started = []
        for hook in self.hooks:
            if hook["events"] is not None and event not in hook["events"]:
                continue
            thread = threading.Thread(target=self._send, args=(hook, event, body), daemon=True, name="webhook")
            thread.start()
            started.append(thread)
        with self._lock:
            self._inflight = [t for t in self._inflight if t.is_alive()] + started

    def wait(self, timeout=3):
        with self._lock:
            threads = list(self._inflight)
        for thread in threads:
            thread.join(timeout)

    def _send(self, hook, event, body):
        headers = {"Content-Type": "application/json", "User-Agent": "agent-office"}
        secret = hook["secret"]
        if secret:
            headers["X-Guild-Signature"] = sign(secret, body)
            if hook["header"]:
                headers[hook["header"]] = secret
        req = urllib.request.Request(hook["url"], data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                res.read(256)
        except Exception as exc:
            detail = exc
            if isinstance(exc, urllib.error.HTTPError):
                detail = "HTTP %s" % exc.code
            self.log("agent-office: webhook failed event=%s url=%s: %s" % (event, safe_url(hook["url"]), detail))
            return
        except BaseException:
            traceback.print_exc()
