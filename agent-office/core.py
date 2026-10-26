"""The office itself: sessions with a status, names, archive. Shared by the HTTP routes."""
import threading
import time
from pathlib import Path

import sessions
import store

ACTIVE_WINDOW = 60          # transcript written this recently = working
IDLE_WINDOW = 2 * 3600      # after this with no process = finished
QUESTION_WINDOW = 24 * 3600  # an unanswered question older than this is stale
PERMISSION_STALL = 90       # pending tool + live process + silence = probably a permission prompt


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
        self.flags = {}  # sid -> (ts, message), from the Notification hook; memory only
        self.agents_dir = str(Path(cfg.get("agents_dir", "~/Agents")).expanduser())
        self.runner = None  # set by server.py once the runner exists
        self.team = None

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
        return self.scanner.transcripts.get(sid)

    def detail(self, sid):
        t = self.transcript(sid)
        if not t:
            return None
        info = self.summary(t, time.time())
        info["messages"] = list(t.messages)
        info["pending"] = [s for _, s in t.pending.values()]
        info["tokens"] = t.usage_total()[:4]
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

    def flag(self, sid, message):
        self.flags[sid] = (time.time(), " ".join(str(message).split())[:160] or "needs your permission")
