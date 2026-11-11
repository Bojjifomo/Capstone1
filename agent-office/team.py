"""The guild's specialists: hire them, group them into divisions, pick the best fit, ask them."""
import json
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

import briefing
import fundaccess
import haiku
import handoff
import personas
import progress
import roster
import runner
import sessions
import store

# role keywords -> (job title, character class, division)
TITLES = [
    (("ceo", "chief", "executive", "regent"), "Chief Executive", "knight", "Executive"),
    (("operations", "orchestrator", "marshal"), "Operations Manager", "cleric", "Management"),
    (("innovator", "innovation", "product"), "Product Lead", "ranger", "Management"),
    (("security", "pentest", "appsec", "audit"), "Security Engineer", "knight", "Operations"),
    (("qa", "test", "tester", "review", "reviewer"), "QA Engineer", "knight", "Operations"),
    (("devops", "ops", "infra", "sre", "deploy", "ci"), "DevOps Engineer", "cleric", "Operations"),
    (("frontend", "ui", "css", "react", "web"), "Frontend Engineer", "bard", "Engineering"),
    (("backend", "api", "database", "db", "server"), "Backend Engineer", "knight", "Engineering"),
    (("engineer", "developer", "coder", "programmer", "code", "python", "coding"), "Software Engineer", "wizard", "Engineering"),
    (("defi", "crypto", "trading", "lp", "finance", "wallet", "onchain", "portfolio", "copy"), "Finance Specialist", "cleric", "Finance"),
    (("data", "ml", "stats", "analytics", "sql"), "Data Analyst", "wizard", "Research"),
    (("research", "researcher", "analyst", "scout", "investigate"), "Research Analyst", "ranger", "Research"),
    (("writer", "docs", "documentation", "copy", "content", "editor", "translator"), "Technical Writer", "bard", "Writing"),
    (("design", "designer", "ux", "illustrator", "video", "thumbnail"), "Designer", "bard", "Design"),
    (("pm", "manager", "planner", "project", "lead", "coordinator"), "Project Manager", "cleric", "Management"),
]
DEFAULT_TITLE = ("Specialist", None, "General")
NAME_POOL = ["Aldric", "Brynn", "Cassia", "Doran", "Elowen", "Fenwick", "Garrick", "Hollis", "Isolde", "Jory",
             "Kestrel", "Lyra", "Merrick", "Nyx", "Orin", "Perrin", "Quill", "Rowan", "Sable", "Thorne", "Ulric",
             "Vesper", "Wren", "Yara", "Zephyr"]
ROMAN = ["", " II", " III", " IV", " V", " VI", " VII", " VIII", " IX", " X"]
BIO_OPENERS = {
    "wizard": "keeps a desk full of half-finished branches and never trusts code they haven't tested twice",
    "knight": "sits by the door and asks every change for its papers",
    "ranger": "goes off to research and comes back with sources and footnotes",
    "bard": "turns tangled notes into words people actually read",
    "cleric": "keeps the books balanced and the team in good order",
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


SUGGEST_SYSTEM = (
    "You pick which teammates should take a task. Answer ONLY a JSON object: "
    '{"names": ["Name", ...], "why": "one short sentence"}. Pick at most 3, best first, only names from the team list. '
    "An empty list is fine when nobody fits.")
DRAFT_SYSTEM = (
    "You complete a hiring form for a specialist AI agent that works in Claude Code. Fill in every empty field and "
    "polish the rest; keep whatever the owner wrote in spirit. Answer ONLY a JSON object with keys "
    "name (one short first name), role (a job title under 80 characters), division (one of the divisions in use when "
    "one fits, else a short new one), instructions (their standing instructions in Markdown, 1500-4000 characters: "
    "mission, what they know, the steps they follow, what good looks like, what they never do, how they report), "
    "model (one of the offered models: haiku for checks, lookups and summaries; sonnet for everyday coding; opus only "
    "for hard design or judgement), effort (low, medium, high, xhigh or max) and reason (one sentence on the model "
    "and effort you chose). Write the instructions in English; say in them that anything the owner reads is in short "
    "casual Indonesian.")

GROKBOT_INSTRUCTIONS = (
    "You are bojjifomo's Grok in the guild hall. Chat casually in Indonesian. For anything about LP wallets, "
    "pools, the LP Call Bot or program health, look it up with the hub tools first and give the numbers. "
    "Keep answers short; offer detail when it helps.")


class Team:
    def __init__(self, office):
        self.office = office
        self.dir = Path(office.agents_dir)
        data = store.load("staff.json", {})
        self.staff = data.get("staff", [])
        self.divisions = data.get("divisions", [])
        self.lock = threading.Lock()
        self._office_names()

    def _office_names(self):
        """Divisions saved under the old guild names get their office names once."""
        changed = False
        for m in self.staff:
            new = roster.division_name(m.get("division"))
            if new != m.get("division"):
                m["division"], changed = new, True
        divisions = []
        for d in self.divisions:
            new = roster.division_name(d)
            if new not in divisions:
                divisions.append(new)
        if divisions != self.divisions:
            self.divisions, changed = divisions, True
        if changed:
            self.save()

    def save(self):
        store.save("staff.json", {"staff": self.staff, "divisions": self.divisions})

    def resident(self):
        """The orchestrator, stationed in the Counting House. Not stored in staff.json."""
        name = "Quill"
        if self.office is not None and getattr(self.office, "briefing", None):
            name = self.office.briefing.name
        return {
            "id": briefing.ORCH_ID, "name": name, "builtin": True,
            "role": "LP orchestrator, read-only briefs", "title": "Treasurer", "kind": "cleric",
            "division": "Finance", "model": "", "seed": "quill-orchestrator",
            "folder": "", "instructions": "Runs python3 -m orchestrator brief. Write tools stay off.",
            "bio": ("%s keeps the Counting House: a read-only brief of wallets, pools and the web showcase. "
                    "Write tools never run unless you start the orchestrator yourself.") % name,
        }

    def find(self, key):
        key = str(key).strip().lower()
        exact = [s for s in self.staff if s["id"] == key or s["name"].lower() == key]
        if exact:
            return exact[0]
        first = [s for s in self.staff if s["name"].lower().split()[0] == key]
        if len(first) == 1:
            return first[0]  # "lyra" is ambiguous once there is a Lyra II
        resident = self.resident()
        if key in (resident["id"], resident["name"].lower(), resident["name"].lower().split()[0]):
            return resident
        return None

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

    def hire(self, name, role, instructions, division="", model="sonnet", effort=""):
        name = " ".join(str(name).split())[:40]
        role = " ".join(str(role).split())[:80]
        instructions = str(instructions).strip()[:8000]
        if not role:
            raise ValueError("give the recruit a role")
        if model not in ("", "opus", "sonnet", "haiku", "grok"):
            raise ValueError("unknown model")
        if effort not in runner.EFFORTS:
            raise ValueError("unknown effort")
        self._offered(model)
        with self.lock:
            if model == "opus":
                self._take_opus_seat()
            final = self.free_name(name)
            title, kind, default_division = title_for(role)
            division = roster.division_name(" ".join(str(division).split())[:40]) or default_division
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
                "effort": effort, "mode": "acceptEdits", "seed": secrets.token_hex(4), "hired": time.time(),
                "bio": "%s the %s %s. Hired %s for %s." % (final, title, BIO_OPENERS[kind],
                                                           "%d %s" % (datetime.now().day, datetime.now().strftime("%B %Y")), role),
            }
            self._write_files(member)
            self.staff.append(member)
            if division not in self.divisions:
                self.divisions.append(division)
            self.save()
        return member, (name if name and name != final else "")

    def _write_files(self, member):
        """CLAUDE.md, plus the Fund section and permissions for Counting House members."""
        cfg = getattr(self.office, "cfg", {}) or {}
        text = self.brief_file(member)
        if fundaccess.in_fund(member):
            text += fundaccess.brief_section(cfg, member["name"])
        (Path(member["folder"]) / "CLAUDE.md").write_text(text, encoding="utf-8")
        fundaccess.sync_settings(member, cfg)

    @staticmethod
    def brief_file(m):
        return ("# %s, %s\n\nYou are %s, a specialist in bojjifomo's Agent Office. Your role: %s.\n\n"
                "## Your instructions\n\n%s\n\n## How you work\n\n"
                "- A lead session sends you a self-contained brief. Do the work, then end with a short answer "
                "the lead can use as-is: what you did, what you found, and anything left open.\n"
                "- Never ask the user questions. If something is unclear, make the most reasonable assumption "
                "and say which one you made.\n"
                "- Work only inside the folders the brief names.\n") % (
            m["name"], m["title"], m["name"], m["role"], m["instructions"] or "(none yet)")

    def ensure_grokbot(self):
        """With an xAI key set, the guild has Grokbot: chat with it as @Grokbot. Hired once, then left alone."""
        if any(s.get("grokbot") for s in self.staff):
            return None
        member, _ = self.hire("Grokbot", "Grok assistant: chats with bojjifomo, reads the LP hub, helps with code",
                              GROKBOT_INSTRUCTIONS, division="Finance", model="grok")
        with self.lock:
            member["grokbot"] = True
            member["kind"] = "bard"
            self.save()
        return member

    def _offered(self, model):
        if model == "grok":
            if not getattr(self.office, "grok", None):
                raise ValueError("Grok is off: set XAI_API_KEY (or xai_api_key in config.json) and restart")
            return
        chain = getattr(self.office, "chain", None)
        if model and chain and model not in chain.allowed():
            raise ValueError("%s is not offered right now. Offered: %s" % (model, ", ".join(chain.allowed())))

    def _take_opus_seat(self):
        """Raise when every Opus seat is taken. Call with self.lock held."""
        chain = getattr(self.office, "chain", None)
        seats = chain.settings["opus_seats"] if chain else 2
        if sum(1 for s in self.staff if s.get("model") == "opus") >= seats:
            raise RuntimeError("the %d Opus seats are taken; free one first" % seats)

    def set_model(self, key, model, by="bojjifomo"):
        """Move a specialist to haiku, sonnet or opus. Opus seats are limited; only bojjifomo changes the CEO's."""
        if model not in ("haiku", "sonnet", "opus", "grok"):
            raise ValueError("pick haiku, sonnet, opus or grok")
        self._offered(model)
        with self.lock:
            member = self.find(key)
            if not member:
                raise LookupError("no teammate called %r" % key)
            if member.get("builtin"):
                raise ValueError("%s is not a Claude Code session" % member["name"])
            chain = getattr(self.office, "chain", None)
            if chain and chain.roles.get("ceo") == member["id"] and by != "bojjifomo":
                raise PermissionError("only bojjifomo changes the CEO's model")
            old = member.get("model") or "default"
            if model == "opus" and old != "opus":
                self._take_opus_seat()
            member["model"] = model
            self.save()
        return member, old

    def update(self, key, role, instructions, division=""):
        role = " ".join(str(role).split())
        instructions = str(instructions).strip()
        division = " ".join(str(division).split())
        if not role:
            raise ValueError("give them a role")
        if len(role) > 80:
            raise ValueError("keep the role to 80 characters; the details belong in the instructions")
        if len(instructions) > 8000:
            raise ValueError("instructions are limited to 8000 characters")
        if len(division) > 40:
            raise ValueError("keep the division name to 40 characters")
        with self.lock:
            member = self.find(key)
            if not member:
                raise LookupError("no teammate called %r" % key)
            if member.get("builtin"):
                raise ValueError("%s is built in and cannot be edited" % member["name"])
            member["role"] = role
            member["instructions"] = instructions
            if division:
                division = roster.division_name(division)
                member["division"] = division
                if division not in self.divisions:
                    self.divisions.append(division)
            Path(member["folder"]).mkdir(parents=True, exist_ok=True)
            self._write_files(member)
            used = {s["division"] for s in self.staff}
            self.divisions = [d for d in self.divisions if d in used]
            self.save()
            return member

    def dismiss(self, sid):
        with self.lock:
            member = self.find(sid)
            if not member or member.get("builtin"):
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

    def set_effort(self, key, effort):
        if effort not in runner.EFFORTS:
            raise ValueError("pick low, medium, high, xhigh, max, or leave it as it is")
        member = self.find(key)
        if not member or member.get("builtin"):
            raise LookupError("no teammate called %r" % key)
        with self.lock:
            was = member.get("effort", "")
            member["effort"] = effort
            self.save()
        return {"name": member["name"], "was": was or "default", "effort": effort or "default"}

    # ---- helpers that think: who should take a task, and a hire form filled in ----
    def suggest(self, cfg, task, limit=3):
        """Teammates for a task: Haiku picks from the roster; keyword ranking when it cannot."""
        task = " ".join(str(task).split())[:2000]
        people = [s for s in self.staff if not s.get("grokbot")]
        if not task or not people:
            return {"names": [], "why": "", "by": "none"}
        names = {s["name"].lower(): s["name"] for s in people}
        try:
            roster_text = "\n".join("- %s: %s (%s)" % (s["name"], s["role"], s["division"]) for s in people)
            raw = haiku.ask(cfg, SUGGEST_SYSTEM, "Task:\n%s\n\nTeam:\n%s" % (task, roster_text), timeout=40)
            start, end = raw.index("{"), raw.rindex("}") + 1
            data = json.loads(raw[start:end])
            picked = [names[str(n).strip().lower()] for n in (data.get("names") or []) if str(n).strip().lower() in names]
            if picked:
                return {"names": picked[:limit], "why": str(data.get("why") or "")[:200], "by": "haiku"}
        except Exception:
            pass
        ranked = [r["name"] for r in self.rank(task) if r["score"] > 0][:limit]
        return {"names": ranked, "why": "picked by keyword match", "by": "keywords"}

    def draft(self, cfg, fields, offered, divisions):
        """Fill in and polish a hire form with Sonnet. Returns the complete form; never hires."""
        given = {k: " ".join(str(fields.get(k) or "").split())[:2000] for k in ("name", "role", "division", "instructions", "model", "effort")}
        prompt = ("Offered models: %s. Efforts: low, medium, high, xhigh, max. Divisions in use: %s.\n\nForm so far:\n%s" % (
            ", ".join(offered), ", ".join(divisions) or "none yet",
            "\n".join("%s: %s" % (k, v or "(empty)") for k, v in given.items())))
        raw = haiku.ask(cfg, DRAFT_SYSTEM, prompt, timeout=90, model="sonnet")
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
        out = {}
        for k in ("name", "role", "division", "instructions", "reason"):
            out[k] = str(data.get(k) or given.get(k) or "").strip()
        out["name"] = out["name"][:40]
        out["role"] = out["role"][:80]
        out["division"] = roster.division_name(out["division"])[:40]
        out["instructions"] = out["instructions"][:8000]
        model = str(data.get("model") or "").lower()
        out["model"] = model if model in offered else (given["model"] if given["model"] in offered else (offered[0] if offered else "sonnet"))
        effort = str(data.get("effort") or "").lower()
        out["effort"] = effort if effort in runner.EFFORTS and effort else "medium"
        return out

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
            entry["effort"] = s.get("effort", "")
            entry.update(status=status, note=note, sid=t.id if t else "", active=t.mtime if t else 0,
                         last=(t.last_text or "")[-300:] if t else "", pay=self.pay_of(s),
                         persona=personas.persona(s["seed"], s["role"] + " " + s["instructions"][:200],
                                                  name=s["name"], title=s["title"], topic=s["role"].lower()))
            entry["persona"]["bio"] = s["bio"] + " " + entry["persona"]["bio"]
            entry["progress"] = self.office.progress.public(s["id"])
            entry["persona"]["title"] = progress.titled(entry["persona"]["title"], entry["progress"]["rank"])
            chain = getattr(self.office, "chain", None)
            entry["chain"] = chain.role_of(s["id"]) if chain else ""
            out.append(entry)
        out.append(self._resident_entry())
        divisions = list(self.divisions)
        if fundaccess.FUND_DIVISION not in divisions:
            divisions.append(fundaccess.FUND_DIVISION)
        return {"staff": out, "divisions": divisions}

    def _resident_entry(self):
        member = self.resident()
        state = self.office.briefing.public() if getattr(self.office, "briefing", None) else {}
        running = bool(state.get("running"))
        status = "working" if running else "idle"
        note = "writing today's brief" if running else ""
        text = state.get("text") or ""
        active = state.get("ended") or time.time()
        entry = {k: member[k] for k in ("id", "name", "role", "title", "kind", "seed", "division", "bio", "model")}
        entry.update(status=status, note=note, sid="", active=active, builtin=True,
                     last=text[-300:], pay={"today": 0.0, "week": 0.0, "month": 0.0},
                     persona=personas.persona(member["seed"], member["role"], name=member["name"],
                                              title=member["title"], topic="LP briefs"))
        entry["persona"]["bio"] = member["bio"] + " " + entry["persona"]["bio"]
        entry["progress"] = self.office.progress.public(member["id"])
        entry["persona"]["title"] = progress.titled(entry["persona"]["title"], entry["progress"]["rank"])
        return entry

    def pay_of(self, member):
        total = {"today": 0.0, "week": 0.0, "month": 0.0}
        with self.office.scanner.lock:
            for t in self.office.scanner.transcripts.values():
                if t.cwd == member["folder"]:
                    for k, v in t.pay().items():
                        total[k] += v
        return {k: round(v, 4) for k, v in total.items()}

    def ask(self, key, brief, origin="office-ask", from_queue=False, mode=None, add_dirs=()):
        member = self.find(key)
        if not member:
            raise LookupError("no teammate called %r; try `office team`" % key)
        brief = str(brief).strip()
        if not brief:
            raise ValueError("the brief is empty")
        if member.get("builtin"):
            # The text is only a label. The process is always `orchestrator brief`, read-only.
            if not from_queue:
                held = self.office.economy.consider(False, False, "brief", "today's brief", {})
                if held:
                    if held.get("blocked"):
                        raise RuntimeError("the treasury is empty")
                    held["name"] = member["name"]
                    return held
            result = self.office.briefing.start()
            result["name"] = member["name"]
            return result
        Path(member["folder"]).mkdir(parents=True, exist_ok=True)
        if "RESULT:" not in brief:
            brief += handoff.CONTRACT
        result = self.office.new_job(member["folder"], brief, mode or member.get("mode", "acceptEdits"),
                                     model=member.get("model", "sonnet"), helper=True, origin=origin,
                                     from_queue=from_queue, add_dirs=add_dirs, effort=member.get("effort", ""))
        if result.get("queued_for_gold"):
            result["name"] = member["name"]
            return result
        if getattr(self.office, "raids", None):
            self.office.raids.join(member["name"])
        result["name"] = member["name"]
        return result
