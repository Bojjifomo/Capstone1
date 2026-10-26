"""Runs `claude -p` for messages sent from the phone: one run per session, plus a queue.

The browser only ever sends ids and text. This module builds the argv itself and never
uses a shell.
"""
import json
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import sessions
import store

MODES = ("acceptEdits", "auto", "plan", "manual", "dontAsk", "bypassPermissions")
RISKY_MODES = {"bypassPermissions"}
MODELS = {"": None, "opus": "opus", "sonnet": "sonnet", "haiku": "haiku"}
MAX_TEXT = 20000
KEEP_RUNS = 60


def find_claude(cfg):
    home = Path.home()
    for c in (cfg.get("claude_bin"), shutil.which("claude"), home / ".local/bin/claude",
              home / ".claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if c and os.path.isfile(str(c)) and os.access(str(c), os.X_OK):
            return str(c)
    return None


class Run:
    def __init__(self, sid, cwd, text, mode, origin, helper):
        self.id = secrets.token_hex(6)
        self.sid = sid
        self.cwd = cwd
        self.text = text
        self.mode = mode
        self.origin = origin
        self.helper = helper
        self.events = []
        self.v = 0
        self.done = False
        self.ok = None
        self.cost = None
        self.final = ""
        self.started = time.time()
        self.ended = None
        self.proc = None
        self.stderr = deque(maxlen=30)
        self.streamed = set()   # message ids whose text already arrived as deltas
        self.current = ""       # id of the message being streamed
        self.cond = threading.Condition()

    def _changed(self):
        self.v += 1
        self.cond.notify_all()

    def add(self, kind, text):
        with self.cond:
            self.events.append({"k": kind, "t": text})
            self._changed()

    def delta(self, text):
        with self.cond:
            if self.current not in self.streamed or not self.events or self.events[-1]["k"] != "text":
                self.streamed.add(self.current)
                self.events.append({"k": "text", "t": ""})
            self.events[-1]["t"] += text
            self._changed()

    def public(self):
        with self.cond:
            return {"id": self.id, "sid": self.sid, "events": [dict(e) for e in self.events], "v": self.v,
                    "done": self.done, "ok": self.ok, "cost": self.cost, "started": self.started,
                    "ended": self.ended, "text": self.text[:400], "mode": self.mode, "origin": self.origin}


class Runner:
    def __init__(self, cfg, on_finish=None):
        self.cfg = cfg
        self.on_finish = on_finish
        self.runs = {}
        self.by_sid = {}
        self.queues = store.load("queue.json", {})
        self.helpers = set(store.load("helpers.json", []))
        self.lock = threading.RLock()

    # ---- queue ----------------------------------------------------------
    def queue_of(self, sid):
        return self.queues.get(sid, [])

    def _save_queue(self):
        self.queues = {k: v for k, v in self.queues.items() if v}
        store.save("queue.json", self.queues)

    def enqueue(self, sid, text, mode, model=""):
        item = {"id": secrets.token_hex(4), "text": text, "mode": mode, "model": model, "ts": time.time()}
        with self.lock:
            self.queues.setdefault(sid, []).append(item)
            self._save_queue()
        return item

    def take(self, sid, qid=None):
        with self.lock:
            items = self.queues.get(sid, [])
            for i, item in enumerate(items):
                if qid is None or item["id"] == qid:
                    items.pop(i)
                    self._save_queue()
                    return item
        return None

    def put_back(self, sid, item):
        with self.lock:
            self.queues.setdefault(sid, []).insert(0, item)
            self._save_queue()

    def to_front(self, sid, qid):
        with self.lock:
            item = self.take(sid, qid)
            if item:
                self.queues.setdefault(sid, []).insert(0, item)
                self._save_queue()
            return item

    # ---- runs -----------------------------------------------------------
    def active(self, sid):
        rid = self.by_sid.get(sid)
        return bool(rid and rid in self.runs and not self.runs[rid].done)

    def latest(self, sid):
        rid = self.by_sid.get(sid)
        return self.runs.get(rid) if rid else None

    def is_helper(self, sid):
        return sid in self.helpers

    def mark_helper(self, sid):
        with self.lock:
            self.helpers.add(sid)
            store.save("helpers.json", sorted(self.helpers)[-500:])

    def start(self, sid, cwd, text, mode, new=False, model="", system="", origin="phone", helper=False):
        claude = find_claude(self.cfg)
        if not claude:
            raise RuntimeError("claude was not found; set claude_bin in config.json")
        if mode not in MODES:
            raise ValueError("unknown permission mode")
        if model not in MODELS:
            raise ValueError("unknown model")
        if not cwd or not os.path.isdir(cwd):
            raise RuntimeError("the project folder is gone: %s" % cwd)
        argv = [claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--permission-mode", mode]
        argv += ["--session-id", sid] if new else ["--resume", sid]
        if MODELS[model]:
            argv += ["--model", MODELS[model]]
        if system:
            argv += ["--append-system-prompt", system]
        run = Run(sid, cwd, text, mode, origin, helper)
        if helper:
            self.mark_helper(sid)
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([os.path.dirname(claude), env.get("PATH", "/usr/bin:/bin")])
        with self.lock:
            if self.active(sid):
                raise RuntimeError("this session is already answering")
            run.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True)
            self.runs[run.id] = run
            self.by_sid[sid] = run.id
            self._trim()
        message = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        try:
            run.proc.stdin.write((json.dumps(message) + "\n").encode())
            run.proc.stdin.close()
        except OSError:
            pass
        threading.Thread(target=self._read_stderr, args=(run,), daemon=True).start()
        threading.Thread(target=self._read, args=(run,), daemon=True).start()
        return run

    def _trim(self):
        done = sorted((r for r in self.runs.values() if r.done), key=lambda r: r.started)
        for r in done[:max(0, len(self.runs) - KEEP_RUNS)]:
            self.runs.pop(r.id, None)

    def _read_stderr(self, run):
        for line in run.proc.stderr:
            run.stderr.append(line.decode("utf-8", "replace").rstrip())

    def _read(self, run):
        for raw in run.proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self._handle(run, msg)
        code = run.proc.wait()
        with run.cond:
            if run.ok is None:
                run.ok = False
                tail = "\n".join(list(run.stderr)[-6:]) or "exit code %s" % code
                run.events.append({"k": "error", "t": tail})
            run.done = True
            run.ended = time.time()
            run._changed()
        if self.on_finish:
            try:
                self.on_finish(run)
            except Exception:  # a failed notification must not kill the reader
                pass

    def _handle(self, run, msg):
        kind = msg.get("type")
        if kind == "stream_event":
            ev = msg.get("event") or {}
            if ev.get("type") == "message_start":
                run.current = (ev.get("message") or {}).get("id", "")
            elif ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                run.delta(ev["delta"].get("text", ""))
        elif kind == "assistant":
            m = msg.get("message") or {}
            for block in m.get("content") or []:
                if block.get("type") == "text" and m.get("id") not in run.streamed and block.get("text", "").strip():
                    run.add("text", block["text"])
                elif block.get("type") == "tool_use":
                    run.add("tool", sessions.tool_summary(block.get("name", "?"), block.get("input")))
        elif kind == "result":
            with run.cond:
                run.ok = not msg.get("is_error") and msg.get("subtype", "success") == "success"
                run.cost = msg.get("total_cost_usd")
                run.final = str(msg.get("result") or "")
                if not run.ok:
                    run.events.append({"k": "error", "t": run.final or msg.get("subtype", "error")})
                run._changed()

    def stop(self, rid):
        run = self.runs.get(rid)
        if not run or run.done or not run.proc:
            return False
        try:
            os.killpg(run.proc.pid, signal.SIGINT)
        except OSError:
            pass

        def harder():
            time.sleep(5)
            if not run.done:
                try:
                    os.killpg(run.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        threading.Thread(target=harder, daemon=True).start()
        run.add("error", "Stopped from the phone.")
        return True

    def wait(self, rid, after_v, timeout=20):
        run = self.runs.get(rid)
        if not run:
            return None
        with run.cond:
            run.cond.wait_for(lambda: run.v > after_v or run.done, timeout=timeout)
        return run.public()
