"""Grok as a guild member: chat and errands on xAI's API instead of Claude Code.

A Grok run looks like any other run to the rest of Agent Office (runner.Run): the phone
streams its events, the guild chat gets its final answer, the treasury counts its cost.
The difference is the engine. Claude Code cannot drive a Grok model, so this module runs
its own small tool loop against https://api.x.ai/v1/chat/completions (OpenAI-compatible).

What a Grok member may do follows the run's permission mode, the same words as a quest:

  plan, manual           read: the hub (LP data, LP Call Bot, Chain), list and read files
  acceptEdits            + write and edit files inside its folders
  auto, dontAsk,         + run commands inside its folders (no shell, 120 s, a short deny list)
  bypassPermissions

Its folders are the member's own folder plus any folder the brief adds (a warden's program).
Paths are resolved before use, so `..` and symlinks cannot leave them. Secrets (.env, keys,
config.json) are never read, and the xAI key and the office token never reach a command.

Python 3 standard library only, like the rest of Agent Office.
"""
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import store

MODEL_KEY = "grok"
DEFAULT_MODEL = "grok-4"
DEFAULT_BASE = "https://api.x.ai/v1"
MAX_STEPS = 24
MAX_FILE = 200_000
MAX_TOOL_OUT = 20_000
COMMAND_TIMEOUT = 120
MEMORY_TURNS = 20
READ_MODES = ("plan", "manual")
WRITE_MODES = ("acceptEdits", "auto", "dontAsk", "bypassPermissions")
RUN_MODES = ("auto", "dontAsk", "bypassPermissions")
SECRET_NAMES = re.compile(r"(^\.env($|\.)|\.pem$|\.key$|^id_[a-z0-9]+$|^config\.json$|\.p12$|^credentials)", re.I)
DENY_COMMANDS = {"sudo", "su", "doas", "rm -rf /", "mkfs", "shutdown", "reboot", "halt", "launchctl", "dd",
                 "diskutil", "chown", "chmod -R 777", "curl", "wget", "ssh", "scp", "nc"}
HIDDEN_ENV = ("XAI_API_KEY", "AGENT_OFFICE_TOKEN", "ANTHROPIC_API_KEY")

SYSTEM = """You are {name}, a member of bojjifomo's guild in Agent Office, running on xAI's Grok.
Your role: {role}.

{instructions}

How you work:
- You have tools. Use them to look things up instead of guessing. The hub tools read live LP data
  (RH Wallet Finder, pools, Smart LP Finder), LP Call Bot status and program health.
- Your folders: {folders}. File tools only work inside them; paths may be relative to the first one.
- Permission mode: {mode}. {powers}
- Never ask questions back. If something is unclear, make the most reasonable assumption and say which.
- Answer bojjifomo in casual Indonesian unless the brief is in English. Keep chat answers short.
- End with your final answer as plain text, no tool call."""


class GrokError(RuntimeError):
    pass


def api_key(cfg):
    return (os.environ.get("XAI_API_KEY") or str((cfg or {}).get("xai_api_key") or "")).strip()


def ready(cfg):
    return bool(api_key(cfg))


def model_name(cfg):
    return str((cfg or {}).get("grok_model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _price(cfg, key, default):
    try:
        return max(0.0, float((cfg or {}).get(key, default)))
    except (TypeError, ValueError):
        return default


class Client:
    """One chat completion at a time. `base` can point at a fake server in tests."""

    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.base = str(self.cfg.get("xai_base_url") or DEFAULT_BASE).rstrip("/")
        self.model = model_name(self.cfg)

    def chat(self, messages, tools):
        key = api_key(self.cfg)
        if not key:
            raise GrokError("no xAI key: set XAI_API_KEY (or xai_api_key in config.json)")
        body = json.dumps({"model": self.model, "messages": messages, "tools": tools,
                           "tool_choice": "auto"}).encode()
        req = urllib.request.Request(self.base + "/chat/completions", data=body, method="POST", headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json"})
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    last = "xAI %d: %s" % (e.code, detail)
                    time.sleep(2 * (attempt + 1))
                    continue
                raise GrokError("xAI %d: %s" % (e.code, detail.replace(key, "***")))
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = "xAI unreachable: %s" % e
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
        raise GrokError(last or "xAI did not answer")


def _fn(name, description, props=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}}}


def tool_specs(mode, lp_tools):
    specs = [
        _fn("hub_lp", "Read LP data from RH Wallet Finder. tool is one of: " +
            "; ".join("%s (%s)" % (k, v) for k, v in lp_tools.items()) +
            ". params holds that tool's query parameters, e.g. {\"n\": 5, \"sort\": \"24h\"}.",
            {"tool": {"type": "string", "enum": list(lp_tools)}, "params": {"type": "object"}}, ["tool"]),
        _fn("hub_lpbot", "LP Call Bot: alive or not, open positions, follower count."),
        _fn("hub_chain", "Program health, open incidents and the situation report."),
        _fn("list_dir", "List a folder (one level).", {"path": {"type": "string"}}),
        _fn("read_file", "Read a text file. offset/limit are line numbers.",
            {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"]),
        _fn("search", "Find lines matching a regular expression in text files under a folder.",
            {"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]),
    ]
    if mode in WRITE_MODES:
        specs += [
            _fn("write_file", "Create or overwrite a text file.",
                {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            _fn("edit_file", "Replace one exact occurrence of old with new in a file.",
                {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                ["path", "old", "new"]),
        ]
    if mode in RUN_MODES:
        specs.append(_fn("run_command", "Run one command (no shell, no pipes) in your first folder, or in cwd "
                         "inside your folders. 120 s limit. Use it for tests, git status/diff/commit, python.",
                         {"command": {"type": "string"}, "cwd": {"type": "string"}}, ["command"]))
    return specs


def powers_text(mode):
    if mode in RUN_MODES:
        return "You may read and write files and run commands in your folders."
    if mode in WRITE_MODES:
        return "You may read and write files in your folders. Commands are off in this mode."
    return "Read only: no file changes, no commands."


class Tools:
    def __init__(self, roots, mode, gateway=None, chain=None):
        self.roots = [Path(r).resolve() for r in roots if r and os.path.isdir(r)]
        if not self.roots:
            raise GrokError("no working folder")
        self.mode = mode
        self.gateway = gateway
        self.chain = chain

    # paths
    def path(self, p, must_exist=True):
        p = str(p or ".").strip() or "."
        raw = Path(os.path.expanduser(p))
        full = (raw if raw.is_absolute() else self.roots[0] / raw).resolve()
        if not any(full == r or r in full.parents for r in self.roots):
            raise GrokError("outside your folders: %s" % p)
        if SECRET_NAMES.search(full.name):
            raise GrokError("that file holds secrets and stays closed")
        if must_exist and not full.exists():
            raise GrokError("no such path: %s" % p)
        return full

    def call(self, name, args):
        fn = getattr(self, "t_" + name, None)
        if not fn:
            raise GrokError("unknown tool %s" % name)
        if name in ("write_file", "edit_file") and self.mode not in WRITE_MODES:
            raise GrokError("file changes are off in %s mode" % self.mode)
        if name == "run_command" and self.mode not in RUN_MODES:
            raise GrokError("commands are off in %s mode" % self.mode)
        out = fn(**(args or {}))
        if not isinstance(out, str):
            out = json.dumps(out, default=str)
        if len(out) > MAX_TOOL_OUT:
            out = out[:MAX_TOOL_OUT] + "\n… [cut %d characters]" % (len(out) - MAX_TOOL_OUT)
        return out

    # hub
    def t_hub_lp(self, tool, params=None):
        if not self.gateway:
            raise GrokError("the hub is not available")
        q = {str(k): [str(v).lower() if isinstance(v, bool) else str(v)] for k, v in (params or {}).items()}
        return self.gateway.lp(tool, q)

    def t_hub_lpbot(self):
        if not self.gateway:
            raise GrokError("the hub is not available")
        return self.gateway.lpbot()

    def t_hub_chain(self):
        if not (self.gateway and self.chain):
            raise GrokError("the chain is not available")
        return self.gateway.chain_summary(self.chain)

    # files
    def t_list_dir(self, path="."):
        d = self.path(path)
        if not d.is_dir():
            raise GrokError("not a folder")
        rows = []
        for c in sorted(d.iterdir())[:300]:
            if c.name.startswith(".git") and c.is_dir():
                continue
            rows.append(c.name + ("/" if c.is_dir() else ""))
        return "\n".join(rows) or "(empty)"

    def t_read_file(self, path, offset=1, limit=400):
        f = self.path(path)
        if not f.is_file():
            raise GrokError("not a file")
        if f.stat().st_size > 5_000_000:
            raise GrokError("file too large")
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(offset or 1))
        end = min(len(lines), start - 1 + max(1, min(2000, int(limit or 400))))
        return "\n".join("%d\t%s" % (i, lines[i - 1]) for i in range(start, end + 1)) or "(empty)"

    def t_search(self, pattern, path="."):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise GrokError("bad pattern: %s" % e)
        base = self.path(path)
        hits = []
        files = [base] if base.is_file() else base.rglob("*")
        for f in files:
            if len(hits) >= 200:
                break
            if not f.is_file() or ".git" in f.parts or "node_modules" in f.parts or SECRET_NAMES.search(f.name):
                continue
            try:
                if f.stat().st_size > 1_000_000:
                    continue
                for n, line in enumerate(f.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
                    if rx.search(line):
                        hits.append("%s:%d: %s" % (f.relative_to(self.roots[0]) if self.roots[0] in f.parents else f, n, line[:200]))
                        if len(hits) >= 200:
                            break
            except (OSError, UnicodeDecodeError):
                continue
        return "\n".join(hits) or "(no matches)"

    def t_write_file(self, path, content):
        f = self.path(path, must_exist=False)
        if len(content) > MAX_FILE:
            raise GrokError("content too large")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        return "wrote %s (%d bytes)" % (path, len(content.encode()))

    def t_edit_file(self, path, old, new):
        f = self.path(path)
        text = f.read_text(encoding="utf-8")
        n = text.count(old) if old else 0
        if n != 1:
            raise GrokError("old must occur exactly once (found %d)" % n)
        f.write_text(text.replace(old, new, 1), encoding="utf-8")
        return "edited %s" % path

    def t_run_command(self, command, cwd="."):
        try:
            argv = shlex.split(str(command))
        except ValueError as e:
            raise GrokError("cannot parse command: %s" % e)
        if not argv:
            raise GrokError("empty command")
        joined = " ".join(argv)
        prog = os.path.basename(argv[0])
        if prog in DENY_COMMANDS or any(d in joined for d in DENY_COMMANDS if " " in d) \
                or (prog == "git" and "push" in argv and any(a in argv for a in ("-f", "--force", "--force-with-lease"))) \
                or (prog == "rm" and any(a.startswith("-") and "r" in a for a in argv[1:]) and any(a in ("/", "~", "..") for a in argv[1:])):
            raise GrokError("that command is not allowed for a Grok member")
        where = self.path(cwd)
        if not where.is_dir():
            raise GrokError("cwd is not a folder")
        env = {k: v for k, v in os.environ.items() if k not in HIDDEN_ENV}
        env.update(NO_COLOR="1", TERM="dumb")
        try:
            p = subprocess.run(argv, cwd=str(where), capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
                               env=env, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise GrokError("no such program: %s" % argv[0])
        except subprocess.TimeoutExpired:
            return "[stopped after %ds]" % COMMAND_TIMEOUT
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if (p.stderr or "").strip() else "")
        return (out.strip() or "(no output)") + ("\n[exit code %d]" % p.returncode if p.returncode else "")


def summary(name, args):
    args = args or {}
    if name == "hub_lp":
        return "📊 hub %s %s" % (args.get("tool", ""), json.dumps(args.get("params") or {}))[:160]
    if name == "run_command":
        return "$ " + str(args.get("command", ""))[:160]
    if name in ("read_file", "write_file", "edit_file", "list_dir"):
        return "%s %s" % (name.replace("_", " "), args.get("path", "."))
    return name.replace("_", " ")


class Memory:
    """The last few chat turns per member folder, so Grokbot remembers the conversation."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = store.load("grok_memory.json", {})
        if not isinstance(self.data, dict):
            self.data = {}

    def get(self, key):
        with self.lock:
            return list(self.data.get(key, []))

    def add(self, key, user, answer):
        with self.lock:
            turns = self.data.setdefault(key, [])
            turns += [{"role": "user", "content": user[:4000]}, {"role": "assistant", "content": answer[:4000]}]
            self.data[key] = turns[-MEMORY_TURNS * 2:]
            store.save("grok_memory.json", self.data)

    def forget(self, key):
        with self.lock:
            self.data.pop(key, None)
            store.save("grok_memory.json", self.data)


class Engine:
    """Runs one Grok run on a thread. `run` is a runner.Run; `finish(run)` is the runner's own ending."""

    def __init__(self, cfg, office=None):
        self.cfg = cfg or {}
        self.office = office
        self.memory = Memory()
        self.spend_lock = threading.Lock()

    def spent_today(self):
        with self.spend_lock:
            return float(store.load("grok_spend.json", {}).get(time.strftime("%Y-%m-%d"), 0.0))

    def _spend(self, dollars):
        if not dollars:
            return
        with self.spend_lock:
            data = store.load("grok_spend.json", {})
            day = time.strftime("%Y-%m-%d")
            data = {k: v for k, v in data.items() if k >= time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))}
            data[day] = round(float(data.get(day, 0.0)) + dollars, 6)
            store.save("grok_spend.json", data)

    def member_for(self, cwd):
        team = getattr(self.office, "team", None)
        if team:
            m = team.member_for_cwd(cwd)
            if m:
                return m
        return {"name": "Grok", "role": "assistant", "instructions": ""}

    def start(self, run, add_dirs, finish):
        run.cancelled = False
        threading.Thread(target=self._go, args=(run, add_dirs, finish), daemon=True).start()

    def _go(self, run, add_dirs, finish):
        try:
            self._loop(run, add_dirs)
        except Exception as e:  # anything: the run must end and say why
            with run.cond:
                run.ok = False
                run.events.append({"k": "error", "t": str(e)[:500]})
                run._changed()
        try:
            self._spend(float(run.cost or 0))
        except (OSError, ValueError, TypeError):
            pass
        with run.cond:
            if run.ok is None:
                run.ok = False
            run.done = True
            run.ended = time.time()
            run._changed()
        finish(run)

    def _loop(self, run, add_dirs):
        member = self.member_for(run.cwd)
        gateway = getattr(self.office, "gateway", None)
        chain = getattr(self.office, "chain", None)
        tools = Tools([run.cwd] + [d for d in add_dirs or () if d], run.mode, gateway, chain)
        lp_tools = {}
        if gateway:
            import gateway as gw
            lp_tools = {k: v[0] for k, v in gw.LP_TOOLS.items()}
        specs = tool_specs(run.mode, lp_tools or {"status": "sync state"})
        system = SYSTEM.format(name=member.get("name", "Grok"), role=member.get("role", "assistant"),
                               instructions=member.get("instructions") or "", mode=run.mode,
                               folders=", ".join(str(r) for r in tools.roots), powers=powers_text(run.mode))
        if getattr(run, "system", ""):
            system += "\n\n" + run.system
        messages = [{"role": "system", "content": system}] + self.memory.get(run.cwd) + \
                   [{"role": "user", "content": run.text}]
        client = Client(self.cfg)
        p_in, p_out = _price(self.cfg, "grok_price_in", 3.0), _price(self.cfg, "grok_price_out", 15.0)
        cost = 0.0
        steps = int(self.cfg.get("grok_max_steps") or MAX_STEPS)
        final = ""
        for _ in range(max(1, min(60, steps))):
            if run.cancelled:
                raise GrokError("Stopped from the phone.")
            reply = client.chat(messages, specs)
            usage = reply.get("usage") or {}
            cost += (usage.get("prompt_tokens", 0) * p_in + usage.get("completion_tokens", 0) * p_out) / 1e6
            with run.cond:
                run.cost = round(cost, 6)
            msg = ((reply.get("choices") or [{}])[0].get("message")) or {}
            text = msg.get("content") or ""
            calls = msg.get("tool_calls") or []
            entry = {"role": "assistant", "content": text}
            if calls:
                entry["tool_calls"] = calls
            messages.append(entry)
            if text.strip():
                run.add("text", text)
            if not calls:
                final = text
                break
            for c in calls:
                fn = c.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError
                except ValueError:
                    args, out = {}, "error: arguments were not a JSON object"
                else:
                    run.add("tool", summary(name, args))
                    try:
                        out = tools.call(name, args)
                    except (GrokError, LookupError, ValueError, RuntimeError, OSError, TypeError) as e:
                        out = "error: %s" % e
                messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": out})
        else:
            final = final or "Stopped after %d steps without a final answer." % steps
            run.add("error", final)
            with run.cond:
                run.final, run.ok = final, False
            return
        with run.cond:
            run.final = final
            run.ok = True
            run._changed()
        self.memory.add(run.cwd, run.text, final)
