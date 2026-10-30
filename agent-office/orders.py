"""Orders from the guild chat: bojjifomo types a line under the map, the guild acts on it.

  @Name <message>        to one person: a guild member gets it as an errand, a quest gets it as your next message
  <message>              to the Operations Manager, who does it or hands it out (the CEO if ops is vacant)
  /check <intern>        check one program now
  /round ops|innovator|ceo|budget
  /status                the situation report, in the chat
  /help                  this list

Everything is written to the ledger as kind "order" (what you said) and "reply" (their final answer).
"""
import re

import chain as chain_mod

HELP = ("@Name pesan → ke satu orang (anggota guild dapat errand, quest dapat pesan berikutnya). "
        "Tanpa @ → ke Operations Manager, dia yang kerjain atau bagi-bagi. "
        "/check <program> · /round ops|innovator|ceo|budget · /status · /help")

OPS_BRIEF = """bojjifomo just wrote this in the guild chat:

"{text}"

Act on it as the Operations Manager. If it is small and yours, do it. If it belongs to someone else, hand it over with a
self-contained brief (`{cli} who "<task>"`, `{cli} ask <name> "<brief>" --no-wait`) and say who has it. If it needs
bojjifomo's approval, file a petition (`{cli} propose`). If it is a question, answer it.
Your final answer goes straight back into the chat: a few lines of casual Indonesian, no headings."""

ERRAND_NOTE = """bojjifomo wrote to you in the guild chat:

"{text}"

Do it or answer it. Your final answer goes straight back into the chat: a few lines of casual Indonesian."""


class OrderError(Exception):
    pass


def first(name):
    return str(name or "").split(" ")[0].lower()


def find_target(office, key):
    """A guild member or an open quest, by name or first name. Returns (kind, object)."""
    key = key.strip().lower()
    member = office.team.find(key)
    if member and not member.get("builtin"):
        return "staff", member
    sessions = [s for s in office.all_sessions() if s["status"] in ("needs", "working", "idle") and not s.get("helper")]
    hits = [s for s in sessions if first(s.get("persona", {}).get("name")) == key or s["name"].lower() == key]
    if not hits:
        hits = [s for s in sessions if s["name"].lower().startswith(key)]
    if len(hits) > 1:
        needs = [s for s in hits if s["status"] == "needs"]
        if len(needs) == 1:
            hits = needs
        else:
            raise OrderError("%d quests answer to %s. Open the right one from the list, or use a longer name." % (len(hits), key))
    if hits:
        return "session", hits[0]
    if member and member.get("builtin"):
        raise OrderError("%s writes the brief only: open the Counting House instead." % member["name"])
    raise OrderError("Nobody in town is called %s. Try @ and a name from the list." % key)


def handle(office, text, default_mode="acceptEdits"):
    text = " ".join(str(text).split()) if "\n" not in str(text) else str(text).strip()
    if not text:
        raise OrderError("Write something first.")
    if len(text) > 4000:
        raise OrderError("Keep a chat order under 4000 characters; open the person for a long brief.")
    cli = office.chain.cli

    if text.startswith("/"):
        cmd, _, rest = text[1:].partition(" ")
        cmd, rest = cmd.lower(), rest.strip()
        if cmd == "help":
            return {"reply": HELP, "by": "Guild"}
        if cmd == "status":
            return {"reply": office.chain.situation(), "by": "Chain"}
        if cmd == "check":
            if not rest:
                raise OrderError("Which program? /check <name>")
            r = office.chain.check_now(rest)
            return {"reply": "%s: %s" % (rest, "sehat" if r["ok"] else "DOWN: " + "; ".join(r["reasons"])), "by": "Chain"}
        if cmd == "round":
            if rest not in ("ops", "innovator", "ceo", "budget"):
                raise OrderError("/round ops, innovator, ceo or budget")
            r = office.chain.start_round(rest)
            office.ledger.add("order", "/round " + rest, by="bojjifomo", sid=r["sid"])
            return {"to": r["who"], "sid": r["sid"], "run": r.get("run")}
        raise OrderError("Unknown command /%s. %s" % (cmd, HELP))

    match = re.match(r"^@([^\s:,]+(?:\s[IVX]+)?)[\s:,]+(.+)$", text, re.S)
    if match:
        kind, target = find_target(office, match.group(1))
        message = match.group(2).strip()
        if kind == "staff":
            result = office.team.ask(target["id"], ERRAND_NOTE.format(text=message), origin="chat")
            who = target["name"]
        else:
            result = office.send(target["id"], message, default_mode)
            who = target.get("persona", {}).get("name", target["name"]).split(" ")[0]
            result.setdefault("sid", target["id"])
    else:
        boss = office.chain.holder("ops") or office.chain.holder("ceo")
        if not boss:
            raise OrderError("Appoint an Operations Manager in ⚜️ Chain first, or talk to someone directly with @Name.")
        result = office.team.ask(boss["id"], OPS_BRIEF.format(text=text, cli=cli), origin="chat",
                                 mode=chain_mod.MODE)
        who, message = boss["name"], text
    if result.get("queued_for_gold"):
        office.ledger.add("order", "→ %s (waits for gold): %s" % (who, message[:300]), by="bojjifomo")
        return {"to": who, "held": True}
    sid = result.get("sid")
    if sid and result.get("run"):
        office.chat_runs[sid] = who
    office.ledger.add("order", "→ %s: %s" % (who, message[:300]), by="bojjifomo", sid=sid or "")
    return {"to": who, "sid": sid, "run": result.get("run"), "queued": bool(result.get("queued"))}
