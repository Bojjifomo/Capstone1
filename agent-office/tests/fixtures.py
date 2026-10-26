"""Build a fake $HOME with Claude Code transcripts in every state, for the test copy on :7790."""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def sid(n):
    return str(uuid.UUID(int=n * 0x1111111111111111111))


DOCS_REPLY = """## Pipeline report

The nightly import is **fixed**. Three things changed:

1. Rows with an empty `wallet` field are skipped instead of crashing.
2. Retries now back off: 2s, 4s, 8s.
3. The summary table is written atomically.

| Step | Before | After |
| --- | --- | --- |
| Import | 14 min | 3 min |
| Retry storms | daily | none |

```python
def backoff(attempt):
    return 2 ** attempt
```

> Draft for the changelog: Nightly import is 4x faster and no longer crashes on empty rows.

- Next: watch tonight's run
  - check the log at 02:00
"""


def write(home, project, n, title, entries, mtime):
    folder = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", project)
    folder.mkdir(parents=True, exist_ok=True)
    Path(project).mkdir(parents=True, exist_ok=True)
    path = folder / (sid(n) + ".jsonl")
    with open(path, "w") as f:
        if title:
            f.write(json.dumps({"type": "summary", "summary": title, "leafUuid": "x"}) + "\n")
        for e in entries:
            e.setdefault("cwd", project)
            e.setdefault("sessionId", sid(n))
            e.setdefault("entrypoint", "cli")
            e.setdefault("gitBranch", "main")
            f.write(json.dumps(e) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def user(text, ts):
    return {"type": "user", "timestamp": iso(ts), "message": {"role": "user", "content": text}}


def tool_result(tid, ts):
    return {"type": "user", "timestamp": iso(ts),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}


def assistant(ts, text=None, tool=None, stop="end_turn", model="claude-opus-5-5", mid=None):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "id": tool[0], "name": tool[1], "input": tool[2]})
        stop = "tool_use"
    return {"type": "assistant", "timestamp": iso(ts), "message": {
        "id": mid or "msg_" + uuid.uuid4().hex[:12], "role": "assistant", "model": model, "stop_reason": stop,
        "content": content,
        "usage": {"input_tokens": 1200, "output_tokens": 800, "cache_read_input_tokens": 40000,
                  "cache_creation_input_tokens": 3000}}}


def build(home):
    home = Path(home)
    now = time.time()
    p = str(home / "Projects")
    write(home, p + "/webapp", 1, "Fix the login redirect loop", [
        user("The login page loops back to itself after sign-in. Find out why and fix it.", now - 900),
        assistant(now - 880, "Looking at the auth middleware first.", ("t1", "Grep", {"pattern": "redirect_to"})),
        tool_result("t1", now - 870),
        assistant(now - 20, None, ("t2", "Bash", {"command": "npm test -- auth"})),
    ], now - 5)
    write(home, p + "/api-server", 2, "Refactor the payments API", [
        user("Split payments.py into a service and a router.", now - 4000),
        assistant(now - 300, "Done: `payments/service.py` holds the logic and `payments/router.py` the routes. "
                  "All 42 tests pass.\n\nShould I also migrate the three old v1 endpoints, or leave them for now?"),
    ], now - 300)
    write(home, p + "/docs-site", 3, "Write the onboarding guide", [
        user("Write an onboarding guide for new contributors.", now - 2000),
        assistant(now - 400, "I have an outline ready.", ("t3", "AskUserQuestion", {"questions": [
            {"question": "Which audience should the guide target first?"}]})),
    ], now - 400)
    write(home, p + "/rh-pipeline", 4, "Nightly import pipeline", [
        user("The nightly import keeps crashing. Fix it and give me a short report.", now - 7000),
        assistant(now - 1200, DOCS_REPLY, model="claude-sonnet-5-5"),
    ], now - 1200)
    write(home, p + "/landing", 5, "", [
        user("Make the hero section on the landing page look less empty", now - 5000),
        assistant(now - 3400, "Added a gradient, the product screenshot and two short benefit lines. Done.",
                  model="claude-haiku-5-5"),
    ], now - 3400)
    write(home, p + "/experiments", 6, "Try a websocket prototype", [
        user("Prototype a websocket feed.", now - 3 * 86400),
        assistant(now - 2 * 86400, "Prototype works; see ws_demo.py. Closing this out."),
    ], now - 2 * 86400)
    (home / "Projects" / "webapp").mkdir(parents=True, exist_ok=True)
    build_mac_bits(home)
    return home


FAKE_LAUNCHCTL = """#!/bin/sh
# Stands in for launchctl in tests.
if [ "$1" = "list" ]; then
  printf '%s\\t%s\\t%s\\n' PID Status Label 4242 0 com.bojjifomo.agent-office - 0 com.bojjifomo.lp-scanner - 1 com.bojjifomo.daily-report
  exit 0
fi
echo "$@" >> "$HOME/launchctl.log"
"""


def build_mac_bits(home):
    """LaunchAgents with logs, a fake launchctl, and some big folders for the vault."""
    import plistlib
    agents = home / "Library" / "LaunchAgents"
    logs = home / "logs"
    agents.mkdir(parents=True, exist_ok=True)
    logs.mkdir(exist_ok=True)
    jobs = {
        "com.bojjifomo.agent-office": {"KeepAlive": True, "ProgramArguments": ["/usr/bin/python3", "server.py"]},
        "com.bojjifomo.lp-scanner": {"StartInterval": 900, "ProgramArguments": ["/usr/bin/python3", "scan.py"]},
        "com.bojjifomo.daily-report": {"StartCalendarInterval": {"Hour": 7, "Minute": 30},
                                       "ProgramArguments": ["/bin/sh", "report.sh"]},
    }
    for label, p in jobs.items():
        p.update(Label=label, StandardOutPath=str(logs / (label + ".out")), StandardErrorPath=str(logs / (label + ".err")))
        with open(agents / (label + ".plist"), "wb") as f:
            plistlib.dump(p, f)
        (logs / (label + ".out")).write_text("ok\n")
        (logs / (label + ".err")).write_text("")
    (logs / "com.bojjifomo.daily-report.err").write_text(
        "fetching prices\nTraceback (most recent call last):\nConnectionError: rpc.example timed out after 30s\n")
    tool = home / "bin" / "launchctl"
    tool.parent.mkdir(exist_ok=True)
    tool.write_text(FAKE_LAUNCHCTL)
    tool.chmod(0o755)
    for rel, mb in (("Downloads", 6), (".npm", 3), (".Trash", 2), ("Library/Caches", 4)):
        d = home / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "blob.bin").write_bytes(os.urandom(mb * 1024 * 1024))
    (home / ".Trash" / "old folder").mkdir(exist_ok=True)
    (home / ".Trash" / "old folder" / "x.txt").write_text("bye")


if __name__ == "__main__":
    print(build(sys.argv[1]))
