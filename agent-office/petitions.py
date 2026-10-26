"""Petitions: update proposals from the guild that wait for bojjifomo's yes or no."""
import secrets
import threading
import time

import store

STATUSES = ("pending", "approved", "rejected", "building")
IMPACT = ("", "high", "medium", "low")
EFFORT = ("", "S", "M", "L")

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


class Petitions:
    def __init__(self):
        self.items = store.load("petitions.json", [])
        self.lock = threading.Lock()

    def save(self):
        store.save("petitions.json", self.items)

    def get(self, pid):
        return next((p for p in self.items if p["id"] == pid), None)

    def add(self, by, title, app, summary, plan, impact="", effort=""):
        title = " ".join(str(title).split())[:120]
        if not title:
            raise ValueError("a petition needs a title")
        plan = str(plan).strip()[:20000]
        if not plan:
            raise ValueError("a petition needs a plan")
        item = {
            "id": secrets.token_hex(4), "by": " ".join(str(by).split())[:40] or "someone",
            "title": title, "app": " ".join(str(app).split())[:60], "summary": " ".join(str(summary).split())[:400],
            "plan": plan, "impact": impact if impact in IMPACT else "", "effort": effort if effort in EFFORT else "",
            "status": "pending", "created": time.time(), "decided": None, "note": "", "sid": "",
        }
        with self.lock:
            self.items.append(item)
            self.items = self.items[-200:]
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
            item["status"] = "building"
            item["sid"] = sid
            self.save()

    def brief(self, item):
        fields = dict(item, note="\n\nbojjifomo's note: " + item["note"] if item["note"] else "")
        return BUILD_BRIEF.format(**fields)

    def listing(self):
        order = {"pending": 0, "approved": 1, "building": 2, "rejected": 3}
        return sorted(self.items, key=lambda p: (order.get(p["status"], 9), -p["created"]))

    def pending_count(self):
        return sum(1 for p in self.items if p["status"] == "pending")
