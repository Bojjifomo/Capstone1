"""Eco mode: an Opus lead that only plans and reviews, and hands the work to cheaper Sonnet and Haiku helpers.

Opus tokens cost the most, so in eco mode the lead spends them where they pay off: understanding the quest,
splitting it, choosing the right helper for each piece, and checking what comes back. Reading files, searching,
writing code and running tests go to subagents on Haiku or Sonnet.

A quest stays in eco mode for every message sent to it from the Guild Hall (eco.json), because
--append-system-prompt only lasts for one `claude -p` call.
"""
import threading

import store

LEAD = "opus"
HELPERS = ("haiku", "sonnet")

PROMPT = """You are the eco-mode lead for this quest in bojjifomo's guild. You run on {lead} and your tokens are the
most expensive in the guild, so you PLAN and REVIEW; helpers do the work.

How to work:
1. Plan first. Understand the goal, then split it into small, self-contained pieces with a clear "done" for each.
2. Send each piece to a subagent (the Task/Agent tool) and set its model by how hard the piece is:
{ladder}
   A helper cannot see this conversation: give it the paths, the context, the exact change and what "done" means.
   Independent pieces can run at the same time.
3. Do not read big files, search the codebase or write code yourself. Two exceptions: a one-line fix after
   reviewing, and reading a helper's diff to judge it.
4. Review every result yourself: does it meet "done", does it break anything, are the tests run and passing?
   If not, send it back to a helper with exactly what to fix (move up to the stronger helper if the first one
   failed twice).
5. Guild members can take pieces too: `{cli} team`, then `{cli} ask <name> "<brief>" --no-wait` and
   `{cli} result <session-id>`.
6. Finish with a short report: a table of piece, helper, model, result; what you fixed after review;
   what is left open."""

LADDER = {
    "haiku": "   - haiku: look-ups, reading and summarizing files, searching, listing, simple edits and renames, running tests",
    "sonnet": "   - sonnet: normal coding, bug fixes, refactors, writing tests, anything that needs judgment",
}


def prompt(cli, allowed, lead=LEAD):
    """The lead's appended system prompt, naming only the helper models bojjifomo still offers."""
    helpers = [m for m in HELPERS if m in allowed] or ["sonnet"]
    ladder = "\n".join(LADDER[m] for m in helpers)
    if len(helpers) == 1:
        ladder += "\n   (only %s is offered as a helper right now)" % helpers[0]
    return PROMPT.format(lead=lead.capitalize(), ladder=ladder, cli=cli)


class Eco:
    """Which quests are in eco mode. Persisted, so a restart keeps them."""

    def __init__(self):
        saved = store.load("eco.json", [])
        if isinstance(saved, dict):
            sids, self.all = saved.get("sids") or [], bool(saved.get("all"))
        else:
            sids, self.all = saved, False
        self.sids = set(x for x in sids if isinstance(x, str)) if isinstance(sids, list) else set()
        self.lock = threading.Lock()

    def has(self, sid):
        """Eco for this quest: switched on for it, or the office-wide Eco button is on."""
        return self.all or sid in self.sids

    def _save(self):
        store.save("eco.json", {"sids": sorted(self.sids), "all": self.all})

    def set(self, sid, on):
        with self.lock:
            if on:
                self.sids.add(sid)
            else:
                self.sids.discard(sid)
            self._save()

    def set_all(self, on):
        """The Eco button in the header: every new session and every message from here runs eco."""
        with self.lock:
            self.all = bool(on)
            self._save()
