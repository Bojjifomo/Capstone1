"""Quill's errand: `python3 -m orchestrator brief`, read-only, never `--yes`.

The guild member lives in team.roster. This module only runs the process and
keeps the latest text in orchestrator.json (gitignored, next to the other state).
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import store

HERE = Path(__file__).resolve().parent
ORCH_ID = "b71ef001"
DEFAULT_NAME = "Quill"


def orchestrator_root(cfg):
    explicit = str((cfg or {}).get("orchestrator_root") or "").strip()
    if explicit:
        root = Path(explicit).expanduser()
        if (root / "orchestrator" / "__main__.py").is_file():
            return root
        return None
    sibling = HERE.parent
    if (sibling / "orchestrator" / "__main__.py").is_file():
        return sibling
    return None


def _timeout(cfg):
    try:
        return min(3600.0, max(5.0, float((cfg or {}).get("orchestrator_timeout") or 600)))
    except (TypeError, ValueError):
        return 600.0


class Briefing:
    def __init__(self, cfg, on_event=None):
        self.cfg = cfg or {}
        self.on_event = on_event
        self.lock = threading.Lock()
        self.proc = None
        saved = store.load("orchestrator.json", {})
        if not isinstance(saved, dict):
            saved = {}
        self.state = {
            "running": False,
            "text": str(saved.get("text") or "")[-20000:],
            "started": saved.get("started") or 0,
            "ended": saved.get("ended") or 0,
            "ok": saved.get("ok"),
            "error": str(saved.get("error") or ""),
            "name": self.name,
        }
        # a crash mid-brief must not come back as "still writing"
        if saved.get("running"):
            self.state["error"] = self.state["error"] or "the brief was interrupted when the guild hall restarted"

    @property
    def name(self):
        name = " ".join(str(self.cfg.get("orchestrator_name") or DEFAULT_NAME).split())[:40]
        return name or DEFAULT_NAME

    def public(self):
        with self.lock:
            data = dict(self.state)
        data["name"] = self.name
        data["id"] = ORCH_ID
        data["text"] = data.get("text") or ""
        return data

    def save(self):
        with self.lock:
            payload = dict(self.state)
        payload["running"] = bool(payload.get("running"))
        store.save("orchestrator.json", payload)

    def command(self):
        """Argv for the brief. Read-only env, and `--yes` is refused."""
        env = dict(os.environ)
        env["ORCHESTRATOR_READ_ONLY"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        custom = self.cfg.get("orchestrator_cmd")
        if custom:
            if isinstance(custom, str) or not isinstance(custom, (list, tuple)) or not custom:
                raise RuntimeError("orchestrator_cmd must be a list of arguments")
            argv = [str(part) for part in custom]
            cwd = None
        else:
            root = orchestrator_root(self.cfg)
            if root is None:
                raise RuntimeError("the orchestrator is not installed; set orchestrator_root in config.json")
            argv = [sys.executable, "-m", "orchestrator", "brief"]
            cwd = str(root)
        if "--yes" in argv:
            raise RuntimeError("the guild never runs the orchestrator with --yes")
        return argv, cwd, env

    def start(self):
        with self.lock:
            if self.state.get("running"):
                raise RuntimeError("a brief is already being written")
        argv, cwd, env = self.command()
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        except OSError as exc:
            with self.lock:
                self.state.update(running=False, ok=False, error=str(exc)[:300], ended=time.time())
            self.save()
            raise RuntimeError("could not start the orchestrator")
        with self.lock:
            self.state.update(running=True, started=time.time(), ended=0, ok=None, error="", text="", name=self.name)
            self.proc = proc
        self.save()
        threading.Thread(target=self._read, args=(proc,), daemon=True, name="brief").start()
        threading.Thread(target=self._kill_later, args=(proc,), daemon=True, name="brief-timeout").start()
        self._notify("started")
        return {"briefing": True, "started": True, "name": self.name, "id": ORCH_ID}

    def _notify(self, phase):
        if not self.on_event:
            return
        try:
            self.on_event(phase, self.public())
        except Exception:
            pass

    def _kill_later(self, proc):
        try:
            proc.wait(timeout=_timeout(self.cfg))
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            with self.lock:
                if self.proc is proc and not self.state.get("error"):
                    self.state["error"] = "the brief was stopped after %d seconds" % int(_timeout(self.cfg))

    def _read(self, proc):
        chunks = []
        try:
            for line in proc.stdout:
                chunks.append(line)
                with self.lock:
                    if self.proc is proc:
                        self.state["text"] = "".join(chunks)[-20000:]
            code = proc.wait()
        except Exception as exc:
            code = 1
            with self.lock:
                self.state["error"] = self.state.get("error") or str(exc)[:300]
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
        with self.lock:
            if self.proc is not proc:
                return
            self.state["running"] = False
            self.state["ended"] = time.time()
            self.state["text"] = "".join(chunks)[-20000:]
            self.state["ok"] = code == 0 and not self.state.get("error")
            if code and not self.state.get("error"):
                self.state["error"] = "the brief stopped (exit %s)" % code
            self.proc = None
        self.save()
        self._notify("finished")
