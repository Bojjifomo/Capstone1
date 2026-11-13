"""The chain of command.

Your programs are the guild's interns. Each intern has a warden (a recruited specialist) who looks after it.
Above them: the innovator files petitions, the operations manager (the orchestrator) makes rounds and sends
people where they are needed, and the CEO steps in when the operations manager fails or an incident gets
past them. Anything the CEO cannot fix comes to bojjifomo's phone.

Escalation for a broken intern:  warden -> operations manager -> CEO -> you.
Health checks are fixed and run here, never as shell text: a launchd job's state, an HTTP GET, a file's age.
"""
import os
import json
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import handoff
import roster
import store

ROLES = ("ceo", "ops", "innovator", "reviewer")
ROLE_NAMES = {"ceo": "CEO", "ops": "Operations Manager", "innovator": "Innovator", "reviewer": "Reviewer",
              "warden": "Warden", "you": "you"}
LEVELS = ("warden", "ops", "ceo", "you")
DEFAULTS = {
    "auto": True,                 # dispatch and rounds on their own
    "check_minutes": 5,           # how often interns are checked
    "fail_after": 2,              # failed checks in a row before an incident opens
    "ops_every_hours": 6,         # the operations manager's rounds
    "innovator_every_days": 7,    # the innovator's review
    "stuck_minutes": 90,          # a round still running after this counts as failed
    "max_auto_per_day": 12,       # automatic dispatches (incidents and rounds) per day
    "auto_review": True,          # the Reviewer reads every pull request a guild run opens
    "auto_review_prs": True,      # the Reviewer reads every open pull request, once per head commit
    "pr_sweep_minutes": 45,       # how often open pull requests are looked for
    "review_hours": 24,           # the CEO's budget review (0 = off)
    "guard_pct": 80,              # at this share of the daily budget, automatic rounds stop
    "opus_seats": 2,              # how many specialists may run on Opus at once
    "allowed_models": ["haiku", "sonnet", "opus"],   # the models offered everywhere; at least one
}
MODEL_CHOICES = ("haiku", "sonnet", "opus")
LIMITS = {"check_minutes": (1, 240), "fail_after": (1, 10), "ops_every_hours": (1, 72),
          "innovator_every_days": (1, 60), "stuck_minutes": (15, 600), "max_auto_per_day": (0, 100),
          "pr_sweep_minutes": (10, 720), "review_hours": (0, 168), "guard_pct": (10, 100), "opus_seats": (0, 20)}
PR_REPO = "Bojjifomo/Capstone1"
PR_SWEEP_MAX = 3          # reviews started per sweep, to protect the budget
MODE = "auto"  # Claude Code's guarded auto mode: no prompts, risky actions still blocked

PRESETS = roster.PRESETS   # who gets recruited into each role; the text lives in roster.py

REVIEW_BRIEF = """You are the Reviewer of bojjifomo's guild. {by} just opened this pull request:

{url}

{head}Read it: `gh pr view {url}` and `gh pr diff {url}` (or open the page). Run the project's tests on the branch if you can.
Check what a careful reviewer checks: does it do what it says, are there tests, could it leak a secret or a key, does it
weaken a security check or widen a network bind, can it spend RPC quota, money or tokens in a loop, does it touch
wallets, is anything deleted that should not be.

You review; you never merge, approve, push or comment on GitHub (only Aurelian merges, under his own merge rules, and only what you mark safe). Your answer comes back to the Guild Hall.
Your verdict is the RESULT line:
RESULT: done - safe to merge (one sentence why)
RESULT: blocked - do not merge yet: <the blocking problems, most important first>
RESULT: needs you - <a decision only bojjifomo can make, such as a new key, a cost, a design choice>

{rules}"""

RULES = """Rules for every guild run:
- Work in the folders named here. Read what you need, change only what the fix needs.
- Code changes go on a new git branch with a pull request. Never merge to main, never force-push.
- Never delete data, never read or print secrets (.env, keys, tokens), never move funds or touch wallets.
- `{cli} chain` shows every intern and incident. `{cli} check <intern>` checks one again right now.
- End with a short report: what was wrong, what you did, whether it is healthy now, what is left."""

# Only the CEO (Aurelian) gets this exception; every other run keeps RULES as written.
CEO_MERGE_LINE = "Never merge to main (only Aurelian, under the merge rules in his instructions), never force-push."


def rules_for(role, cli):
    text = RULES.format(cli=cli)
    if role == "ceo":
        text = text.replace("Never merge to main, never force-push.", CEO_MERGE_LINE)
    return text

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
        self.reviews = data.get("reviews") or []    # pull requests the Reviewer read
        self.pr_seen = data.get("pr_seen") or {}    # "number:head sha" -> when it was reviewed (restarts do not re-review)
        self._next_sweep = 0
        self._sweeping = False
        self.health = {}                           # intern id -> latest check, memory only
        self.fails = {}                             # intern id -> failed checks in a row
        self._next_check = 0
        self._checking = False

    def forget(self, staff_id):
        """Someone left the guild: free their seat and their intern."""
        with self.lock:
            for role in [r for r, sid in self.roles.items() if sid == staff_id]:
                self.roles.pop(role)
            for intern in self.interns:
                if intern.get("warden") == staff_id:
                    intern["warden"] = ""
            self.save()

    def allowed(self):
        """The models offered in every picker and accepted by the server. Never empty."""
        got = [m for m in MODEL_CHOICES if m in (self.settings.get("allowed_models") or [])]
        return got or list(MODEL_CHOICES)

    def pick_model(self, wanted):
        """The wanted model if it is offered, else the nearest one that is (Sonnet first, then Haiku, then Opus)."""
        allowed = self.allowed()
        if wanted in allowed:
            return wanted
        return next(m for m in ("sonnet", "haiku", "opus") if m in allowed)

    def save(self):
        with self.lock:
            store.save("chain.json", {
                "roles": self.roles, "interns": self.interns, "incidents": self.incidents[-200:],
                "runs": self.runs, "rounds": self.rounds, "settings": self.settings, "dispatched": self.dispatched,
                "reviews": self.reviews[-50:],
                "pr_seen": dict(sorted(self.pr_seen.items(), key=lambda kv: kv[1])[-300:]),
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
        """Appoint the preset for a role. A guild member with that name is reused, never overwritten."""
        preset = PRESETS[role]
        existing = self.office.team.find(preset["name"])
        if existing and not existing.get("builtin"):
            member = existing
        else:
            member, _ = self.office.team.hire(preset["name"], preset["role"], preset["instructions"],
                                              preset["division"], self.pick_model(preset["model"]))
        self.appoint(role, member["id"])
        return member

    def recruit_team(self):
        """The standing team: CEO, operations manager, innovator and two wardens. Fills only what is missing."""
        done = []
        for role in roster.ORDER:
            if self.holder(role):
                done.append({"role": ROLE_NAMES[role], "name": self.holder(role)["name"], "status": "already appointed"})
                continue
            existing = self.office.team.find(PRESETS[role]["name"])
            try:
                member = self.recruit(role)
            except RuntimeError as e:
                done.append({"role": ROLE_NAMES[role], "name": PRESETS[role]["name"], "status": "not recruited: %s" % e})
                continue
            done.append({"role": ROLE_NAMES[role], "name": member["name"],
                         "status": "appointed (already in the guild)" if existing else "recruited"})
        for key, preset in roster.WARDENS.items():
            existing = self.office.team.find(preset["name"])
            if existing and not existing.get("builtin"):
                done.append({"role": "Warden", "name": existing["name"], "status": "already in the guild"})
                continue
            try:
                member, _ = self.office.team.hire(preset["name"], preset["role"], preset["instructions"],
                                                  preset["division"], self.pick_model(preset["model"]))
            except RuntimeError as e:
                done.append({"role": "Warden", "name": preset["name"], "status": "not recruited: %s" % e})
                continue
            done.append({"role": "Warden", "name": member["name"], "status": "recruited"})
        return done

    def known_interns(self):
        """The programs in roster.KNOWN_INTERNS that exist in one of bojjifomo's project folders."""
        try:
            projects = self.office.projects()
        except Exception:
            projects = []
        try:
            labels = {j["label"]: j["id"] for j in self.office.familiars.jobs()}
        except Exception:
            labels = {}
        # Sessions often run inside a sub-folder (Capstone1/agent-office), so look a few folders up as well.
        home = os.path.expanduser("~")
        roots = []
        for p in projects:
            path, ids = p["path"], p["id"]
            for _ in range(4):
                if path not in [r["path"] for r in roots]:
                    roots.append({"path": path, "id": ids if path == p["path"] else "", "label": path.replace(home, "~", 1)})
                parent = os.path.dirname(path)
                if parent == path or parent in ("/", home, os.path.dirname(home)):
                    break
                path = parent
        exact = {p["path"]: p for p in projects}
        out = []
        for k in roster.KNOWN_INTERNS:
            fresh = k.get("fresh", "")
            project = None
            # a program that runs from an installed copy is watched there (launchd cannot run from an external disk)
            installed = os.path.expanduser(k["installed"]) if k.get("installed") else ""
            if installed and os.path.isfile(os.path.join(installed, "bot.py")):
                project = {"path": installed, "id": "", "label": installed.replace(home, "~", 1)}
                fresh = k.get("installed_fresh", fresh)
            if not project:
                project = next((r for r in roots if os.path.isdir(os.path.join(r["path"], k["folder"]))), None)
            if not project:
                continue
            if project["path"] in exact:
                project = dict(project, id=exact[project["path"]]["id"])
            warden = self.office.team.find(k["warden"])
            out.append({
                "name": k["name"], "project": project["id"], "project_path": project["path"],
                "project_label": project["label"], "url": k.get("url", ""), "fresh": fresh,
                "fresh_hours": k.get("fresh_hours", 24),
                "launchd": k.get("launchd", "") if k.get("launchd") in labels else "",
                "familiar": labels.get(k.get("launchd", ""), ""),
                "warden": warden["id"] if warden and not warden.get("builtin") else "", "warden_name": k["warden"],
                "diagnose": k.get("diagnose") or [], "diagnose_hint": k.get("diagnose_hint", ""),
                "hint": k.get("hint", ""), "added": bool(self.intern(k["name"])),
            })
        return out

    def setup_interns(self):
        """Add every known program that is healthy right now. The rest are listed with what to do first."""
        done = []
        for k in self.known_interns():
            if k["added"]:
                done.append({"name": k["name"], "status": "already an intern"})
                continue
            probe = {"id": "probe-" + secrets.token_hex(3), "name": k["name"], "project": k["project_path"],
                     "launchd": k["launchd"], "url": k["url"], "fresh": k["fresh"], "fresh_hours": k["fresh_hours"],
                     "diagnose": k["diagnose"], "diagnose_hint": k["diagnose_hint"]}
            result = self.check(probe)
            with self.lock:
                self.health.pop(probe["id"], None)
            if not result["ok"]:
                done.append({"name": k["name"], "status": "skipped: " + "; ".join(result["reasons"]) + ". " + k["hint"]})
                continue
            item = self.add_intern(k["name"], k["project_path"], launchd=k["launchd"], url=k["url"], fresh=k["fresh"],
                                   fresh_hours=k["fresh_hours"], warden=k["warden"],
                                   diagnose=k["diagnose"], diagnose_hint=k["diagnose_hint"])
            self.check_now(item["id"])
            done.append({"name": k["name"], "status": "added, warden %s" % (k["warden_name"] if k["warden"] else "none yet")})
        return done

    # ---- the QA checkpoint: the Reviewer reads every pull request the guild opens ----------------------------
    def maybe_review(self, run, author=None):
        if not self.settings.get("auto_review") or getattr(run, "origin", "") == "review" or not run.ok:
            return []
        reviewer = self.holder("reviewer")
        if not reviewer or (author and author["id"] == reviewer["id"]):
            return []
        by = author["name"] if author else "a quest"
        started = []
        for url in handoff.pr_urls(run.final)[:2]:
            try:
                r = self.request_review(url, by=by, cwd=getattr(run, "cwd", ""), automatic=True)
            except Exception as e:
                self.office.ledger.add("guild", "No review for %s: %s" % (url, str(e)[:120]), by="Chain")
                continue
            if r:
                started.append(r)
        return started

    def request_review(self, url, by="bojjifomo", cwd="", automatic=False, sha=""):
        if not handoff.PR_URL.fullmatch(str(url or "").strip()):
            raise ValueError("give a GitHub pull request link, like https://github.com/owner/repo/pull/12")
        url = url.strip()
        reviewer = self.holder("reviewer")
        if not reviewer:
            raise LookupError("appoint a Reviewer in the Chain panel first")
        with self.lock:
            recent = next((x for x in self.reviews if x["url"] == url and (not x.get("ended") or (
                not sha and time.time() - x["started"] < 86400))), None)
        if recent and automatic:
            return None
        if recent and not recent.get("ended"):
            raise RuntimeError("%s is already reviewing it" % recent["reviewer"])
        if automatic and not self._may_dispatch():
            return None
        if self.office.economy.snapshot().get("paused"):
            if automatic:
                return None
            raise RuntimeError("the treasury is empty")
        sha = str(sha or "").strip()[:40]
        head = "Review the head commit %s (verify with `gh pr view %s --json headRefOid`).\n\n" % (sha[:7], url) if sha else ""
        brief = REVIEW_BRIEF.format(url=url, by=by, head=head, rules=RULES.format(cli=self.cli))
        dirs = [cwd] if cwd and os.path.isdir(cwd) else []
        result = self.office.team.ask(reviewer["id"], brief, origin="review", mode=MODE, add_dirs=dirs)
        if not result.get("sid"):
            return None
        with self.lock:
            self.reviews.append({"url": url, "by": by, "reviewer": reviewer["name"], "sid": result["sid"],
                                 "started": time.time(), "ended": None, "outcome": "", "note": "",
                                 "pr": url.rsplit("/", 1)[-1], "sha": sha})
            if sha:
                self.pr_seen["%s:%s" % (url.rsplit("/", 1)[-1], sha)] = time.time()
            self.reviews = self.reviews[-50:]
            self.runs[result["sid"]] = {"kind": "review", "url": url}
            if automatic:
                self._count_dispatch()
            self.save()
        self.office.ledger.add("guild", "%s is reviewing %s (opened by %s)" % (reviewer["name"], url, by),
                               by=reviewer["name"], sid=result["sid"])
        return {"who": reviewer["name"], "sid": result["sid"], "run": result.get("run"), "url": url}

    # ---- the sweep: the Reviewer reads every open pull request, once per head commit -----------------------
    def head_sha(self, url):
        """The PR's current head commit, or "" when gh cannot say."""
        try:
            if not shutil.which("gh"):
                return ""
            proc = subprocess.run(["gh", "pr", "view", url, "--json", "headRefOid"], capture_output=True, text=True, timeout=20)
            return str(json.loads(proc.stdout).get("headRefOid") or "") if proc.returncode == 0 else ""
        except Exception:
            return ""

    def open_prs(self):
        """Open pull requests from gh; [] (quietly) when gh is missing, slow or fails."""
        try:
            if not shutil.which("gh"):
                return []
            proc = subprocess.run(["gh", "pr", "list", "--repo", PR_REPO, "--state", "open", "--json",
                                   "number,url,headRefOid,isDraft,title"], capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or "gh failed").strip()[:120])
            data = json.loads(proc.stdout or "[]")
            return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []
        except Exception as e:
            print("pr sweep: gh unavailable (%s)" % str(e)[:120])
            return []

    def sweep_prs(self):
        """Start a review for each non-draft open PR whose head commit has not been reviewed. Returns the reviews started."""
        if not self.settings.get("auto_review_prs") or not self.settings.get("auto_review") or not self.holder("reviewer"):
            return []
        started = []
        for pr in sorted(self.open_prs(), key=lambda p: p.get("number") or 0):
            if len(started) >= PR_SWEEP_MAX:
                break
            url, sha, number = str(pr.get("url") or ""), str(pr.get("headRefOid") or ""), pr.get("number")
            if pr.get("isDraft") or not sha or not number or not handoff.PR_URL.fullmatch(url):
                continue
            with self.lock:
                if "%s:%s" % (number, sha) in self.pr_seen:
                    continue
                if any(x["url"] == url and (not x.get("ended") or (not x.get("sha") and time.time() - x["started"] < 86400))
                       for x in self.reviews):
                    continue   # one is in flight, or a guild run's own review of this PR just happened
            try:
                r = self.request_review(url, by="the open-PR sweep", automatic=True, sha=sha)
            except Exception as e:
                self.office.ledger.add("guild", "No review for %s: %s" % (url, str(e)[:120]), by="Chain")
                continue
            if not r:
                break   # treasury empty or the daily dispatch limit: try again next sweep
            started.append(r)
        return started

    # ---- START HERE FIRST: the setup, checked from the real state -------------------------------------------
    def start_steps(self):
        cfg = getattr(self.office, "cfg", {}) or {}
        try:
            budget = float(cfg.get("daily_budget_usd") or 0)
        except (TypeError, ValueError):
            budget = 0
        pusher = getattr(self.office, "pusher", None)
        ops = self.rounds.get("ops") or {}
        guarded = [i for i in self.interns if self.member(i.get("warden"))]
        healthy = [i for i in self.interns if (self.health.get(i["id"]) or {}).get("ok")]
        steps = [
            ("ravens", "Push notifications on this phone", bool(pusher and pusher.subscribed),
             "Ketuk 🪶 di kanan atas → Send ravens to this phone (harus lewat https tailscale)."),
            ("budget", "A daily budget", budget > 0,
             "Isi \"daily_budget_usd\" di ~/Projects/agent-office/config.json, lalu restart."),
            ("team", "CEO, Operations Manager, Innovator and Reviewer appointed", all(self.holder(r) for r in ROLES),
             "Pencet Recruit the standing team di bawah, atau office setup di Mac."),
            ("interns", "At least one program watched", bool(self.interns),
             "Take on an intern di bawah, atau Add every known program."),
            ("wardens", "Every program has a warden", bool(self.interns) and len(guarded) == len(self.interns),
             "Pilih Warden di kartu tiap intern."),
            ("healthy", "Every program checked healthy once", bool(self.interns) and len(healthy) == len(self.interns),
             "Check now di tiap intern; yang merah dibenerin dulu sebelum Autopilot."),
            ("round", "A first operations round that finished well", ops.get("ok") is True,
             "Run a round now di baris Operations Manager, lalu baca laporannya."),
            ("autopilot", "Autopilot on", bool(self.settings.get("auto")),
             "Nyalakan di bagian Autopilot, setelah langkah di atas beres."),
        ]
        return [{"id": k, "label": label, "done": bool(done), "how": how} for k, label, done, how in steps]

    # ---- scorecard: what to track after launch -----------------------------------------------------------------
    def scorecard(self, days=7):
        since = time.time() - days * 86400
        people = []
        progress = getattr(self.office, "progress", None)
        for s in self.office.team.staff if progress else []:
            hist = [h for h in ((progress.actors.get(s["id"]) or {}).get("history") or [])
                    if h.get("kind") == "quest" and h.get("ts", 0) >= since]
            if not hist:
                continue
            costs = [h["cost"] for h in hist if h.get("cost") is not None]
            secs = [h["seconds"] for h in hist if h.get("seconds")]
            outcomes = {}
            for h in hist:
                o = h.get("outcome") or ("done" if h.get("ok") else "failed")
                outcomes[o] = outcomes.get(o, 0) + 1
            people.append({
                "name": s["name"], "model": s.get("model") or "default", "seat": self.role_of(s["id"]), "runs": len(hist),
                "done_pct": round(100 * outcomes.get("done", 0) / len(hist)),
                "avg_cost": round(sum(costs) / len(costs), 4) if costs else None,
                "avg_minutes": round(sum(secs) / len(secs) / 60, 1) if secs else None, "outcomes": outcomes,
            })
        people.sort(key=lambda p: -p["runs"])
        incs = [x for x in self.incidents if x.get("opened", 0) >= since]
        fixed = [x for x in incs if x["status"] == "resolved" and x.get("resolved")]
        minutes = sorted((x["resolved"] - x["opened"]) / 60 for x in fixed)
        by_level = {}
        for x in fixed:
            level = (x["attempts"][-1]["level"] if x["attempts"] else "check") if x.get("resolved_by") != "check" else "check"
            by_level[level] = by_level.get(level, 0) + 1
        reviews = [r for r in self.reviews if r.get("started", 0) >= since]
        return {
            "days": days, "people": people,
            "incidents": {"opened": len(incs), "fixed": len(fixed), "open": sum(1 for x in incs if x["status"] != "resolved"),
                          "median_minutes": round(minutes[len(minutes) // 2], 1) if minutes else None,
                          "fixed_by": by_level,
                          "reached_you": sum(1 for x in incs if x.get("level") == "you")},
            "reviews": {"count": len(reviews), "safe": sum(1 for r in reviews if r.get("outcome") == "done"),
                        "blocked": sum(1 for r in reviews if r.get("outcome") == "blocked")},
        }

    def scorecard_text(self, days=7):
        sc = self.scorecard(days)
        lines = ["Scorecard, last %d days:" % days]
        for p in sc["people"]:
            lines.append("- %s (%s%s): %d runs, %d%% done, avg %s, %s min%s" % (
                p["name"], p["model"], ", " + p["seat"] if p["seat"] else "", p["runs"], p["done_pct"],
                "$%.3f" % p["avg_cost"] if p["avg_cost"] is not None else "cost unknown",
                p["avg_minutes"] if p["avg_minutes"] is not None else "?",
                "".join(", %d %s" % (n, o) for o, n in p["outcomes"].items() if o != "done")))
        i = sc["incidents"]
        lines.append("Incidents: %d opened, %d fixed (median %s min), %d reached bojjifomo, fixed by: %s" % (
            i["opened"], i["fixed"], i["median_minutes"] if i["median_minutes"] is not None else "-", i["reached_you"],
            ", ".join("%s %d" % kv for kv in i["fixed_by"].items()) or "-"))
        r = sc["reviews"]
        lines.append("Reviews: %d (%d safe, %d blocked)" % (r["count"], r["safe"], r["blocked"]))
        return "\n".join(lines)

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

    def add_intern(self, name, project, launchd="", url="", fresh="", fresh_hours=24, warden="", diagnose=None, diagnose_hint=""):
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
            if diagnose:
                item["diagnose"] = [str(a) for a in diagnose]
                item["diagnose_hint"] = str(diagnose_hint)[:200]
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
            cmd, hint = self._probe_of(i)
            if reasons and cmd:
                why = self._diagnose(cmd, hint)
                if why:
                    reasons.append(why)
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

    def _probe_of(self, i):
        """(command, hint) for an intern: its own, else the one in roster.KNOWN_INTERNS with the same name
        (interns stored before the key existed)."""
        if i.get("diagnose"):
            return i["diagnose"], i.get("diagnose_hint", "")
        k = next((k for k in roster.KNOWN_INTERNS if k["name"] == i.get("name")), None)
        return (k.get("diagnose"), k.get("diagnose_hint", "")) if k else (None, "")

    def _diagnose(self, cmd, hint=""):
        """The url check failed: run the probe command (no shell) so the warden sees the cause."""
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return ""
            out = (res.stdout or "") + (res.stderr or "")
        except subprocess.TimeoutExpired:
            out = "timed out after 10 s"
        except OSError as e:
            out = str(e)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        line = next((ln for ln in lines if "dyld" in ln or "Library not loaded" in ln),
                    lines[0] if lines else "exit code is not 0")
        text = "%s gagal: %s" % (" ".join([os.path.basename(cmd[0])] + cmd[1:]), line[:200])
        return text + (" — jalankan: " + hint if hint else "")

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
                      rules=rules_for(level, self.cli))
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
            brief = BUDGET_BRIEF.format(when=when, report=report.text(data) + "\n\n" + self.scorecard_text(7),
                                        guide=report.MODEL_GUIDE, cli=self.cli,
                                        rules=rules_for("ceo", self.cli))
        elif kind == "innovator":
            month = time.time() - 30 * 86400
            recent = [x for x in self.incidents if x["opened"] >= month]
            incidents = "\n".join("- %s %s: %s (%s)" % (now_text(x["opened"]), x["name"], "; ".join(x["reasons"])[:160],
                                                        x["status"]) for x in recent) or "- none"
            brief = INNOVATOR_BRIEF.format(when=when, situation=self.situation(), incidents=incidents,
                                           cli=self.cli, rules=rules_for("innovator", self.cli))
        else:
            label = {"ops": "operations", "ceo": "CEO"}[kind]
            brief = ROUND_BRIEF.format(role=ROLE_NAMES[role], kind=label, when=when, takeover=takeover,
                                       situation=self.situation(), rules=rules_for(role, self.cli))
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
        if self.settings.get("auto_review_prs") and not self._sweeping and now >= self._next_sweep and self.holder("reviewer"):
            self._next_sweep = now + self.settings["pr_sweep_minutes"] * 60
            self._sweeping = True

            def sweep():
                try:
                    self.sweep_prs()
                except Exception:
                    import traceback
                    traceback.print_exc()
                finally:
                    self._sweeping = False

            threading.Thread(target=sweep, daemon=True, name="chain-pr-sweep").start()
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
            for rv in self.reviews:   # a review that never reports back is failed, so its commit can be tried again
                if not rv.get("ended") and now - rv.get("started", now) > limit:
                    rv.update(ended=now, outcome="failed", note="stuck for over %d minutes" % self.settings["stuck_minutes"])
                    self.runs.pop(rv.get("sid"), None)
                    if rv.get("pr") and rv.get("sha"):
                        self.pr_seen.pop("%s:%s" % (rv["pr"], rv["sha"]), None)
                    self.save()
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
            result = handoff.parse(run.final, ok)
            if entry["kind"] == "review":
                rv = next((x for x in self.reviews if x.get("sid") == run.sid), None)
                if rv:
                    rv.update(ended=time.time(), outcome=result["outcome"], note=result["note"] or summary[:300])
                    if result["outcome"] == "failed" and rv.get("pr") and rv.get("sha"):
                        self.pr_seen.pop("%s:%s" % (rv["pr"], rv["sha"]), None)   # a failed review is tried again
                self.save()
            elif entry["kind"] == "round":
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
                        a.update(ended=time.time(), ok=ok, summary=summary, outcome=result["outcome"],
                                 note=result["note"])
                self.save()
        if entry["kind"] == "review":
            if rv:
                verdict = {"done": "safe to merge", "blocked": "do not merge yet", "needs-you": "needs your decision",
                           "failed": "the review did not finish"}[result["outcome"]]
                note = rv.get("note") or ""
                self.office.ledger.add("guild", "%s %s reviewed %s%s: %s%s" % (
                    handoff.ICON[result["outcome"]], rv["reviewer"], rv["url"],
                    (" (PR #%s %s)" % (rv["pr"], rv["sha"][:7])) if rv.get("sha") and rv.get("pr") else "", verdict, (" - " + note[:200]) if note else ""),
                    by=rv["reviewer"], sid=run.sid)
                self.office.notify("PR review: %s" % verdict, "%s · %s" % (rv["url"].split("github.com/")[-1], note[:140]),
                                   tag="review-" + run.sid[:8], url="/#s=" + run.sid)
            return
        if entry["kind"] == "round":
            if failed_ops:
                self._ceo_takeover("their round ended with an error: %s" % (summary[:200] or "no report"))
            return
        if inc["status"] == "resolved":
            return
        i = self.intern(inc["intern"])
        if not i:
            return
        stated = result
        result = self.check(i)
        if result["ok"]:
            self.resolve(inc["id"], "fixed by %s" % next((a["who"] for a in inc["attempts"] if a.get("sid") == run.sid), "the guild"),
                         by=next((a["who"] for a in inc["attempts"] if a.get("sid") == run.sid), "guild"))
        elif stated["outcome"] == "needs-you":
            # a decision only bojjifomo can make: climbing to the CEO would only spend Opus time on it
            with self.lock:
                inc["reasons"] = result["reasons"]
                inc["level"], inc["status"] = "you", "needs-you"
                self.save()
            self._to_you(inc, stated["note"] or "they need your decision")
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
                if key in ("auto", "auto_review", "auto_review_prs"):
                    self.settings[key] = bool(value)
                elif key == "allowed_models":
                    picked = [m for m in MODEL_CHOICES if isinstance(value, (list, tuple)) and m in value]
                    if not picked:
                        raise ValueError("keep at least one model")
                    self.settings["allowed_models"] = picked
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
                "reviews": list(reversed(self.reviews[-30:])), "start": self.start_steps(),
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

    def open_incidents(self):
        with self.lock:
            return [dict(x) for x in self.incidents if x["status"] != "resolved"]

    # ---- the server room: every program bojjifomo runs, checked in the background --------------------
    PROGRAMS_EVERY = 90

    def programs(self):
        """Known programs with their health: registered interns use their last check, the rest a cached probe."""
        now = time.time()
        with self.lock:
            cached = getattr(self, "_programs", None)
            stale = not cached or now - cached["at"] > self.PROGRAMS_EVERY
            busy = getattr(self, "_programs_busy", False)
            if stale and not busy:
                self._programs_busy = True
                threading.Thread(target=self._probe_programs, daemon=True, name="programs").start()
            rows = list((cached or {"rows": []})["rows"])
        for r in rows:
            i = self.intern(r["name"])
            if i:
                h = self.health.get(i["id"])
                r["intern"] = True
                if h:
                    r["ok"], r["reasons"] = h["ok"], h["reasons"]
        up = sum(1 for r in rows if r.get("ok"))
        return {"rows": rows, "up": up, "trouble": sum(1 for r in rows if r.get("ok") is False), "total": len(rows),
                "checked": (cached or {}).get("at", 0)}

    def _probe_programs(self):
        rows = []
        try:
            try:
                jobs = self.office.familiars.jobs()
            except Exception:
                jobs = []
            for k in self.known_interns():
                probe = {"id": "probe-" + secrets.token_hex(3), "name": k["name"], "project": k["project_path"],
                         "launchd": k["launchd"], "url": k["url"], "fresh": k["fresh"], "fresh_hours": k["fresh_hours"]}
                try:
                    result = self.check(probe, jobs)
                except Exception as e:  # noqa: BLE001
                    result = {"ok": False, "reasons": [str(e)[:120]]}
                with self.lock:
                    self.health.pop(probe["id"], None)
                rows.append({"name": k["name"], "ok": result["ok"], "reasons": result["reasons"], "project": k["project_label"],
                             "url": k["url"], "launchd": k["launchd"], "intern": k["added"], "warden": k["warden_name"]})
        finally:
            with self.lock:
                self._programs = {"rows": rows, "at": time.time()}
                self._programs_busy = False
