"""Petitions: update proposals from the guild that wait for bojjifomo's yes or no.

Near-duplicate titles from the same app are refused while one is still open.
Pending petitions older than petition_expire_days (default 14, 0 disables) expire.
The last-200 cap still applies, and drops settled petitions before live ones.
"""
import difflib
import re
import secrets
import threading
import time

import prwatch
import store

STATUSES = ("pending", "approved", "rejected", "building", "merged", "closed", "failed", "expired")
OPEN = ("pending", "approved", "building")
TERMINAL = ("rejected", "expired", "merged", "closed", "failed")
IMPACT = ("", "high", "medium", "low")
EFFORT = ("", "S", "M", "L")
CAP = 200
PUBLIC_KEYS = ("id", "by", "title", "app", "summary", "impact", "effort", "status", "note", "sid",
               "pr_url", "created", "decided", "closed", "quest_ok")

BUILD_BRIEF = """bojjifomo approved this petition from {by}. Build it.

# {title}
App: {app}

{plan}

How to finish:
- Work on a new git branch, never on main. Keep the change to what the plan says.
- Run the app's tests before you push. Add tests for anything new.
- Push the branch and open a pull request that links back to this petition ("Petition {id}").
- Do not merge it: bojjifomo reviews and merges.
- End with a short report: what changed, how you tested it, and the PR link.{note}"""


def _norm_title(title):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(title).casefold()).split())


def _stem(word):
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def near_duplicate(a, b):
    """True when two titles are the same petition in different words."""
    left, right = _norm_title(a), _norm_title(b)
    if not left or not right:
        return False
    if left == right:
        return True
    ta, tb = set(left.split()), set(right.split())
    if ta and tb and len(ta & tb) / len(ta | tb) >= 0.8:
        return True
    sa, sb = {_stem(w) for w in ta}, {_stem(w) for w in tb}
    if sa and sb and len(sa & sb) / len(sa | sb) >= 0.75 and min(len(sa), len(sb)) >= 2:
        return True
    return difflib.SequenceMatcher(None, left, right).ratio() >= 0.86


def _app_key(app):
    return " ".join(str(app or "").split()).casefold()


def public(item):
    if not item:
        return {}
    out = {}
    for key in PUBLIC_KEYS:
        if key == "quest_ok":
            out[key] = bool(item.get(key))
        elif key in ("created", "decided", "closed"):
            out[key] = item.get(key) or 0
        else:
            out[key] = item.get(key, "") or ""
    return out


class Petitions:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.items = store.load("petitions.json", [])
        if not isinstance(self.items, list):
            self.items = []
        self.lock = threading.Lock()

    def save(self):
        store.save("petitions.json", self.items)

    def expire_days(self):
        try:
            return float(self.cfg.get("petition_expire_days", 14))
        except (TypeError, ValueError):
            return 14

    def get(self, pid):
        return next((p for p in self.items if p["id"] == pid), None)

    def _duplicate_of(self, title, app):
        key = _app_key(app)
        for existing in self.items:
            if existing.get("status") not in OPEN:
                continue
            if _app_key(existing.get("app")) != key:
                continue
            if near_duplicate(existing.get("title"), title):
                return existing
        return None

    def add(self, by, title, app, summary, plan, impact="", effort=""):
        title = " ".join(str(title).split())[:120]
        if not title:
            raise ValueError("a petition needs a title")
        plan = str(plan).strip()[:20000]
        if not plan:
            raise ValueError("a petition needs a plan")
        app = " ".join(str(app).split())[:60]
        item = {
            "id": secrets.token_hex(4), "by": " ".join(str(by).split())[:40] or "someone",
            "title": title, "app": app, "summary": " ".join(str(summary).split())[:400],
            "plan": plan, "impact": impact if impact in IMPACT else "", "effort": effort if effort in EFFORT else "",
            "status": "pending", "created": time.time(), "decided": None, "note": "", "sid": "",
            "pr_url": "", "closed": None, "quest_ok": False,
        }
        with self.lock:
            twin = self._duplicate_of(title, app)
            if twin:
                raise ValueError("a similar petition is already open for this app: " + twin["title"])
            self.items.append(item)
            self._expire_locked()
            self._trim_locked()
            self.save()
        return item

    def decide(self, pid, decision, note=""):
        with self.lock:
            item = self.get(pid)
            if not item:
                raise LookupError("no such petition")
            if item["status"] not in ("pending", "approved"):
                raise ValueError("this petition was already %s" % item["status"])
            if decision not in ("approve", "reject"):
                raise ValueError("decide approve or reject")
            item["status"] = "approved" if decision == "approve" else "rejected"
            item["note"] = str(note).strip()[:1000]
            item["decided"] = time.time()
            self.save()
            return dict(item)

    def mark_building(self, pid, sid):
        with self.lock:
            item = self.get(pid)
            if item is None:
                raise LookupError("no such petition")
            item["status"] = "building"
            item["sid"] = sid
            item["quest_ok"] = False
            self.save()

    def expire(self, now=None):
        """Mark stale pending petitions expired. Returns the ones that changed."""
        with self.lock:
            changed = self._expire_locked(now)
            if changed:
                self._trim_locked()
                self.save()
            return [dict(p) for p in changed]

    def _expire_locked(self, now=None):
        days = self.expire_days()
        if days <= 0:
            return []
        now = time.time() if now is None else now
        cutoff = now - days * 86400
        changed = []
        for item in self.items:
            if item.get("status") == "pending" and (item.get("created") or 0) < cutoff:
                item["status"] = "expired"
                item["decided"] = now
                item["note"] = item.get("note") or ("expired after %g days with no decision" % days)
                changed.append(item)
        return changed

    def _trim_locked(self):
        if len(self.items) <= CAP:
            return

        def drop(pool):
            for item in sorted(pool, key=lambda p: p.get("created") or 0):
                if len(self.items) <= CAP:
                    return
                self.items.remove(item)

        drop([p for p in self.items if p.get("status") in TERMINAL])
        if len(self.items) > CAP:
            drop(list(self.items))

    def finish_build(self, sid, ok, text):
        """The build quest ended. A PR link keeps it open for polling; a hard failure closes it."""
        url = prwatch.find_url(text)
        with self.lock:
            item = next((p for p in self.items if p.get("sid") == sid and p.get("status") == "building"), None)
            if item is None:
                return None
            if url:
                item["pr_url"] = url
            item["quest_ok"] = bool(ok)
            if not ok and not url:
                item["status"] = "failed"
                item["closed"] = time.time()
            self.save()
            return dict(item)

    def poll_prs(self, fetch=None):
        """Refresh building petitions that already have a PR URL. Returns those that closed."""
        fetch = fetch or prwatch.fetch
        with self.lock:
            targets = [(p["id"], p.get("pr_url") or "") for p in self.items
                       if p.get("status") == "building" and p.get("pr_url")]
        changed = []
        for pid, url in targets:
            try:
                status = fetch(url)
            except Exception:
                status = None
            if status not in ("merged", "closed"):
                continue
            with self.lock:
                item = self.get(pid)
                if not item or item.get("status") != "building":
                    continue
                item["status"] = status
                item["closed"] = time.time()
                self.save()
                changed.append(dict(item))
        return changed

    def brief(self, item):
        fields = dict(item, note="\n\nbojjifomo's note: " + item["note"] if item.get("note") else "")
        return BUILD_BRIEF.format(**{k: fields.get(k, "") for k in ("by", "title", "app", "plan", "id", "note")})

    def listing(self):
        order = {"pending": 0, "approved": 1, "building": 2, "merged": 3, "closed": 4, "failed": 5, "rejected": 6, "expired": 7}
        return sorted(self.items, key=lambda p: (order.get(p.get("status"), 9), -(p.get("created") or 0)))

    def pending_count(self):
        return sum(1 for p in self.items if p.get("status") == "pending")
