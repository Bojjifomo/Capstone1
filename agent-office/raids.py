"""A party-leader quest is a raid boss. Subtasks chip its health; the leader's return ends it.

HP stays at least 1 until the leader's own run finishes, so a long raid does not
die on the third errand. Victory pays loot XP to the leader and everyone who was
sent in. State lives in raids.json.
"""
import threading
import time

import store

LOOT = 80
HIT = 34


class Raids:
    def __init__(self, office=None):
        self.office = office
        saved = store.load("raids.json", {})
        if not isinstance(saved, dict):
            saved = {}
        self.current = saved.get("current") if isinstance(saved.get("current"), dict) else None
        self.lock = threading.Lock()

    def save(self):
        store.save("raids.json", {"current": self.current})

    def begin(self, sid, title):
        title = " ".join(str(title or "").split())[:80] or "A raid"
        raid = {"id": sid, "title": title, "hp": 100, "max": 100, "party": [],
                "status": "fighting", "started": time.time(), "ended": None, "loot": 0}
        with self.lock:
            if self.current and self.current.get("status") == "fighting" and self.current.get("id") != sid:
                self.current["status"] = "fled"
                self.current["ended"] = time.time()
            self.current = raid
            self.save()
        ledger = getattr(self.office, "ledger", None) if self.office is not None else None
        if ledger:
            ledger.add("guild", "A raid begins: " + title)
        return raid

    def join(self, name):
        name = " ".join(str(name or "").split())[:40]
        if not name:
            return
        with self.lock:
            raid = self.current
            if not raid or raid.get("status") != "fighting":
                return
            if name not in raid["party"]:
                raid["party"].append(name)
                self.save()

    def strike(self, name):
        """One finished errand. Health does not hit zero until the leader returns."""
        name = " ".join(str(name or "").split())[:40] or "an ally"
        with self.lock:
            raid = self.current
            if not raid or raid.get("status") != "fighting":
                return None
            if name not in raid["party"]:
                raid["party"].append(name)
            raid["hp"] = max(1, int(raid.get("hp") or 100) - HIT)
            self.save()
            return raid["hp"]

    def finish(self, ok):
        with self.lock:
            raid = self.current
            if not raid or raid.get("status") != "fighting":
                return None
            raid["ended"] = time.time()
            if ok:
                raid["hp"] = 0
                raid["status"] = "victory"
                raid["loot"] = LOOT
            else:
                raid["status"] = "fled"
                raid["loot"] = 0
            self.save()
            done = dict(raid)
        self._after(done)
        return done

    def _after(self, raid):
        ledger = getattr(self.office, "ledger", None) if self.office is not None else None
        if raid.get("status") == "victory":
            self._loot(raid)
            if ledger:
                ledger.add("guild", "The party felled %s. Loot: %d XP each." % (raid["title"], raid.get("loot") or 0))
        elif ledger:
            ledger.add("guild", "The raid on %s broke." % raid["title"])

    def _loot(self, raid):
        progress = getattr(self.office, "progress", None) if self.office is not None else None
        if not progress:
            return
        seen = set()
        cwd = ""
        if getattr(self.office, "scanner", None):
            t = self.office.scanner.transcripts.get(raid["id"])
            cwd = t.cwd if t else ""
        key, name = progress.actor_for_cwd(cwd, raid["id"])
        progress.grant(key, name, raid.get("loot") or LOOT, "raid", raid["title"], "Epic", ok=True, cost=None)
        seen.add(key)
        for member in raid.get("party") or []:
            key, name = progress.actor_named(member)
            if key in seen:
                continue
            seen.add(key)
            progress.grant(key, name, raid.get("loot") or LOOT, "raid", raid["title"], "Epic", ok=True, cost=None)

    def on_run(self, run):
        with self.lock:
            raid = self.current
            fighting = bool(raid and raid.get("status") == "fighting")
            boss = fighting and raid.get("id") == getattr(run, "sid", None)
        if not fighting:
            return None
        if boss:
            return self.finish(bool(getattr(run, "ok", False)))
        if not getattr(run, "helper", False) or not getattr(run, "ok", False):
            return None
        name = "an ally"
        if self.office is not None and getattr(self.office, "team", None):
            member = self.office.team.member_for_cwd(getattr(run, "cwd", ""))
            if member:
                name = member["name"]
        return self.strike(name)

    def public(self):
        raid = self.current
        if not raid:
            return None
        if raid.get("status") != "fighting" and time.time() - (raid.get("ended") or 0) > 600:
            return None
        return {k: raid.get(k) for k in ("id", "title", "hp", "max", "party", "status", "started", "ended", "loot")}
