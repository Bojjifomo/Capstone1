"""The office itself: sessions with a status, names, archive. Shared by the HTTP routes."""
import hashlib
import os
import re
import threading
import time
import uuid
from pathlib import Path

import briefing
import guard
import panels
import personas
import petitions
import progress
import push
import runner
import sessions
import store
import team
import webhooks

ACTIVE_WINDOW = 60          # transcript written this recently = working
IDLE_WINDOW = 2 * 3600      # after this with no process = finished
QUESTION_WINDOW = 24 * 3600  # an unanswered question older than this is stale
PERMISSION_STALL = 90       # pending tool + live process + silence = probably a permission prompt
LONG_JOB = 180              # push when a session that worked this long goes quiet


class Refused(Exception):
    """A request the office won't carry out; `info` goes back to the browser."""

    def __init__(self, message, **info):
        super().__init__(message)
        self.info = dict(info, error=message)


def plain(text, limit=140):
    text = re.sub(r"```[\s\S]*?```", " ", text or "")
    text = re.sub(r"[#*_>`|]+", "", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


STARTED_AS = {"cli": "Terminal", "sdk-cli": "Agent Office", "claude-desktop": "Claude app", "remote": "Remote control",
              "sdk-ts": "SDK", "sdk-py": "SDK", "claude-vscode": "VS Code", "mcp": "MCP"}


def home_path(p):
    p = str(p)
    home = str(Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


class Office:
    def __init__(self, cfg):
        self.cfg = cfg
        self.scanner = sessions.Scanner()
        self.lock = threading.RLock()
        self.names = store.load("names.json", {})
        self.archived = store.load("archived.json", {})
        self.assigned = store.load("assignments.json", {})  # sid -> division
        self.flags = {}  # sid -> (ts, message), from the Notification hook; memory only
        self.agents_dir = str(Path(cfg.get("agents_dir", "~/Agents")).expanduser())
        self.runner = runner.Runner(cfg, on_finish=self.run_finished)
        self.pusher = push.Pusher(cfg.get("push_subject", push.DEFAULT_SUBJECT))
        self.codes = guard.Codes(self.notify, dev=cfg.get("_dev", False))
        self.hooks = webhooks.Webhooks(cfg)
        self.briefing = briefing.Briefing(cfg, on_event=self._on_brief)
        self.team = team.Team(self)
        self.ledger = panels.Ledger()
        self.petitions = petitions.Petitions(cfg)
        self.progress = progress.Progress(self)
        self.familiars = panels.Familiars(cfg)
        self.vault = panels.Vault()
        self.old = {}           # transcripts older than the scan window, loaded on demand
        self.seen = {}          # sid -> (status, working since) for notifications
        self.familiar_seen = {}
        self._pr_busy = False
        self._pr_next = time.time() + self._pr_interval()
        self.monitor_started = False

    # ---- status -------------------------------------------------------
    def is_helper(self, t):
        if t.cwd and (t.cwd == self.agents_dir or t.cwd.startswith(self.agents_dir + "/")):
            return True
        return bool(self.runner and self.runner.is_helper(t.id))

    def status(self, t, live, now):
        if t.id in self.archived:
            return "archived", ""
        if self.runner and self.runner.active(t.id):
            return "working", "answering your message"
        age = now - t.mtime
        helper = self.is_helper(t)
        if not helper:
            flag = self.flags.get(t.id)
            if flag and flag[0] >= t.mtime - 2:
                return "needs", flag[1]
            if age < QUESTION_WINDOW:
                for name, summary in t.pending.values():
                    if name == "ExitPlanMode":
                        return "needs", "has a plan for you to approve"
                    if name in sessions.ASK_TOOLS:
                        return "needs", "asks: " + summary.split(": ", 1)[-1]
                if t.pending and live and age > PERMISSION_STALL:
                    return "needs", "waiting on permission? " + next(iter(t.pending.values()))[1]
                if t.turn_ended and age > 3 and t.last_kind == "assistant" and sessions.ends_with_question(t.last_text):
                    return "needs", "asks you a question"
        if age < ACTIVE_WINDOW or (live and not t.turn_ended):
            doing = next(iter(t.pending.values()))[1] if t.pending else "thinking"
            return "working", doing
        if live or age < IDLE_WINDOW:
            return "idle", ""
        return "finished", ""

    def name_of(self, t):
        return self.names.get(t.id) or t.title

    def summary(self, t, now):
        live = self.scanner.live.get(t.id)
        status, note = self.status(t, live, now)
        cost = t.usage_total()
        info = {
            "id": t.id,
            "name": self.name_of(t),
            "title": t.title,
            "project": Path(t.cwd).name if t.cwd else t.path.parent.name,
            "folder": home_path(t.cwd) if t.cwd else "",
            "branch": t.branch,
            "model": sessions.short_model(t.model),
            "started": t.first_ts,
            "active": max(t.mtime, t.last_ts),
            "status": status,
            "note": note,
            "last": (t.last_text or "")[-240:],
            "live": bool(live),
            "desktop": bool(live and (live.get("desktop") or "desktop" in (t.entrypoint or ""))),
            "helper": self.is_helper(t),
            "cost": round(cost[4], 4),
        }
        if self.runner:
            info["queue"] = len(self.runner.queue_of(t.id))
        if self.team:
            info["staff"] = self.team.staff_for_session(t)
        info["division"] = self.assigned.get(t.id, "")
        info["pay"] = t.pay()
        info["last_tool"] = t.last_tool
        info["started_as"] = STARTED_AS.get(t.started_as, t.started_as or "Terminal")
        staff = info.get("staff")
        info["persona"] = personas.persona(staff["seed"] if staff else t.id, t.title + " " + info["project"],
                                           name=staff["name"] if staff else None, topic=info["project"] + " project")
        info["progress"] = self.progress.public(staff["id"] if staff else t.id)
        info["persona"]["title"] = progress.titled(info["persona"]["title"], info["progress"]["rank"])
        return info

    def all_sessions(self):
        self.scanner.refresh()
        now = time.time()
        with self.scanner.lock:
            items = [self.summary(t, now) for t in self.scanner.transcripts.values()]
        items.sort(key=lambda s: s["started"])
        return items

    def transcript(self, sid):
        self.scanner.refresh()
        t = self.scanner.transcripts.get(sid)
        if t or not sessions.SID_RE.match(sid):
            return t
        if sid not in self.old:
            found = next(iter(sessions.projects_dir().glob("*/%s.jsonl" % sid)), None)
            if not found:
                return None
            if len(self.old) > 20:
                self.old.pop(next(iter(self.old)))
            self.old[sid] = sessions.Transcript(found)
        self.old[sid].update()
        return self.old[sid]

    def detail(self, sid):
        t = self.transcript(sid)
        if not t:
            run = self.runner.latest(sid)
            if not run:
                return None
            # a brand-new session whose transcript isn't written yet
            return {"id": sid, "name": "A new quest", "status": "working", "note": "", "messages": [],
                    "pending": [], "queue_items": [], "run": run.public(), "folder": home_path(run.cwd),
                    "staff": self.team.card(self.team.member_for_cwd(run.cwd))}
        info = self.summary(t, time.time())
        info["messages"] = list(t.messages)
        info["pending"] = [s for _, s in t.pending.values()]
        info["tokens"] = t.usage_total()[:4]
        info["queue_items"] = self.runner.queue_of(sid)
        run = self.runner.latest(sid)
        if run and (not run.done or time.time() - (run.ended or 0) < 600):
            info["run"] = run.public()
        return info

    # ---- edits --------------------------------------------------------
    def rename(self, sid, name):
        name = " ".join(str(name).split())[:60]
        with self.lock:
            if name:
                self.names[sid] = name
            else:
                self.names.pop(sid, None)
            store.save("names.json", self.names)

    def set_archived(self, sid, archived):
        with self.lock:
            if archived:
                self.archived[sid] = time.time()
            else:
                self.archived.pop(sid, None)
            store.save("archived.json", self.archived)

    def assign(self, sid, division):
        division = " ".join(str(division).split())[:40]
        with self.lock:
            if division:
                self.assigned[sid] = division
            else:
                self.assigned.pop(sid, None)
            store.save("assignments.json", self.assigned)

    def flag(self, sid, message):
        self.flags[sid] = (time.time(), " ".join(str(message).split())[:160] or "needs your permission")

    # ---- talking to sessions (stage 2) ----------------------------------
    def notify(self, title, body, tag="office", url="/"):
        try:
            return self.pusher.send(title, body, tag=tag, url=url)
        except Exception:
            return False

    def check_mode(self, mode, purpose, code_id, code):
        if mode not in runner.MODES:
            raise Refused("unknown permission mode")
        if mode in runner.RISKY_MODES and not self.codes.verify(code_id, code, purpose):
            if code_id:
                self.ledger.add("seal", "wrong or expired seal for " + purpose)
            raise Refused("this needs a fresh guild seal from your phone", needs_code=purpose)

    def send(self, sid, text, mode, model="", code_id="", code="", now_please=False):
        text = str(text).strip()
        if not text or len(text) > runner.MAX_TEXT:
            raise Refused("write a message (up to %d characters)" % runner.MAX_TEXT)
        t = self.transcript(sid)
        if not t:
            raise Refused("no such session")
        live = self.scanner.live.get(sid)
        if live and live.get("desktop"):
            raise Refused("open in the Claude app", desktop=True)
        self.check_mode(mode, "bypass:" + sid, code_id, code)
        status, _ = self.status(t, live, time.time())
        if self.runner.active(sid) or (status == "working" and not now_please):
            return {"queued": self.runner.enqueue(sid, text, mode, model)}
        run = self.runner.start(sid, t.cwd, text, mode, model=model)
        return {"run": run.id}

    def send_now(self, sid, qid):
        if self.runner.active(sid):
            self.runner.to_front(sid, qid)
            return {"queued": True, "front": True}
        item = self.runner.take(sid, qid)
        if not item:
            raise Refused("that message is no longer waiting")
        t = self.transcript(sid)
        live = self.scanner.live.get(sid)
        if live and live.get("desktop"):
            self.runner.put_back(sid, item)
            raise Refused("open in the Claude app", desktop=True)
        run = self.runner.start(sid, t.cwd, item["text"], item["mode"], model=item.get("model", ""))
        return {"run": run.id}

    def projects(self):
        """Folders a new job may start in. The browser only ever sees their ids."""
        found = {}
        self.scanner.refresh()
        with self.scanner.lock:
            for t in self.scanner.transcripts.values():
                if t.cwd and os.path.isdir(t.cwd):
                    found[t.cwd] = max(found.get(t.cwd, 0), t.mtime)
        roots = [Path(r).expanduser() for r in self.cfg.get("project_roots", [])] + [Path(self.agents_dir)]
        for root in roots:
            try:
                for d in root.iterdir():
                    if d.is_dir() and not d.name.startswith("."):
                        found.setdefault(str(d), 0)
            except OSError:
                pass
        items = [{"id": hashlib.sha1(p.encode()).hexdigest()[:12], "path": p, "label": home_path(p), "recent": ts}
                 for p, ts in found.items()]
        items.sort(key=lambda i: (-i["recent"], i["label"].lower()))
        return items

    def project_path(self, pid):
        for p in self.projects():
            if p["id"] == pid:
                return p["path"]
        raise Refused("unknown project folder")

    def new_job(self, pid, text, mode, model="", code_id="", code="", system="", helper=False, origin="phone"):
        text = str(text).strip()
        if not text or len(text) > runner.MAX_TEXT:
            raise Refused("write the first message (up to %d characters)" % runner.MAX_TEXT)
        path = pid if helper else self.project_path(pid)
        self.check_mode(mode, "bypass:new", code_id, code)
        sid = str(uuid.uuid4())
        run = self.runner.start(sid, path, text, mode, new=True, model=model, system=system,
                                origin=origin, helper=helper)
        return {"run": run.id, "sid": sid}

    def pump(self):
        """Start queued messages for sessions that have gone quiet."""
        now = time.time()
        for sid in list(self.runner.queues):
            if not self.runner.queue_of(sid) or self.runner.active(sid):
                continue
            t = self.scanner.transcripts.get(sid)
            if not t:
                continue
            live = self.scanner.live.get(sid)
            if live and live.get("desktop"):
                continue
            if self.status(t, live, now)[0] == "working":
                continue
            item = self.runner.take(sid)
            try:
                self.runner.start(sid, t.cwd, item["text"], item["mode"], model=item.get("model", ""))
            except Exception:
                self.runner.put_back(sid, item)

    def emit(self, event, data):
        try:
            self.hooks.fire(event, data if isinstance(data, dict) else {"value": data})
        except Exception:
            import traceback
            traceback.print_exc()

    def _on_brief(self, phase, state):
        name = state.get("name") or "Quill"
        if phase == "started":
            self.ledger.add("guild", "%s set out to write today's brief" % name, by=name)
        elif phase == "finished":
            text = " ".join((state.get("text") or state.get("error") or "done").split())
            self.ledger.add("guild", text[:240] or "the brief is in", by=name)
            try:
                self.progress.on_brief(state)
            except Exception:
                import traceback
                traceback.print_exc()

    def _pr_interval(self):
        try:
            return max(0.0, float(self.cfg.get("pr_poll_seconds", 300)))
        except (TypeError, ValueError):
            return 300.0

    def run_finished(self, run):
        self.scanner.last_scan = 0  # pick up the new transcript lines right away
        item = None
        try:
            snap = run.public()
            text = (snap.get("final") or "") + "\n" + "\n".join(e.get("t", "") for e in snap.get("events") or [])
            item = self.petitions.finish_build(run.sid, bool(run.ok), text)
        except Exception:
            import traceback
            traceback.print_exc()
        data = {"sid": run.sid, "ok": bool(run.ok), "cost": run.cost, "origin": run.origin or "",
                "text": (run.final or "")[:500]}
        if item:
            data.update(petition=item.get("id") or "", status=item.get("status") or "", pr_url=item.get("pr_url") or "")
            if item.get("pr_url") and item.get("status") == "building":
                self._pr_next = min(self._pr_next, time.time() + 5)
            if item.get("status") in ("failed", "merged", "closed"):
                self.ledger.add("guild", "Petition %s is %s" % (item.get("title") or item["id"], item["status"]))
                self.emit("petition.closed", petitions.public(item))
        try:
            self.progress.on_quest(run, item)
        except Exception:
            import traceback
            traceback.print_exc()
        self.emit("quest.finished", data)

    def _expire_petitions(self):
        try:
            expired = self.petitions.expire()
        except Exception:
            import traceback
            traceback.print_exc()
            return
        for item in expired:
            self.ledger.add("guild", "Petition expired: " + item["title"])
            self.emit("petition.decided", dict(petitions.public(item), decision="expire"))

    def _check_familiars(self):
        now = time.time()
        if now < getattr(self, "_fam_next", 0):
            return
        self._fam_next = now + 60
        try:
            jobs = self.familiars.jobs()
        except Exception:
            return
        for job in jobs:
            signature = (job.get("exit") or "", job.get("error") or "")
            if job["id"] not in self.familiar_seen:
                self.familiar_seen[job["id"]] = signature  # baseline, so a restart is not a new error
                continue
            if self.familiar_seen[job["id"]] == signature:
                continue
            self.familiar_seen[job["id"]] = signature
            if not signature[1] and not job.get("failed"):
                continue
            self.ledger.add("bot", "familiar error: %s: %s" % (job.get("label") or job["id"], signature[1] or signature[0]))
            self.emit("familiar.error", {"id": job["id"], "label": job.get("label") or "", "error": signature[1], "exit": signature[0]})

    def _poll_prs(self):
        try:
            changed = self.petitions.poll_prs()
        except Exception:
            import traceback
            traceback.print_exc()
            changed = []
        finally:
            self._pr_busy = False
        for item in changed:
            self.ledger.add("guild", "Petition %s is %s" % (item.get("title") or item["id"], item["status"]))
            self.emit("petition.closed", petitions.public(item))
            if item.get("status") == "merged":
                try:
                    self.progress.on_merged(item)
                except Exception:
                    import traceback
                    traceback.print_exc()

    def _check_prs(self):
        interval = self._pr_interval()
        if interval <= 0 or self._pr_busy or time.time() < self._pr_next:
            return
        self._pr_next = time.time() + interval
        self._pr_busy = True
        threading.Thread(target=self._poll_prs, daemon=True, name="pr-poll").start()

    def watch(self):
        """Background loop: notice status changes, push them, start queued messages."""
        first = True
        while True:
            try:
                self.scanner.refresh(min_interval=0)
                now = time.time()
                with self.scanner.lock:
                    current = {t.id: (t, self.status(t, self.scanner.live.get(t.id), now))
                               for t in self.scanner.transcripts.values()}
                for sid, (t, (status, note)) in current.items():
                    before, since = self.seen.get(sid, (None, now))
                    if status == "working" and before != "working":
                        since = now
                    if not first and not self.is_helper(t):
                        if status == "needs" and before != "needs":
                            self.notify(self.name_of(t) + " awaits your word", note or plain(t.last_text),
                                        tag="needs-" + sid[:8], url="/#s=" + sid)
                        elif before == "working" and status in ("idle", "finished") and now - since >= LONG_JOB:
                            self.notify(self.name_of(t) + " returned from the quest", plain(t.last_text) or "Done.",
                                        tag="done-" + sid[:8], url="/#s=" + sid)
                    self.seen[sid] = (status, since)
                first = False
                self.pump()
                self._expire_petitions()
                self._check_familiars()
                self._check_prs()
            except Exception:
                import traceback
                traceback.print_exc()
            time.sleep(4)

    def start_watching(self):
        if not self.monitor_started:
            self.monitor_started = True
            threading.Thread(target=self.watch, daemon=True, name="watch").start()
