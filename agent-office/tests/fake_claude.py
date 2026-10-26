#!/usr/bin/env python3
"""Stands in for `claude -p ... stream-json` in tests: streams a reply and appends the transcript."""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

args = sys.argv[1:]
sid = args[args.index("--resume") + 1] if "--resume" in args else args[args.index("--session-id") + 1]
mode = args[args.index("--permission-mode") + 1]
line = sys.stdin.readline()
text = json.loads(line)["message"]["content"][0]["text"]
delay = float(os.environ.get("FAKE_CLAUDE_DELAY", "0.15")) * (8 if "slow" in text else 1)
now = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
folder = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
folder.mkdir(parents=True, exist_ok=True)
transcript = folder / (sid + ".jsonl")


def log(entry):
    entry.update({"sessionId": sid, "cwd": os.getcwd(), "timestamp": now(), "entrypoint": "sdk-cli"})
    with open(transcript, "a") as f:
        f.write(json.dumps(entry) + "\n")


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


log({"type": "user", "message": {"role": "user", "content": text}})
out({"type": "system", "subtype": "init", "session_id": sid, "model": "claude-sonnet-5-5", "permissionMode": mode})
mid = "msg_" + uuid.uuid4().hex[:10]
reply = "**Heard you.** You said: %s\n\n```\nmode=%s\n```" % (text[:200], mode)
out({"type": "stream_event", "event": {"type": "message_start", "message": {"id": mid}}})
for i in range(0, len(reply), 12):
    out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": reply[i:i + 12]}}})
    time.sleep(delay / 4)
msg = {"id": mid, "role": "assistant", "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
       "content": [{"type": "text", "text": reply}],
       "usage": {"input_tokens": 10, "output_tokens": 40, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0}}
out({"type": "assistant", "message": msg})
log({"type": "assistant", "message": msg})
time.sleep(delay)
out({"type": "result", "subtype": "success", "is_error": False, "result": reply, "total_cost_usd": 0.0012})
