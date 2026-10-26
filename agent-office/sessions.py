"""Find Claude Code sessions: transcripts in ~/.claude/projects plus the process list."""
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

KEEP_MESSAGES = 40
MAX_TEXT = 6000
MAX_AGE_DAYS = 30

# $ per million tokens: (model prefix, input, output, cache read). Cache writes are
# 1.25x input (5 minute) or 2x input (1 hour). Longest prefix wins.
PRICES = [
    ("claude-fable-5-1", 10, 50, 0.25), ("claude-fable", 10, 50, 1.0), ("claude-mythos", 10, 50, 1.0),
    ("claude-opus-5-5", 4, 20, 0.20), ("claude-opus-5", 5, 25, 0.50),
    ("claude-opus-4-5", 5, 25, 0.50), ("claude-opus-4-6", 5, 25, 0.50),
    ("claude-opus-4-7", 5, 25, 0.50), ("claude-opus-4-8", 5, 25, 0.50),
    ("claude-opus-4", 15, 75, 1.50), ("claude-3-opus", 15, 75, 1.50),
    ("claude-sonnet-5", 2, 10, 0.20), ("claude-sonnet-4", 3, 15, 0.30), ("claude-3", 3, 15, 0.30),
    ("claude-haiku-5", 0.10, 0.50, 0.01), ("claude-haiku-4", 1, 5, 0.10), ("claude-3-5-haiku", 0.80, 4, 0.08),
]
PRICES.sort(key=lambda p: -len(p[0]))
DEFAULT_PRICE = (3, 15, 0.30)

ASK_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
SESSION_ID_RE = re.compile(r"(?:--resume|-r|--session-id)[ =]([0-9a-f]{8}-[0-9a-f-]{27})")
CLAUDE_CMD_RE = re.compile(r"(?:^|/)claude(?:\s|$)|@anthropic-ai/claude-code")


def projects_dir():
    return Path.home() / ".claude" / "projects"


def encode_project(cwd):
    """Claude Code names project folders after the cwd with every non-alphanumeric char as '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def price_for(model):
    for prefix, i, o, cr in PRICES:
        if model.startswith(prefix):
            return i, o, cr
    return DEFAULT_PRICE


def usage_cost(model, u):
    if not model or model.startswith("<"):
        return 0.0
    i, o, cr = price_for(model)
    cw = u.get("cache_creation_input_tokens", 0) or 0
    detail = u.get("cache_creation") or {}
    cw_1h = detail.get("ephemeral_1h_input_tokens", 0) or 0
    cw_5m = max(cw - cw_1h, 0)
    return ((u.get("input_tokens", 0) or 0) * i + (u.get("output_tokens", 0) or 0) * o
            + (u.get("cache_read_input_tokens", 0) or 0) * cr
            + cw_5m * i * 1.25 + cw_1h * i * 2) / 1e6


def short_model(model):
    """claude-opus-5-5 -> Opus 5.5"""
    if not model or model.startswith("<"):
        return ""
    m = re.match(r"claude-([a-z]+)-(\d+)(?:-(\d))?(?:-|$)", model)
    if not m:
        return model.replace("claude-", "")
    name, major, minor = m.groups()
    return name.capitalize() + " " + major + ("." + minor if minor else "")


def parse_ts(s):
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def day_of(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


TAG_RE = re.compile(r"<(system-reminder|local-command-stdout|local-command-stderr|command-message|command-args)>[\s\S]*?</\1>")


def clean_user_text(text):
    cmd = re.search(r"<command-name>([^<]*)</command-name>", text)
    args = re.search(r"<command-args>([^<]*)</command-args>", text)
    text = TAG_RE.sub("", text)
    text = re.sub(r"</?command-name>[^<]*(</command-name>)?", "", text)
    if cmd:
        text = (cmd.group(1).strip() + " " + (args.group(1).strip() if args else "")).strip()
    return text.strip()


def tool_summary(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        detail = inp.get("command", "")
    elif name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        detail = os.path.basename(str(inp.get("file_path") or inp.get("notebook_path") or ""))
    elif name in ("Grep", "Glob"):
        detail = inp.get("pattern", "")
    elif name in ("Task", "Agent"):
        detail = inp.get("description", "")
    elif name in ("WebFetch",):
        detail = inp.get("url", "")
    elif name == "WebSearch":
        detail = inp.get("query", "")
    elif name == "AskUserQuestion":
        qs = inp.get("questions") or []
        detail = qs[0].get("question", "") if qs and isinstance(qs[0], dict) else ""
    else:
        detail = ""
    detail = " ".join(str(detail).split())
    return name + (": " + detail[:140] if detail else "")


def ends_with_question(text):
    """True only when the last part of a reply actually asks something."""
    text = re.sub(r"```[\s\S]*?(```|$)", "", text or "")
    lines = [l.strip() for l in text.strip().splitlines()]
    lines = [l for l in lines if l and not l.startswith(">") and not l.startswith("|")]
    for line in lines[-2:]:
        line = re.sub(r"[*_`)\]]+$", "", line).rstrip()
        if line.endswith("?") or line.endswith("？"):
            return True
    return False


class Transcript:
    """Incrementally parsed state of one session's .jsonl file."""

    def __init__(self, path):
        self.path = path
        self.id = path.stem
        self.reset()

    def reset(self):
        self.offset = 0
        self.size = 0
        self.mtime = 0.0
        self.cwd = ""
        self.summary = ""
        self.custom_title = ""
        self.first_user = ""
        self.first_ts = 0.0
        self.last_ts = 0.0
        self.model = ""
        self.entrypoint = ""
        self.branch = ""
        self.messages = deque(maxlen=KEEP_MESSAGES)
        self.pending = {}  # tool_use id -> summary, cleared by its tool_result
        self.last_kind = ""  # "user" | "assistant" | "interrupt"
        self.stop_reason = ""
        self.last_text = ""
        self.last_msg_id = ""
        self.seen_ids = set()
        self.usage = {}  # day -> model -> [in, out, cache_read, cache_write, cost]
        self.lines = 0

    def update(self):
        try:
            st = self.path.stat()
        except OSError:
            return False
        if st.st_size == self.size and st.st_mtime == self.mtime:
            return False
        if st.st_size < self.offset:
            self.reset()
        self.mtime = st.st_mtime
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return True  # only a partial line so far
        chunk, self.offset = data[:end + 1], self.offset + end + 1
        self.size = st.st_size
        for raw in chunk.splitlines():
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                self.lines += 1
                self.add(entry)
        return True

    def add(self, e):
        kind = e.get("type")
        if kind == "summary":
            self.summary = e.get("summary") or self.summary
            return
        if kind == "custom-title":
            self.custom_title = e.get("customTitle") or e.get("title") or self.custom_title
            return
        if kind == "ai-title":
            self.summary = e.get("aiTitle") or e.get("title") or self.summary
            return
        if kind not in ("user", "assistant"):
            return
        ts = parse_ts(e.get("timestamp"))
        if ts:
            self.first_ts = self.first_ts or ts
            self.last_ts = max(self.last_ts, ts)
        self.cwd = e.get("cwd") or self.cwd
        self.entrypoint = e.get("entrypoint") or self.entrypoint
        self.branch = e.get("gitBranch") or self.branch
        msg = e.get("message") or {}
        if kind == "assistant":
            self.add_usage(msg, ts)
        if e.get("isSidechain") or e.get("isMeta"):
            return
        if kind == "assistant":
            self.add_assistant(msg, ts)
        else:
            self.add_user(msg, ts)

    def add_usage(self, msg, ts):
        mid, usage, model = msg.get("id"), msg.get("usage"), msg.get("model") or ""
        if not usage or not mid or mid in self.seen_ids:
            return
        self.seen_ids.add(mid)
        row = self.usage.setdefault(day_of(ts or self.mtime), {}).setdefault(model, [0, 0, 0, 0, 0.0])
        row[0] += usage.get("input_tokens", 0) or 0
        row[1] += usage.get("output_tokens", 0) or 0
        row[2] += usage.get("cache_read_input_tokens", 0) or 0
        row[3] += usage.get("cache_creation_input_tokens", 0) or 0
        row[4] += usage_cost(model, usage)

    def add_assistant(self, msg, ts):
        model = msg.get("model") or ""
        if model and not model.startswith("<"):
            self.model = model
        texts, tools = [], []
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text", "").strip():
                texts.append(block["text"])
            elif block.get("type") == "tool_use":
                summary = tool_summary(block.get("name", "?"), block.get("input"))
                self.pending[block.get("id")] = (block.get("name", "?"), summary)
                tools.append(summary)
        mid = msg.get("id") or ""
        same = mid and mid == self.last_msg_id and self.messages and self.messages[-1]["role"] == "assistant"
        if same:
            last = self.messages[-1]
            if texts:
                last["text"] = (last["text"] + "\n\n" + "\n\n".join(texts)).strip()[-MAX_TEXT:]
            last["tools"].extend(tools)
        elif texts or tools:
            self.messages.append({"role": "assistant", "text": "\n\n".join(texts)[-MAX_TEXT:], "tools": tools, "ts": ts})
        if texts:
            self.last_text = self.messages[-1]["text"]
        self.last_msg_id = mid
        self.last_kind = "assistant"
        self.stop_reason = (msg.get("stop_reason") or self.stop_reason) if same else (msg.get("stop_reason") or "")

    def add_user(self, msg, ts):
        content = msg.get("content")
        texts, had_result = [], False
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    self.pending.pop(block.get("tool_use_id"), None)
                    had_result = True
                elif block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    texts.append("[image]")
        text = clean_user_text("\n".join(texts))
        self.last_msg_id = ""
        if not text:
            if had_result:
                self.last_kind = "user"  # the model carries on after a tool result
            return
        if text.startswith("[Request interrupted"):
            self.pending.clear()
            self.last_kind = "interrupt"
            return
        self.pending.clear()
        self.first_user = self.first_user or text[:200]
        self.messages.append({"role": "user", "text": text[-MAX_TEXT:], "tools": [], "ts": ts})
        self.last_kind = "user"

    @property
    def title(self):
        t = self.custom_title or self.summary or self.first_user or "Untitled quest"
        t = " ".join(t.split())
        return t if len(t) <= 70 else t[:67] + "..."

    @property
    def turn_ended(self):
        if self.last_kind == "interrupt":
            return True
        return self.last_kind == "assistant" and not self.pending and self.stop_reason != "tool_use"

    def usage_total(self):
        total = [0, 0, 0, 0, 0.0]
        for models in self.usage.values():
            for row in models.values():
                for i, v in enumerate(row):
                    total[i] += v
        return total


def ps_table():
    """pid -> (ppid, command) for every process (works on macOS and Linux)."""
    try:
        out = subprocess.run(["ps", "axo", "pid=,ppid=,command="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def process_cwds(pids):
    if not pids:
        return {}
    if sys.platform.startswith("linux"):
        found = {}
        for pid in pids:
            try:
                found[pid] = os.readlink("/proc/%d/cwd" % pid)
            except OSError:
                pass
        return found
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(map(str, pids))],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found, pid = {}, None
    for line in out.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid:
            found[pid] = line[1:]
    return found


def is_desktop(pid, table):
    for _ in range(8):
        entry = table.get(pid)
        if not entry:
            return False
        ppid, cmd = entry
        if "Claude.app" in cmd or "Application Support/Claude/" in cmd:
            return True
        pid = ppid
    return False


def claude_processes(own_pid=None):
    """Live Claude Code CLI processes: list of dicts with pid, session id (if known), cwd, desktop."""
    table = ps_table()
    procs = []
    for pid, (ppid, cmd) in table.items():
        if pid == own_pid or not CLAUDE_CMD_RE.search(cmd):
            continue
        if "agent-office" in cmd or "tools/office" in cmd:
            continue
        m = SESSION_ID_RE.search(cmd)
        procs.append({"pid": pid, "sid": m.group(1) if m else "", "cwd": "",
                      "desktop": is_desktop(pid, table), "print": " -p" in cmd or "--print" in cmd})
    cwds = process_cwds([p["pid"] for p in procs])
    for p in procs:
        p["cwd"] = cwds.get(p["pid"], "")
    return procs


class Scanner:
    """Keeps all recent transcripts parsed; refresh() is cheap when nothing changed."""

    def __init__(self):
        self.transcripts = {}
        self.procs = []
        self.live = {}  # sid -> process info
        self.lock = threading.Lock()
        self.last_scan = 0.0

    def refresh(self, min_interval=2.0):
        with self.lock:
            now = time.time()
            if now - self.last_scan < min_interval:
                return
            self.last_scan = now
            seen = set()
            root = projects_dir()
            cutoff = now - MAX_AGE_DAYS * 86400
            try:
                folders = [d for d in root.iterdir() if d.is_dir()]
            except OSError:
                folders = []
            for folder in folders:
                try:
                    files = list(folder.glob("*.jsonl"))
                except OSError:
                    continue
                for f in files:
                    try:
                        if f.stat().st_mtime < cutoff:
                            continue
                    except OSError:
                        continue
                    t = self.transcripts.get(f.stem)
                    if t is None or t.path != f:
                        t = self.transcripts[f.stem] = Transcript(f)
                    t.update()
                    seen.add(f.stem)
            for sid in list(self.transcripts):
                if sid not in seen:
                    del self.transcripts[sid]
            self.procs = claude_processes(os.getpid())
            self.live = self.match_procs()

    def match_procs(self):
        live = {}
        by_folder = {}
        for t in self.transcripts.values():
            by_folder.setdefault(t.path.parent.name, []).append(t)
        for p in self.procs:
            if p["sid"] in self.transcripts:
                live[p["sid"]] = p
        for p in self.procs:
            if p["sid"] or not p["cwd"]:
                continue
            candidates = [t for t in by_folder.get(encode_project(p["cwd"]), []) if t.id not in live]
            if candidates:
                newest = max(candidates, key=lambda t: t.mtime)
                live[newest.id] = p
        return live
