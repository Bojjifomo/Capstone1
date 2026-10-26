"""Small one-shot Haiku calls (summaries, quick replies). No tools, nothing saved to disk.

Results are cached per input, so reopening a sheet never pays twice for the same message.
"""
import hashlib
import json
import subprocess
import threading

import runner

SUMMARY_SYSTEM = ("You summarize what a coding agent just reported, for its owner reading on a phone. "
                  "Answer in the language of the message. 2-4 short bullet points, plain words, no preamble. "
                  "Say clearly if the agent is asking the owner something.")
REPLY_SYSTEM = ("You suggest replies the owner could send to a coding agent, based on the agent's last message. "
                "Answer in the language of the message. Return ONLY a JSON array of 3 short replies "
                "(each under 80 characters), most likely first. If the agent asked a question, answer options for it.")

_cache = {}
_lock = threading.Lock()


def ask(cfg, system, text, timeout=60):
    key = hashlib.sha256((system + "\0" + text).encode()).hexdigest()
    with _lock:
        if key in _cache:
            return _cache[key]
    claude = runner.find_claude(cfg)
    if not claude:
        raise RuntimeError("claude was not found; set claude_bin in config.json")
    argv = [claude, "-p", "--model", "haiku", "--no-session-persistence", "--tools", "",
            "--system-prompt", system, "--output-format", "json"]
    try:
        p = subprocess.run(argv, input=text[-12000:], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Haiku took too long")
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or "Haiku failed").strip()[-300:])
    try:
        out = json.loads(p.stdout).get("result", "")
    except ValueError:
        out = p.stdout
    out = str(out).strip()
    with _lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = out
    return out


def summarize(cfg, message):
    return ask(cfg, SUMMARY_SYSTEM, message)


def replies(cfg, message):
    raw = ask(cfg, REPLY_SYSTEM, message)
    try:
        start, end = raw.index("["), raw.rindex("]") + 1
        items = json.loads(raw[start:end])
    except ValueError:
        items = [line.strip("-•* \t\"") for line in raw.splitlines()]
    return [str(i).strip()[:120] for i in items if str(i).strip()][:3]
