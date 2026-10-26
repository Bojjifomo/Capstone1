"""The guild's specialists: hire them, group them into divisions, pick the best fit, ask them."""
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

import personas
import sessions
import store

# role keywords -> (job title, character class, division)
TITLES = [
    (("security", "pentest", "appsec", "audit"), "Sentinel of the Wards", "knight", "The Watchtower"),
    (("qa", "test", "tester", "review", "reviewer"), "Warden of Trials", "knight", "The Watchtower"),
    (("devops", "ops", "infra", "sre", "deploy", "ci"), "Keeper of the Forge-fires", "cleric", "The Watchtower"),
    (("frontend", "ui", "css", "react", "web"), "Glasswright", "bard", "The Forge"),
    (("backend", "api", "database", "db", "server"), "Artificer of the Deep Halls", "knight", "The Forge"),
    (("engineer", "developer", "coder", "programmer", "code", "python", "coding"), "Artificer", "wizard", "The Forge"),
    (("defi", "crypto", "trading", "lp", "finance", "wallet", "onchain", "portfolio"), "Treasurer", "cleric", "The Counting House"),
    (("data", "ml", "stats", "analytics", "sql"), "Seer of Numbers", "wizard", "The Observatory"),
    (("research", "researcher", "analyst", "scout", "investigate"), "Loremaster", "ranger", "The Observatory"),
    (("writer", "docs", "documentation", "copy", "content", "editor", "translator"), "Scribe", "bard", "The Scriptorium"),
    (("design", "designer", "ux", "illustrator"), "Illuminator", "bard", "The Atelier"),
    (("pm", "manager", "planner", "project", "lead", "coordinator"), "Quartermaster", "cleric", "The War Room"),
]
DEFAULT_TITLE = ("Journeyman Adventurer", None, "The Common Room")
NAME_POOL = ["Aldric", "Brynn", "Cassia", "Doran", "Elowen", "Fenwick", "Garrick", "Hollis", "Isolde", "Jory",
             "Kestrel", "Lyra", "Merrick", "Nyx", "Orin", "Perrin", "Quill", "Rowan", "Sable", "Thorne", "Ulric",
             "Vesper", "Wren", "Yara", "Zephyr"]
ROMAN = ["", " II", " III", " IV", " V", " VI", " VII", " VIII", " IX", " X"]
BIO_OPENERS = {
    "wizard": "keeps a tower of half-finished scrolls and never trusts a spell they haven't tested twice",
    "knight": "stands guard at the gate and asks every change for its papers",
    "ranger": "scouts far ahead and comes back with maps, sources and footnotes",
    "bard": "turns tangled notes into words people actually read",
    "cleric": "keeps the ledgers balanced and the party in good order",
}
STOP = set("the and for with that this from into your you are was were will have has had not but all any can "
           "our out about over then than them they what when where which who why how make made use using "
           "please need want some more most very just also like into onto per via its".split())


def words(text):
    return [w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 1 and w not in STOP]


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "member"


def title_for(role):
    tokens = set(words(role))
    for keys, title, kind, division in TITLES:
        if tokens & set(keys):
            return title, kind, division
    return DEFAULT_TITLE


class Team:
    def __init__(self, office):
        self.office = office
        self.dir = Path(office.agents_dir)
        data = store.load("staff.json", {})
        self.staff = data.get("staff", [])
        self.divisions = data.get("divisions", [])
        self.lock = threading.Lock()

    def save(self):
        store.save("staff.json", {"staff": self.staff, "divisions": self.divisions})

    def find(self, key):
        key = str(key).strip().lower()
        exact = [s for s in self.staff if s["id"] == key or s["name"].lower() == key]
        if exact:
            return exact[0]
        first = [s for s in self.staff if s["name"].lower().split()[0] == key]
        return first[0] if len(first) == 1 else None  # "lyra" is ambiguous once there is a Lyra II

    def free_name(self, wanted):
        taken = {s["name"].lower() for s in self.staff}
        if wanted:
            for suffix in ROMAN:
                if (wanted + suffix).lower() not in taken:
                    return wanted + suffix
        for name in NAME_POOL:
            if name.lower() not in taken:
                return name
        return "Recruit %d" % (len(self.staff) + 1)

    def hire(self, name, role, instructions, division="", model="sonnet"):
        name = " ".join(str(name).split())[:40]
        role = " ".join(str(role).split())[:80]
        instructions = str(instructions).strip()[:8000]
        if not role:
            raise ValueError("give the recruit a role")
        if model not in ("", "opus", "sonnet", "haiku"):
            raise ValueError("unknown model")
        with self.lock:
            final = self.free_name(name)
            title, kind, default_division = title_for(role)
            division = " ".join(str(division).split())[:40] or default_division
            kind = kind or ["wizard", "knight", "ranger", "bard", "cleric"][len(self.staff) % 5]
            folder = self.dir / slug(final)
            n = 2
            while folder.exists() and any(s["folder"] == str(folder) for s in self.staff):
                folder = self.dir / ("%s-%d" % (slug(final), n))
                n += 1
            folder.mkdir(parents=True, exist_ok=True)
            member = {
                "id": secrets.token_hex(4), "name": final, "role": role, "title": title, "kind": kind,
                "division": division, "instructions": instructions, "folder": str(folder), "model": model,
                "mode": "acceptEdits", "seed": secrets.token_hex(4), "hired": time.time(),
                "bio": "%s the %s %s. Hired %s for %s." % (final, title, BIO_OPENERS[kind],
                                                           "%d %s" % (datetime.now().day, datetime.now().strftime("%B %Y")), role),
            }
            (folder / "CLAUDE.md").write_text(self.brief_file(member), encoding="utf-8")
            self.staff.append(member)
            if division not in self.divisions:
                self.divisions.append(division)
            self.save()
        return member, (name if name and name != final else "")

    @staticmethod
    def brief_file(m):
        return ("# %s, %s\n\nYou are %s, a specialist in bojjifomo's guild. Your role: %s.\n\n"
                "## Your instructions\n\n%s\n\n## How you work\n\n"
                "- A lead session sends you a self-contained brief. Do the work, then end with a short answer "
                "the lead can use as-is: what you did, what you found, and anything left open.\n"
                "- Never ask the user questions. If something is unclear, make the most reasonable assumption "
                "and say which one you made.\n"
                "- Work only inside the folders the brief names.\n") % (
            m["name"], m["title"], m["name"], m["role"], m["instructions"] or "(none yet)")

    def dismiss(self, sid):
        with self.lock:
            member = self.find(sid)
            if not member:
                return False
            self.staff.remove(member)  # their folder in ~/Agents stays put
            used = {s["division"] for s in self.staff}
            self.divisions = [d for d in self.divisions if d in used]
            self.save()
            return True

    def rank(self, task):
        """Best fit for a task, by keyword overlap with role, title, division and instructions."""
        want = words(task)
        results = []
        for s in self.staff:
            fields = [(words(s["role"]), 3.0), (words(s["title"]), 1.5), (words(s["division"]), 1.0),
                      (words(s["instructions"]), 1.0)]
            title_keys = next((keys for keys, title, _, _ in TITLES if title == s["title"]), ())
            fields.append((list(title_keys), 2.0))
            score, hits = 0.0, []
            for w in set(want):
                best = 0.0
                for vocab, weight in fields:
                    if w in vocab or any(len(w) > 4 and v[:5] == w[:5] for v in vocab):
                        best = max(best, weight)
                if best:
                    score += best
                    hits.append(w)
            results.append({"name": s["name"], "title": s["title"], "role": s["role"],
                            "score": round(score, 1), "because": sorted(hits)[:8]})
        results.sort(key=lambda r: -r["score"])
        return results

    # ---- runtime --------------------------------------------------------
    def member_for_cwd(self, cwd):
        for s in self.staff:
            if cwd and (cwd == s["folder"] or cwd.startswith(s["folder"] + "/")):
                return s
        return None

    def staff_for_session(self, t):
        return self.card(self.member_for_cwd(t.cwd))

    @staticmethod
    def card(s):
        if not s:
            return None
        return {"id": s["id"], "name": s["name"], "title": s["title"], "kind": s["kind"], "seed": s["seed"],
                "division": s["division"]}

    def latest_session(self, member):
        best = None
        with self.office.scanner.lock:
            for t in self.office.scanner.transcripts.values():
                if t.cwd == member["folder"] and (best is None or t.mtime > best.mtime):
                    best = t
        return best

    def roster(self):
        now = time.time()
        out = []
        for s in self.staff:
            t = self.latest_session(s)
            status, note = self.office.status(t, self.office.scanner.live.get(t.id), now) if t else ("idle", "")
            if status in ("finished", "archived"):
                status = "idle"
            if t and self.office.runner.active(t.id):
                status, note = "working", "on an errand from the lead"
            entry = {k: s[k] for k in ("id", "name", "role", "title", "kind", "seed", "division", "bio", "model")}
            entry.update(status=status, note=note, sid=t.id if t else "", active=t.mtime if t else 0,
                         last=(t.last_text or "")[-300:] if t else "", pay=self.pay_of(s),
                         persona=personas.persona(s["seed"], s["role"] + " " + s["instructions"][:200],
                                                  name=s["name"], title=s["title"], topic=s["role"].lower()))
            entry["persona"]["bio"] = s["bio"] + " " + entry["persona"]["bio"]
            out.append(entry)
        return {"staff": out, "divisions": list(self.divisions)}

    def pay_of(self, member):
        total = {"today": 0.0, "week": 0.0, "month": 0.0}
        with self.office.scanner.lock:
            for t in self.office.scanner.transcripts.values():
                if t.cwd == member["folder"]:
                    for k, v in t.pay().items():
                        total[k] += v
        return {k: round(v, 4) for k, v in total.items()}

    def ask(self, key, brief, origin="office-ask"):
        member = self.find(key)
        if not member:
            raise LookupError("no teammate called %r; try `office team`" % key)
        brief = str(brief).strip()
        if not brief:
            raise ValueError("the brief is empty")
        Path(member["folder"]).mkdir(parents=True, exist_ok=True)
        result = self.office.new_job(member["folder"], brief, member.get("mode", "acceptEdits"),
                                     model=member.get("model", "sonnet"), helper=True, origin=origin)
        result["name"] = member["name"]
        return result
