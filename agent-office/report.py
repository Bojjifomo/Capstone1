"""Where the gold goes: usage by person, kind of work, model and project, plus ideas for spending less.

Everything is read from the transcripts (tokens at API list prices), so it is an estimate. The ideas are
deterministic arithmetic, not guesses: "this person spent $X on Opus; the same tokens on Sonnet cost about $Y".
"""
from datetime import datetime, timedelta
from pathlib import Path

import sessions

FAMILIES = ("opus", "sonnet", "haiku")
# One current model per family, only used to price the same tokens somewhere cheaper.
ALT_MODEL = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-5"}
KIND_LABEL = {"you": "Your own sessions", "guild": "Guild errands", "chain": "Chain (CEO, ops, innovator, wardens)"}
MIN_IDEA = 0.25      # dollars in the window before an idea is worth showing
MODEL_GUIDE = (
    "haiku: health checks, log reading, lookups, summaries, formatting. The cheapest.\n"
    "sonnet: everyday coding and fixes, operations rounds, petitions. The default.\n"
    "opus: hard design, tricky bugs, the CEO's decisions. Seats are limited."
)


def family(model):
    m = str(model or "").lower()
    for f in FAMILIES:
        if f in m:
            return f
    return "other"


def cost_as(model_id, row):
    """What the tokens in a usage row ([in, out, cache_read, cache_write, cost]) cost on another model."""
    i, o, cr = sessions.price_for(model_id)
    return (row[0] * i + row[1] * o + row[2] * cr + row[3] * i * 1.25) / 1e6


def _blank(name, kind):
    return {"name": name, "kind": kind, "cost": 0.0, "today": 0.0, "tokens": 0, "sessions": 0, "mtime": 0, "sid": "",
            "fam": {}, "alt": {"sonnet": {}, "haiku": {}}}


def build(office, days=7):
    days = max(1, min(30, int(days)))
    office.scanner.refresh()
    today = datetime.now().date()
    keys = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    window, now_key = set(keys), keys[-1]
    with office.scanner.lock:
        transcripts = list(office.scanner.transcripts.values())

    actors, per_model, per_project, per_day = {}, {}, {}, {k: 0.0 for k in keys}
    chain = getattr(office, "chain", None)
    for t in transcripts:
        staff = office.team.member_for_cwd(t.cwd)
        if staff:
            role = chain.role_of(staff["id"]) if chain else ""
            key, name, kind = "staff:" + staff["id"], staff["name"], ("chain" if role else "guild")
        else:
            key, name, kind = "session:" + t.id, office.name_of(t), "you"
        actor = None
        project = Path(t.cwd).name if t.cwd else "other"
        for day, models in t.usage.items():
            if day not in window:
                continue
            for model, row in models.items():
                if not row[4] and not (row[0] or row[1]):
                    continue
                if actor is None:
                    actor = actors.setdefault(key, _blank(name, kind))
                    actor["staff"] = staff["id"] if staff else ""
                    actor["role"] = (chain.role_of(staff["id"]) if (chain and staff) else "")
                    actor["configured"] = (staff.get("model") or "default") if staff else ""
                fam = family(model)
                tokens = row[0] + row[1] + row[2] + row[3]
                actor["cost"] += row[4]
                actor["tokens"] += tokens
                actor["fam"][fam] = actor["fam"].get(fam, 0.0) + row[4]
                if fam == "opus":
                    actor["alt"]["sonnet"]["opus"] = actor["alt"]["sonnet"].get("opus", 0.0) + cost_as(ALT_MODEL["sonnet"], row)
                    actor["alt"]["haiku"]["opus"] = actor["alt"]["haiku"].get("opus", 0.0) + cost_as(ALT_MODEL["haiku"], row)
                if fam == "sonnet":
                    actor["alt"]["haiku"]["sonnet"] = actor["alt"]["haiku"].get("sonnet", 0.0) + cost_as(ALT_MODEL["haiku"], row)
                if day == now_key:
                    actor["today"] += row[4]
                per_day[day] += row[4]
                m = per_model.setdefault(fam, {"family": fam, "cost": 0.0, "tokens": 0})
                m["cost"] += row[4]
                m["tokens"] += tokens
                p = per_project.setdefault(project, {"name": project, "cost": 0.0})
                p["cost"] += row[4]
        if actor is not None:
            actor["sessions"] += 1
            if t.mtime >= actor["mtime"]:
                actor["mtime"], actor["sid"] = t.mtime, t.id

    total = sum(a["cost"] for a in actors.values())
    share = (lambda x: round(x / total, 4)) if total else (lambda x: 0.0)
    kinds = {k: {"kind": k, "label": KIND_LABEL[k], "cost": 0.0} for k in KIND_LABEL}
    for a in actors.values():
        kinds[a["kind"]]["cost"] += a["cost"]

    rows = []
    for a in sorted(actors.values(), key=lambda x: -x["cost"])[:15]:
        main = max(a["fam"].items(), key=lambda kv: kv[1])[0] if a["fam"] else "other"
        rows.append({"name": a["name"], "kind": a["kind"], "staff": a["staff"], "role": a["role"], "model": a["configured"] or main,
                     "main": main, "cost": round(a["cost"], 4), "today": round(a["today"], 4), "share": share(a["cost"]),
                     "tokens": a["tokens"], "sessions": a["sessions"], "sid": a["sid"],
                     "fam": {f: round(c, 4) for f, c in a["fam"].items()}})

    spend_rate = total / days
    result = {
        "days": days, "total": round(total, 4), "today": round(per_day[now_key], 4),
        "per_day": [{"day": k, "cost": round(v, 4)} for k, v in per_day.items()],
        "avg_day": round(spend_rate, 4), "projected_month": round(spend_rate * 30, 2),
        "kinds": [dict(k, cost=round(k["cost"], 4), share=share(k["cost"])) for k in kinds.values()],
        "actors": rows,
        "models": [dict(m, cost=round(m["cost"], 4), share=share(m["cost"])) for m in sorted(per_model.values(), key=lambda m: -m["cost"])],
        "projects": [dict(p, cost=round(p["cost"], 4), share=share(p["cost"])) for p in sorted(per_project.values(), key=lambda p: -p["cost"])[:8]],
        "note": "Estimated from transcript token counts at API list prices; a subscription bills differently. "
                "Quill's briefs run through the orchestrator and are not in the transcripts.",
    }
    result["ideas"] = ideas(actors, kinds, total, chain)
    result["potential"] = round(sum(i["saving"] for i in result["ideas"]), 2)
    seats = getattr(chain, "settings", {}).get("opus_seats", 2) if chain else 2
    used = sum(1 for s in office.team.staff if s.get("model") == "opus")
    result["seats"] = {"used": used, "max": seats}
    return result


def money(x):
    return "$%.2f" % x


def ideas(actors, kinds, total, chain):
    out = []
    for a in actors.values():
        fam, alt = a["fam"], a["alt"]
        who = a["name"]
        opus = fam.get("opus", 0.0)
        if opus >= MIN_IDEA:
            cheaper = alt["sonnet"].get("opus", 0.0)
            out.append({
                "id": "opus-" + who, "kind": "likely", "who": who, "saving": round(opus - cheaper, 2),
                "title": "%s: %s on Opus" % (who, money(opus)),
                "detail": "The same tokens on Sonnet cost about %s. Keep Opus for hard design and tricky bugs." % money(cheaper),
                "action": {"staff": a["staff"], "model": "sonnet"} if a.get("staff") else None,
                "hint": "" if a.get("staff") else "Start the next one with Sonnet (/model sonnet)."})
        sonnet = fam.get("sonnet", 0.0)
        if sonnet >= 2 * MIN_IDEA and a["kind"] in ("guild", "chain") and not a.get("role", "").startswith(("CEO", "Operations", "Innovator")):
            cheaper = alt["haiku"].get("sonnet", 0.0)
            out.append({
                "id": "haiku-" + who, "kind": "try", "who": who, "saving": round(sonnet - cheaper, 2),
                "title": "%s: %s on Sonnet" % (who, money(sonnet)),
                "detail": "On Haiku about %s. Worth trying only if their work is checks, lookups or summaries, "
                          "not code changes." % money(cheaper),
                "action": {"staff": a["staff"], "model": "haiku"}, "hint": ""})
    chain_cost = kinds["chain"]["cost"]
    if total and chain_cost >= 0.5 and chain_cost / total >= 0.35 and chain:
        now = chain.settings["ops_every_hours"]
        out.append({
            "id": "chain-upkeep", "kind": "setting", "who": "Chain", "saving": round(chain_cost * 0.5, 2),
            "title": "Chain upkeep is %d%% of the spend" % round(100 * chain_cost / total),
            "detail": "Rounds every %d h. Doubling the gap roughly halves the round cost." % now,
            "action": {"setting": {"ops_every_hours": min(72, now * 2)}}, "hint": ""})
    if total >= 1.0:
        top = max(actors.values(), key=lambda a: a["cost"])
        if top["cost"] / total >= 0.4:
            out.append({
                "id": "top-" + top["name"], "kind": "habit", "who": top["name"], "saving": round(top["cost"] * 0.2, 2),
                "title": "%s is %d%% of the spend" % (top["name"], round(100 * top["cost"] / total)),
                "detail": "A long conversation re-reads its whole history every turn. Start a fresh session for a new task.",
                "action": None, "hint": ""})
    out.sort(key=lambda i: -i["saving"])
    return out[:8]


def text(r):
    """The same report as plain text, for the CEO's brief and `office usage`."""
    lines = ["Last %d days: %s total, %s today, about %s a month at this rate." % (
        r["days"], money(r["total"]), money(r["today"]), money(r["projected_month"]))]
    lines.append("By kind: " + "; ".join("%s %s (%d%%)" % (k["label"], money(k["cost"]), round(100 * k["share"])) for k in r["kinds"]))
    lines.append("By model: " + ("; ".join("%s %s (%d%%)" % (m["family"], money(m["cost"]), round(100 * m["share"]))
                                          for m in r["models"]) or "nothing yet"))
    lines.append("Opus seats: %d of %d used." % (r["seats"]["used"], r["seats"]["max"]))
    lines.append("Biggest spenders:")
    for a in r["actors"][:10]:
        lines.append("- %s [%s, %s%s%s]: %s (%d%%), %d session(s)%s" % (
            a["name"], a["kind"], "configured " if a["staff"] else "mostly ", a["model"],
            (", " + a["role"]) if a["role"] else "", money(a["cost"]),
            round(100 * a["share"]), a["sessions"], ", models used: " + ", ".join("%s %s" % (f, money(c)) for f, c in a["fam"].items())))
    if r["projects"]:
        lines.append("By project: " + "; ".join("%s %s" % (p["name"], money(p["cost"])) for p in r["projects"][:6]))
    lines.append("Ideas (potential about %s):" % money(r["potential"]) if r["ideas"] else "Ideas: none worth acting on.")
    for i in r["ideas"]:
        lines.append("- [%s] %s. %s Saves about %s." % (i["kind"], i["title"], i["detail"], money(i["saving"])))
    return "\n".join(lines)
