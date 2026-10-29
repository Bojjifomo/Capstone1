"""The chain of command.

Your programs are the guild's interns. Each intern has a warden (a recruited specialist) who looks after it.
Above them: the innovator files petitions, the operations manager (the orchestrator) makes rounds and sends
people where they are needed, and the CEO steps in when the operations manager fails or an incident gets
past them. Anything the CEO cannot fix comes to bojjifomo's phone.

Escalation for a broken intern:  warden -> operations manager -> CEO -> you.
Health checks are fixed and run here, never as shell text: a launchd job's state, an HTTP GET, a file's age.
"""
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import store

ROLES = ("ceo", "ops", "innovator")
ROLE_NAMES = {"ceo": "CEO", "ops": "Operations Manager", "innovator": "Innovator", "warden": "Warden", "you": "you"}
LEVELS = ("warden", "ops", "ceo", "you")
DEFAULTS = {
    "auto": True,                 # dispatch and rounds on their own
    "check_minutes": 5,           # how often interns are checked
    "fail_after": 2,              # failed checks in a row before an incident opens
    "ops_every_hours": 6,         # the operations manager's rounds
    "innovator_every_days": 7,    # the innovator's review
    "stuck_minutes": 90,          # a round still running after this counts as failed
    "max_auto_per_day": 12,       # automatic dispatches (incidents and rounds) per day
    "review_hours": 24,           # the CEO's budget review (0 = off)
    "guard_pct": 80,              # at this share of the daily budget, automatic rounds stop
    "opus_seats": 2,              # how many specialists may run on Opus at once
}
LIMITS = {"check_minutes": (1, 240), "fail_after": (1, 10), "ops_every_hours": (1, 72),
          "innovator_every_days": (1, 60), "stuck_minutes": (15, 600), "max_auto_per_day": (0, 100),
          "review_hours": (0, 168), "guard_pct": (10, 100), "opus_seats": (0, 20)}
MODE = "auto"  # Claude Code's guarded auto mode: no prompts, risky actions still blocked

# What `office chain` / the panel use to recruit a missing role.
PRESETS = {
    "ceo": {
        "name": "Aurelian", "role": "CEO of the guild (chief executive, backup for the operations manager)",
        "division": "The Keep", "model": "opus",
        "instructions": (
            "You are the CEO of bojjifomo's guild. You are called only when the Operations Manager failed, "
            "is stuck, or could not fix an incident.\n"
            "1. Restore service first: find out why the program is down, restart its launchd job, re-run a sync, "
            "fix the code on a new branch and open a pull request.\n"
            "2. Then make sure the Operations Manager can work again, and say what went wrong with them.\n"
            "3. Delegate with `office ask <name> \"<brief>\"` when a warden is the better fit.\n"
            "Never: merge to main, force-push, delete data, read or print secrets (.env, keys), move funds, "
            "or change wallets. If you cannot fix it, write exactly what bojjifomo has to do, step by step."),
    },
    "ops": {
        "name": "Bram", "role": "Operations manager (orchestrator) who oversees every program and the guild",
        "division": "The War Room", "model": "sonnet",
        "instructions": (
            "You are the Operations Manager of bojjifomo's guild (the orchestrator). Every program is an intern "
            "with a warden who looks after it. On each round you read the situation report, decide what needs "
            "action and send the right person:\n"
            "- `office ask <warden> \"<self-contained brief>\" --no-wait` for fixes and checks,\n"
            "- `office propose ...` for improvements that need bojjifomo's approval,\n"
            "- `office incident <id> resolve \"<note>\"` once you verified a fix yourself.\n"
            "Fix small things yourself only when no warden fits. Code changes go on a branch with a pull "
            "request; never merge to main, force-push, delete data or touch secrets. End every round with a "
            "short ops report: what is healthy, what you did, what is still open."),
    },
    "innovator": {
        "name": "Orin", "role": "Product innovator and planner who files update petitions",
        "division": "The War Room", "model": "sonnet",
        "instructions": (
            "You are the guild's innovator. Study the programs (the interns), their incident history and the "
            "guild log, then file the few improvements that matter most as petitions with `office propose`. "
            "Each petition needs a clear problem, the change file by file, how to test it and the risk. "
            "Check `office petitions` first so you never repeat one. You never build; bojjifomo decides."),
    },
}

RULES = """Rules for every guild run:
- Work in the folders named here. Read what you need, change only what the fix needs.
- Code changes go on a new git branch with a pull request. Never merge to main, never force-push.
- Never delete data, never read or print secrets (.env, keys, tokens), never move funds or touch wallets.
- `{cli} chain` shows every intern and incident. `{cli} check <intern>` checks one again right now.
- End with a short report: what was wrong, what you did, whether it is healthy now, what is left."""

WARDEN_BRIEF = """You are the warden of the intern "{name}" (a program in bojjifomo's guild).
It is failing its health checks. Incident {iid}, opened {opened}.

Program folder: {project}
Checks: {checks}
What failed: {reasons}
{history}
Find the cause and fix it. Restarting its launchd job (`launchctl kickstart gui/$(id -u)/<label>`) or
re-running its sync is fine. When you are done, run `{cli} check {iid_intern}` to confirm it is healthy.

{rules}"""

ESCALATE_BRIEF = """You are the {role} of bojjifomo's guild. An intern is still down after {tries}.
Incident {iid}: "{name}", opened {opened}.

Program folder: {project}
Checks: {checks}
What failed: {reasons}
{history}
Take over: fix it yourself or send someone better suited (`{cli} team`, `{cli} ask <name> "<brief>"`).
When it is healthy, `{cli} check {iid_intern}` confirms it.

{rules}"""

BUDGET_BRIEF = """You are the CEO of bojjifomo's guild, and you keep the purse. Time for the budget review ({when}).

Usage report (estimated from transcripts at API list prices):
{report}

Which model for which work:
{guide}

Do this:
1. Read the ideas above. Change a model only where the numbers back it:
   `{cli} model <name> <haiku|sonnet|opus>`. At most 3 changes. Never change your own model. Do not put a warden who
   changes code below sonnet. Opus seats are limited, so free one before you give one.
2. Chain upkeep (rounds, reviews) is bojjifomo's setting, not yours: suggest it, do not work around it.
3. Do not stop or skip work that is needed. The aim is the same results for less.
4. End with a short report: what you changed and why, the expected saving per week, and what bojjifomo could cut.

{rules}"""

ROUND_BRIEF = """You are the {role} of bojjifomo's guild. This is your {kind} round ({when}).
{takeover}
Situation report:
{situation}

Decide what needs action now and do it (see your instructions). Keep it proportionate: a healthy guild needs
no busywork, just a short report.

{rules}"""

INNOVATOR_BRIEF = """You are the innovator of bojjifomo's guild. Time for your review ({when}).

Situation report:
{situation}

Incidents in the last 30 days:
{incidents}

Read the programs' folders as needed. File at most 3 petitions with `{cli} propose` for the changes that would
prevent the most trouble or add the most value. Check `{cli} petitions` first and never repeat one.
End with a short list of what you filed and why.

{rules}"""


def now_text(ts=None):
    return datetime.fromtimestamp(ts or time.time()).strftime("%d %b %H:%M")


def clamp(key, value):
    lo, hi = LIMITS[key]
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return DEFAULTS[key]


class Chain:
    def __init__(self, office, cli="office"):
        self.office = office
        self.cli = cli
        self.lock = threading.RLock()
        data = store.load("chain.json", {})
        self.roles = {k: v for k, v in (data.get("roles") or {}).items() if k in ROLES}
        self.interns = data.get("interns") or []
        self.incidents = data.get("incidents") or []
        self.runs = data.get("runs") or {}          # sid -> what the run is for
        self.rounds = data.get("rounds") or {}      # kind -> heartbeat
        self.settings = dict(DEFAULTS, **(data.get("settings") or {}))
        self.dispatched = data.get("dispatched") or {"day": "", "n": 0, "warned": False}
        self.health = {}                            # intern id -> latest check, memory only
        self.fails = {}                             # intern id -> failed checks in a row
        self._next_check = 0
        self._checking = False

    def save(self):
        with self.lock:
            store.save("chain.json", {
                "roles": self.roles, "interns": self.interns, "incidents": self.incidents[-200:],
                "runs": self.runs, "rounds": self.rounds, "settings": self.settings, "dispatched": self.dispatched,
            })

    # ---- people ---------------------------------------------------------------------
    def member(self, staff_id):
        if not staff_id:
            return None
        found = self.office.team.find(staff_id)
        return None if not found or found.get("builtin") else found

    def holder(self, role):
        return self.member(self.roles.get(role))

    def appoint(self, role, staff_id):
        if role not in ROLES:
            raise ValueError("unknown role")
        if staff_id and not self.member(staff_id):
            raise LookupError("no such guild member")
        with self.lock:
            if staff_id:
                self.roles[role] = staff_id
            else:
                self.roles.pop(role, None)
            self.save()

    def recruit(self, role):
        """Hire the preset for a role (or reuse a member with that name) and appoint them."""
        preset = PRESETS[role]
        existing = self.office.team.find(preset["name"])
        if role == "innovator" and existing and not existing.get("builtin"):
            member = existing
        else:
            member, _ = self.office.team.hire(preset["name"], preset["role"], preset["instructions"],
                                              preset["division"], preset["model"])
        self.appoint(role, member["id"])
        return member

    def role_of(self, staff_id):
        names = [ROLE_NAMES[r] for r in ROLES if self.roles.get(r) == staff_id]
        guarded = [i["name"] for i in self.interns if i.get("warden") == staff_id]
        if guarded:
            names.append("Warden of " + ", ".join(guarded))
        return " · ".join(names)

    # ---- interns ------------------------------------------------------------------------
    def intern(self, key):
        key = str(key).strip().lower()
        for i in self.interns:
            if i["id"] == key or i["name"].lower() == key:
                return i
        return None

    def add_intern(self, name, project, launchd="", url="", fresh="", fresh_hours=24, warden=""):
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("name the intern")
        if not project or not os.path.isdir(project):
            raise ValueError("pick the program's folder")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("the health URL must start with http:// or https://")
        if fresh:
            fresh = str(fresh).strip().lstrip("/")
            target = (Path(project) / fresh).resolve()
            if ".." in Path(fresh).parts or Path(project).resolve() not in target.parents:
                raise ValueError("the freshness file must be inside the program's folder")
        if warden and not self.member(warden):
            raise LookupError("no such warden")
        if not (launchd or url or fresh):
            raise ValueError("give the intern at least one check: a launchd job, a health URL or a file")
        with self.lock:
            if self.intern(name):
                raise ValueError("there is already an intern called %s" % name)
            item = {"id": secrets.token_hex(4), "name": name, "project": project, "launchd": launchd,
                    "url": url[:300], "fresh": fresh[:200], "fresh_hours": max(0.1, min(720.0, float(fresh_hours or 24))),
                    "warden": warden, "added": time.time()}
            self.interns.append(item)
            self.save()
        return item

    def update_intern(self, iid, **fields):
        with self.lock:
            item = self.intern(iid)
            if not item:
                raise LookupError("no such intern")
            if "warden" in fields:
                if fields["warden"] and not self.member(fields["warden"]):
                    raise LookupError("no such warden")
                item["warden"] = fields["warden"]
            self.save()
            return item

    def remove_intern(self, iid):
        with self.lock:
            item = self.intern(iid)
            if not item:
                return False
            self.interns.remove(item)
            for inc in self.incidents:
                if inc["intern"] == item["id"] and inc["status"] != "resolved":
                    inc.update(status="resolved", resolved=time.time(), note="the intern left the guild")
            self.health.pop(item["id"], None)
            self.save()
            return True

    def checks_text(self, i):
        parts = []
        if i.get("launchd"):
            parts.append("launchd job %s" % i["launchd"])
        if i.get("url"):
            parts.append("GET %s answers below 400" % i["url"])
        if i.get("fresh"):
            parts.append("%s changed in the last %g h" % (i["fresh"], i["fresh_hours"]))
        return "; ".join(parts) or "none"

    # ---- health ------------------------------------------------------------------------------
    def check(self, i, jobs=None):
        reasons = []
        if not os.path.isdir(i["project"]):
            reasons.append("the folder %s is gone" % i["project"])
        if i.get("launchd"):
            if jobs is None:
                try:
                    jobs = self.office.familiars.jobs()
                except Exception:
                    jobs = []
            job = next((j for j in jobs if j["label"] == i["launchd"]), None)
            if not job:
                reasons.append("launchd job %s is not installed" % i["launchd"])
            elif not job["loaded"]:
                reasons.append("launchd job %s is not loaded" % i["launchd"])
            elif job["failed"]:
                reasons.append("launchd job %s last exited %s%s" % (
                    i["launchd"], job["exit"], (": " + job["error"][:200]) if job.get("error") else ""))
        if i.get("url"):
            try:
                req = urllib.request.Request(i["url"], headers={"User-Agent": "agent-office-health"})
                with urllib.request.urlopen(req, timeout=8) as res:
                    if res.status >= 400:
                        reasons.append("%s answered %d" % (i["url"], res.status))
            except urllib.error.HTTPError as e:
                reasons.append("%s answered %d" % (i["url"], e.code))
            except Exception as e:
                reasons.append("%s did not answer (%s)" % (i["url"], str(getattr(e, "reason", e))[:80]))
        if i.get("fresh"):
            target = Path(i["project"]) / i["fresh"]
            try:
                age = (time.time() - target.stat().st_mtime) / 3600
                if age > i["fresh_hours"]:
                    reasons.append("%s is %.1f h old (limit %g h)" % (i["fresh"], age, i["fresh_hours"]))
            except OSError:
                reasons.append("%s is missing" % i["fresh"])
        result = {"ok": not reasons, "reasons": reasons, "checked": time.time()}
        with self.lock:
            self.health[i["id"]] = result
        return result

    def check_now(self, key):
        i = self.intern(key)
        if not i:
            raise LookupError("no such intern")
        result = self.check(i)
        self._after_check(i, result)
        self.save()
        return result

    def check_all(self):
        try:
            jobs = self.office.familiars.jobs()
        except Exception:
            jobs = []
        for i in list(self.interns):
            try:
                self._after_check(i, self.check(i, jobs))
            except Exception:
                import traceback
                traceback.print_exc()
        self.save()

    def open_incident(self, intern_id):
        return next((x for x in self.incidents if x["intern"] == intern_id and x["status"] != "resolved"), None)

    def _after_check(self, i, result):
        with self.lock:
            inc = self.open_incident(i["id"])
            if result["ok"]:
                self.fails[i["id"]] = 0
                if inc and not self._active(inc):
                    self.resolve(inc["id"], "healthy again", by="check")
                return
            self.fails[i["id"]] = self.fails.get(i["id"], 0) + 1
            if inc or self.fails[i["id"]] < self.settings["fail_after"]:
                return
            inc = {"id": secrets.token_hex(3), "intern": i["id"], "name": i["name"], "opened": time.time(),
                   "reasons": result["reasons"], "level": "", "status": "open", "attempts": []}
            self.incidents.append(inc)
        self.office.ledger.add("guild", "%s is down: %s" % (i["name"], "; ".join(result["reasons"])[:200]), by="Chain")
        self.office.emit("incident.opened", self.public_incident(inc))
        if self.settings["auto"]:
            self.escalate(inc["id"], automatic=True)

    # ---- incidents -----------------------------------------------------------------------------
    def _active(self, inc):
        return any(not a.get("ended") for a in inc["attempts"])

    def ops_down(self):
        r = self.rounds.get("ops") or {}
        if r.get("running") and time.time() - r.get("started", 0) > self.settings["stuck_minutes"] * 60:
            return True
        return r.get("ok") is False

    def who_for(self, level, inc):
        if level == "warden":
            i = self.intern(inc["intern"])
            return self.member(i.get("warden")) if i else None
        if level == "ops":
            return None if self.ops_down() else self.holder("ops")
        if level == "ceo":
            return self.holder("ceo")
        return None

    def next_level(self, current, inc):
        order = list(LEVELS)
        start = order.index(current) + 1 if current in order else 0
        for level in order[start:]:
            if level == "you" or self.who_for(level, inc):
                return level
        return "you"

    def escalate(self, inc_id, automatic=False, note=""):
        """Move an incident one rung up and send the person there."""
        with self.lock:
            inc = next((x for x in self.incidents if x["id"] == inc_id), None)
            if not inc or inc["status"] == "resolved":
                raise LookupError("no open incident %s" % inc_id)
            if self._active(inc):
                raise RuntimeError("someone is already working on it")
            level = self.next_level(inc["level"], inc)
            inc["level"] = level
            inc["status"] = "needs-you" if level == "you" else "working"
            self.save()
        if note:
            self.office.ledger.add("guild", "Incident %s escalated: %s" % (inc["name"], note[:160]), by="Chain")
        if level == "you":
            self._to_you(inc)
            return {"level": "you"}
        if automatic and not self._may_dispatch():
            with self.lock:
                inc["level"], inc["status"] = "you", "needs-you"
                self.save()
            self._to_you(inc, "the guild reached today's limit of automatic dispatches")
            return {"level": "you"}
        return self._dispatch(inc, level, automatic)

    def _dispatch(self, inc, level, automatic):
        i = self.intern(inc["intern"]) or {"project": "", "name": inc["name"], "id": inc["intern"]}
        who = self.who_for(level, inc)
        if self.office.economy.snapshot().get("paused"):
            with self.lock:
                inc["level"], inc["status"] = "you", "needs-you"
                self.save()
            self._to_you(inc, "the treasury is empty, so nobody could be sent")
            return {"level": "you"}
        history = ""
        if inc["attempts"]:
            history = "\nWhat was tried so far:\n" + "\n".join(
                "- %s (%s): %s" % (a["who"], ROLE_NAMES.get(a["level"], a["level"]),
                                   " ".join((a.get("summary") or ("failed" if a.get("ok") is False else "no report")).split())[:600])
                for a in inc["attempts"]) + "\n"
        fields = dict(name=i["name"], iid=inc["id"], iid_intern=i["id"], opened=now_text(inc["opened"]),
                      project=i["project"], checks=self.checks_text(i) if "launchd" in i else "unknown",
                      reasons="; ".join(inc["reasons"]), history=history, cli=self.cli,
                      rules=RULES.format(cli=self.cli))
        if level == "warden":
            brief = WARDEN_BRIEF.format(**fields)
        else:
            tries = ", ".join(a["who"] for a in inc["attempts"]) or "no one could take it"
            brief = ESCALATE_BRIEF.format(role=ROLE_NAMES[level], tries=tries, **fields)
        try:
            result = self.office.team.ask(who["id"], brief, origin="chain", mode=MODE,
                                          add_dirs=[i["project"]] if i["project"] else [])
        except Exception as e:
            result = {"error": str(e)}
        if not result.get("sid"):
            with self.lock:
                inc["attempts"].append({"level": level, "who": who["name"], "started": time.time(),
                                        "ended": time.time(), "ok": False,
                                        "summary": "could not start: %s" % (result.get("error") or "held by the treasury")})
                self.save()
            return self.escalate(inc["id"], automatic=automatic)
        with self.lock:
            inc["attempts"].append({"level": level, "who": who["name"], "sid": result["sid"], "run": result.get("run", ""),
                                    "started": time.time()})
            self.runs[result["sid"]] = {"kind": "incident", "incident": inc["id"], "level": level}
            if automatic:
                self._count_dispatch()
            self.save()
        self.office.ledger.add("guild", "%s (%s) took incident %s: %s" % (
            who["name"], ROLE_NAMES[level], inc["id"], inc["name"]), by=who["name"], sid=result["sid"])
        self.office.emit("incident.escalated", dict(self.public_incident(inc), who=who["name"]))
        return {"level": level, "who": who["name"], "sid": result["sid"], "run": result.get("run")}

    def _to_you(self, inc, why=""):
        last = inc["attempts"][-1] if inc["attempts"] else {}
        body = why or (" ".join((last.get("summary") or "").split())[:180]) or "; ".join(inc["reasons"])[:180]
        self.office.notify("%s is down and needs your word" % inc["name"], body, tag="incident-" + inc["id"], url="/#chain")
        self.office.ledger.add("guild", "Incident %s (%s) needs bojjifomo: %s" % (inc["id"], inc["name"], body[:160]), by="Chain")
        self.office.emit("incident.escalated", dict(self.public_incident(inc), who="you"))

    def resolve(self, inc_id, note="", by="you"):
        with self.lock:
            inc = next((x for x in self.incidents if x["id"] == inc_id), None)
            if not inc:
                raise LookupError("no incident %s" % inc_id)
            if inc["status"] == "resolved":
                return inc
            inc.update(status="resolved", resolved=time.time(), note=str(note)[:300], resolved_by=by)
            self.fails[inc["intern"]] = 0
            self.save()
        self.office.ledger.add("guild", "%s is healthy again%s" % (inc["name"], (": " + note[:160]) if note else ""), by=by)
        self.office.emit("incident.resolved", self.public_incident(inc))
        return inc

    # ---- rounds ------------------------------------------------------------------------------------
    def situation(self):
        lines = ["Interns (programs):"]
        if not self.interns:
            lines.append("- none registered yet")
        for i in self.interns:
            h = self.health.get(i["id"])
            state = "unchecked" if not h else ("ok" if h["ok"] else "DOWN: " + "; ".join(h["reasons"]))
            warden = self.member(i.get("warden"))
            lines.append("- %s [%s] warden %s, folder %s, checks: %s" % (
                i["name"], state, warden["name"] if warden else "none", i["project"], self.checks_text(i)))
        open_ = [x for x in self.incidents if x["status"] != "resolved"]
        lines.append("Open incidents:" if open_ else "Open incidents: none")
        for x in open_:
            tried = ", ".join("%s%s" % (a["who"], " (working)" if not a.get("ended") else "") for a in x["attempts"])
            lines.append("- %s %s: %s; at %s; tried: %s" % (x["id"], x["name"], "; ".join(x["reasons"])[:200],
                                                            ROLE_NAMES.get(x["level"], "nobody yet"), tried or "nobody"))
        try:
            lines.append("Petitions waiting for bojjifomo: %d" % self.office.petitions.pending_count())
        except Exception:
            pass
        try:
            gold = self.office.economy.snapshot()
            if gold.get("budget"):
                lines.append("Treasury: $%.2f spent of $%.2f today%s" % (
                    gold.get("spent", 0), gold["budget"], " (EMPTY: new quests wait)" if gold.get("paused") else ""))
        except Exception:
            pass
        try:
            bad = [j for j in self.office.familiars.jobs() if j["failed"] and not j["ours"]]
            if bad:
                lines.append("Familiars (launchd) with errors: " + "; ".join(
                    "%s exit %s %s" % (j["label"], j["exit"], (j.get("error") or "")[:80]) for j in bad[:6]))
        except Exception:
            pass
        try:
            recent = self.office.ledger.recent(10)
            if recent:
                lines.append("Recent guild log:")
                lines += ["- %s %s" % (e.get("kind", ""), " ".join(str(e.get("text", "")).split())[:140]) for e in recent]
        except Exception:
            pass
        return "\n".join(lines)

    def start_round(self, kind, takeover="", automatic=False):
        """kind: ops, innovator, or ceo (a takeover or acting-ops round)."""
        role = "ops" if kind == "ops" else ("innovator" if kind == "innovator" else "ceo")  # budget is the CEO's
        who = self.holder(role)
        if not who:
            raise LookupError("nobody is appointed as %s" % ROLE_NAMES[role])
        with self.lock:
            r = self.rounds.get(kind) or {}
            if r.get("running") and r.get("sid") and self.office.runner.active(r["sid"]):
                raise RuntimeError("%s is already on a round" % who["name"])
        if automatic and not self._may_dispatch():
            raise RuntimeError("today's limit of automatic dispatches is reached")
        if self.office.economy.snapshot().get("paused"):
            raise RuntimeError("the treasury is empty")
        when = now_text()
        dirs = sorted({i["project"] for i in self.interns if os.path.isdir(i["project"])})
        if kind == "budget":
            import report
            data = report.build(self.office, 7)
            brief = BUDGET_BRIEF.format(when=when, report=report.text(data), guide=report.MODEL_GUIDE, cli=self.cli,
                                        rules=RULES.format(cli=self.cli))
        elif kind == "innovator":
            month = time.time() - 30 * 86400
            recent = [x for x in self.incidents if x["opened"] >= month]
            incidents = "\n".join("- %s %s: %s (%s)" % (now_text(x["opened"]), x["name"], "; ".join(x["reasons"])[:160],
                                                        x["status"]) for x in recent) or "- none"
            brief = INNOVATOR_BRIEF.format(when=when, situation=self.situation(), incidents=incidents,
                                           cli=self.cli, rules=RULES.format(cli=self.cli))
        else:
            label = {"ops": "operations", "ceo": "CEO"}[kind]
            brief = ROUND_BRIEF.format(role=ROLE_NAMES[role], kind=label, when=when, takeover=takeover,
                                       situation=self.situation(), rules=RULES.format(cli=self.cli))
        result = self.office.team.ask(who["id"], brief, origin="chain", mode=MODE, add_dirs=dirs)
        if not result.get("sid"):
            raise RuntimeError("the treasury held the round back")
        with self.lock:
            self.rounds[kind] = dict(self.rounds.get(kind) or {}, running=True, started=time.time(), sid=result["sid"],
                                     run=result.get("run", ""), who=who["name"], takeover=bool(takeover))
            self.runs[result["sid"]] = {"kind": "round", "round": kind}
            if automatic:
                self._count_dispatch()
            self.save()
        self.office.ledger.add("guild", "%s set out on the %s round" % (who["name"], {"ops": "operations", "budget": "budget review"}.get(kind, ROLE_NAMES[role])),
                               by=who["name"], sid=result["sid"])
        return {"who": who["name"], "sid": result["sid"], "run": result.get("run")}

    def guarded(self):
        """True once today's spend reaches guard_pct of the daily budget: only incidents keep moving."""
        try:
            gold = self.office.economy.snapshot()
        except Exception:
            return False
        if not gold.get("budget"):
            return False
        used = gold.get("spent", 0) / gold["budget"] * 100
        on = used >= self.settings["guard_pct"]
        with self.lock:
            day = self._today()
            if on and self.dispatched.get("guard_day") != day:
                self.dispatched["guard_day"] = day
                self.save()
                self.office.notify("The guild is rationing", "%d%% of today's budget is spent. Rounds and reviews wait until "
                                   "tomorrow; incidents still get through." % used, tag="chain-guard", url="/#usage")
                self.office.ledger.add("guild", "Rationing: %d%% of today's budget is spent, rounds wait" % used, by="Chain")
        return on

    def _ceo_takeover(self, why):
        ceo = self.holder("ceo")
        self.office.notify("The Operations Manager is down", why[:180], tag="ops-down", url="/#chain")
        self.office.ledger.add("guild", "The Operations Manager is down: %s" % why[:160], by="Chain")
        self.office.emit("ops.down", {"why": why})
        if not ceo or not self.settings["auto"]:
            return
        try:
            self.start_round("ceo", takeover="The Operations Manager failed (%s). Take over this round, and get "
                                             "them working again." % why, automatic=True)
        except Exception as e:
            self.office.ledger.add("guild", "The CEO could not take over: %s" % str(e)[:160], by="Chain")

    # ---- the clock -----------------------------------------------------------------------------------
    def _today(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _may_dispatch(self):
        with self.lock:
            if self.dispatched.get("day") != self._today():
                self.dispatched = {"day": self._today(), "n": 0, "warned": False}
            if self.dispatched["n"] < self.settings["max_auto_per_day"]:
                return True
            if not self.dispatched.get("warned"):
                self.dispatched["warned"] = True
                self.office.notify("The guild hit today's dispatch limit",
                                   "%d automatic dispatches today. Raise it in ⚜ Chain if you want more." % self.dispatched["n"],
                                   tag="chain-limit", url="/#chain")
            return False

    def _count_dispatch(self):
        if self.dispatched.get("day") != self._today():
            self.dispatched = {"day": self._today(), "n": 0, "warned": False}
        self.dispatched["n"] += 1

    def tick(self):
        """Called from the office's watch loop every few seconds."""
        now = time.time()
        if self.interns and not self._checking and now >= self._next_check:
            self._next_check = now + self.settings["check_minutes"] * 60
            self._checking = True

            def run():
                try:
                    self.check_all()
                finally:
                    self._checking = False

            threading.Thread(target=run, daemon=True, name="chain-check").start()
        self._unstick(now)
        if not self.settings["auto"] or self.guarded():
            return
        ops = self.rounds.get("ops") or {}
        if ops.get("running") and now - ops.get("started", 0) > self.settings["stuck_minutes"] * 60 and not ops.get("stuck"):
            with self.lock:
                ops.update(stuck=True, ok=False)
                self.save()
            try:
                if ops.get("run"):
                    self.office.runner.stop(ops["run"])
            except Exception:
                pass
            self._ceo_takeover("their round has been running for over %d minutes" % self.settings["stuck_minutes"])
        due_kind = "ops" if self.holder("ops") else ("ceo" if self.holder("ceo") else None)
        if due_kind:
            r = self.rounds.get(due_kind) or {}
            if not r.get("running") and now - r.get("started", 0) >= self.settings["ops_every_hours"] * 3600:
                try:
                    self.start_round(due_kind, takeover="" if due_kind == "ops" else
                                     "No Operations Manager is appointed, so you are acting as one.", automatic=True)
                except Exception as e:
                    with self.lock:
                        self.rounds[due_kind] = dict(r, started=now, error=str(e)[:200])
                        self.save()
        hours = self.settings["review_hours"]
        if hours and self.holder("ceo"):
            r = self.rounds.get("budget") or {}
            if not r.get("running") and now - r.get("started", 0) >= hours * 3600:
                try:
                    self.start_round("budget", automatic=True)
                except Exception as e:
                    with self.lock:
                        self.rounds["budget"] = dict(r, started=now, error=str(e)[:200])
                        self.save()
        if self.holder("innovator"):
            r = self.rounds.get("innovator") or {}
            if not r.get("running") and now - r.get("started", 0) >= self.settings["innovator_every_days"] * 86400:
                try:
                    self.start_round("innovator", automatic=True)
                except Exception as e:
                    with self.lock:
                        self.rounds["innovator"] = dict(r, started=now, error=str(e)[:200])
                        self.save()

    def _unstick(self, now):
        """An incident attempt still running after stuck_minutes is stopped and sent up a rung."""
        limit = self.settings["stuck_minutes"] * 60
        stuck = []
        with self.lock:
            for inc in self.incidents:
                if inc["status"] == "resolved":
                    continue
                for a in inc["attempts"]:
                    if not a.get("ended") and now - a.get("started", now) > limit:
                        a.update(ended=now, ok=False, summary="stuck for over %d minutes, stopped" % self.settings["stuck_minutes"])
                        self.runs.pop(a.get("sid"), None)
                        stuck.append((inc, a))
            if stuck:
                self.save()
        for inc, a in stuck:
            try:
                if a.get("run"):
                    self.office.runner.stop(a["run"])
            except Exception:
                pass
            if self.settings["auto"]:
                try:
                    self.escalate(inc["id"], automatic=True)
                except Exception:
                    import traceback
                    traceback.print_exc()

    def on_run(self, run):
        """A Claude run ended. If the chain sent it, record the outcome and decide what comes next."""
        with self.lock:
            entry = self.runs.pop(run.sid, None)
            if not entry:
                return
            summary = " ".join((run.final or "").split())[:1500]
            ok = bool(run.ok)
            if entry["kind"] == "round":
                kind = entry["round"]
                r = self.rounds.setdefault(kind, {})
                r.update(running=False, ended=time.time(), ok=ok, summary=summary[:600])
                if ok:
                    r.pop("stuck", None)
                    r.pop("error", None)
                self.save()
                failed_ops = kind == "ops" and not ok and not r.get("stuck")
            else:
                inc = next((x for x in self.incidents if x["id"] == entry["incident"]), None)
                if not inc:
                    self.save()
                    return
                for a in inc["attempts"]:
                    if a.get("sid") == run.sid:
                        a.update(ended=time.time(), ok=ok, summary=summary)
                self.save()
        if entry["kind"] == "round":
            if failed_ops:
                self._ceo_takeover("their round ended with an error: %s" % (summary[:200] or "no report"))
            return
        if inc["status"] == "resolved":
            return
        i = self.intern(inc["intern"])
        if not i:
            return
        result = self.check(i)
        if result["ok"]:
            self.resolve(inc["id"], "fixed by %s" % next((a["who"] for a in inc["attempts"] if a.get("sid") == run.sid), "the guild"),
                         by=next((a["who"] for a in inc["attempts"] if a.get("sid") == run.sid), "guild"))
        else:
            with self.lock:
                inc["reasons"] = result["reasons"]
            if self.settings["auto"]:
                try:
                    self.escalate(inc["id"], automatic=True)
                except Exception:
                    import traceback
                    traceback.print_exc()

    # ---- settings and views --------------------------------------------------------------------------
    def configure(self, changes):
        with self.lock:
            for key, value in changes.items():
                if key == "auto":
                    self.settings["auto"] = bool(value)
                elif key in LIMITS:
                    self.settings[key] = clamp(key, value)
            self._next_check = 0
            self.save()
        return self.settings

    def public_incident(self, inc):
        return {k: inc.get(k) for k in ("id", "intern", "name", "opened", "reasons", "level", "status", "attempts",
                                         "resolved", "note", "resolved_by")}

    def public(self):
        with self.lock:
            people = {}
            for role in ROLES:
                m = self.holder(role)
                r = self.rounds.get("ceo" if role == "ceo" else role) or {}
                people[role] = {
                    "title": ROLE_NAMES[role], "staff": m["id"] if m else "", "name": m["name"] if m else "",
                    "model": (m.get("model") or "") if m else "", "seed": m["seed"] if m else "",
                    "kind": m["kind"] if m else "", "round": {k: r.get(k) for k in ("running", "started", "ended", "ok", "summary", "error", "sid", "takeover")},
                }
            people["ops"]["down"] = self.ops_down()
            review = self.rounds.get("budget") or {}
            people["ceo"]["review"] = {k: review.get(k) for k in ("running", "started", "ended", "ok", "summary", "error", "sid")}
            interns = []
            for i in self.interns:
                w = self.member(i.get("warden"))
                inc = self.open_incident(i["id"])
                interns.append(dict(i, warden_name=w["name"] if w else "", health=self.health.get(i["id"]),
                                    checks=self.checks_text(i), incident=self.public_incident(inc) if inc else None))
            return {
                "people": people, "interns": interns, "settings": self.settings,
                "incidents": [self.public_incident(x) for x in reversed(self.incidents[-30:])],
                "open": sum(1 for x in self.incidents if x["status"] != "resolved"),
                "needs_you": sum(1 for x in self.incidents if x["status"] == "needs-you"),
                "dispatched": self.dispatched, "guard": self.guard_view(),
            }

    def guard_view(self):
        gold = self.office.economy.snapshot()
        used = (gold.get("spent", 0) / gold["budget"] * 100) if gold.get("budget") else None
        return {"metered": bool(gold.get("budget")), "used": None if used is None else round(used, 1),
                "pct": self.settings["guard_pct"], "on": bool(used is not None and used >= self.settings["guard_pct"])}

    def badge(self):
        with self.lock:
            return {"open": sum(1 for x in self.incidents if x["status"] != "resolved"),
                    "needs_you": sum(1 for x in self.incidents if x["status"] == "needs-you"),
                    "ops_down": self.ops_down() if self.holder("ops") else False}
