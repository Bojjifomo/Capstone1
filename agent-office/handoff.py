"""The handoff contract: every guild run ends with one machine-readable line, so the hall knows what happened.

    RESULT: done - <one sentence>
    RESULT: blocked - <what stopped you>
    RESULT: needs you - <the decision or step bojjifomo must take>

A run without the line is "done" when it succeeded and "failed" when it did not. "needs you" goes straight to
bojjifomo's phone instead of climbing the chain, which saves the CEO's Opus time on decisions only bojjifomo can make.
"""
import re

CONTRACT = """

## The last line of your answer

End with exactly one line in this form, so the Guild Hall knows what happened:
RESULT: done - <one sentence: what is now true>
RESULT: blocked - <what stopped you and what you tried>
RESULT: needs you - <the decision or step only bojjifomo can take>"""

_LINE = re.compile(r"^[\s>*_`#-]*RESULT\s*:\s*\**\s*(done|blocked|needs[\s_-]*(?:you|bojjifomo))\b\**\s*[-:–—]*\s*(.*)$",
                   re.IGNORECASE | re.MULTILINE)
ICON = {"done": "✅", "blocked": "⛔", "needs-you": "🙋", "failed": "❌"}
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


def parse(text, ok=True):
    """The outcome of a run: done, blocked, needs-you, or failed (no RESULT line and the run did not succeed)."""
    # a line that still has the template's <placeholder> is the contract being quoted, not an answer
    found = [m for m in _LINE.finditer(str(text or "")) if not m.group(2).strip().startswith("<")]
    if found:
        word, note = found[-1].group(1).lower(), found[-1].group(2).strip().strip("*`_ ")
        outcome = "done" if word == "done" else "blocked" if word == "blocked" else "needs-you"
        return {"outcome": outcome, "note": note[:300], "stated": True}
    return {"outcome": "done" if ok else "failed", "note": "", "stated": False}


def label(result):
    return "%s %s" % (ICON.get(result["outcome"], ""), result["outcome"].replace("-", " "))


def pr_urls(text):
    seen = []
    for url in PR_URL.findall(str(text or "")):
        if url not in seen:
            seen.append(url)
    return seen
