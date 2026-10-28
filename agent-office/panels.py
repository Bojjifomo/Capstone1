"""Optional panels: Treasury (usage), Familiars (launchd bots), Vault (disk), Scrying (search), Ledger."""
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import sessions
import store


# ---- Treasury: tokens and estimated cost -----------------------------------

def usage(office, days=14):
    office.scanner.refresh()
    today = datetime.now().date()
    keys = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    per_day = {k: {"day": k, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0} for k in keys}
    per_model, per_session = {}, []
    with office.scanner.lock:
        transcripts = list(office.scanner.transcripts.values())
    for t in transcripts:
        total = 0.0
        for day, models in t.usage.items():
            for model, (i, o, cr, cw, cost) in models.items():
                total += cost
                if day in per_day:
                    row = per_day[day]
                    row["input"] += i
                    row["output"] += o
                    row["cache_read"] += cr
                    row["cache_write"] += cw
                    row["cost"] += cost
                name = sessions.short_model(model) or model or "other"
                m = per_model.setdefault(name, {"model": name, "tokens": 0, "cost": 0.0})
                m["tokens"] += i + o + cr + cw
                m["cost"] += cost
        if total:
            staff = office.team.staff_for_session(t)
            per_session.append({"id": t.id, "name": staff["name"] if staff else office.name_of(t),
                                "project": Path(t.cwd).name if t.cwd else "", "cost": round(total, 4),
                                "today": round(sum(r[4] for r in t.usage.get(keys[-1], {}).values()), 4)})
    per_session.sort(key=lambda s: -s["cost"])
    rows = [dict(r, cost=round(r["cost"], 4)) for r in per_day.values()]
    return {"days": rows, "today": rows[-1]["cost"], "week": round(sum(r["cost"] for r in rows[-7:]), 4),
            "models": sorted(per_model.values(), key=lambda m: -m["cost"]), "sessions": per_session[:20],
            "note": "Estimated from transcript token counts at API list prices; a subscription bills differently."}


def cost_today(office):
    key = datetime.now().strftime("%Y-%m-%d")
    with office.scanner.lock:
        return round(sum(r[4] for t in office.scanner.transcripts.values() for r in t.usage.get(key, {}).values()), 4)


# ---- Familiars: launchd jobs -------------------------------------------------

def launch_agents_dir():
    return Path.home() / "Library" / "LaunchAgents"


def _id(label):
    return hashlib.sha1(label.encode()).hexdigest()[:10]


def _tail_error(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if re.search(r"error|exception|traceback|failed|fatal", line, re.I):
            return line.strip()[:300]
    return ""


def _schedule(p):
    if p.get("StartInterval"):
        mins = int(p["StartInterval"]) // 60
        return "every %d min" % mins if mins < 120 else "every %d h" % (mins // 60)
    cal = p.get("StartCalendarInterval")
    if cal:
        entries = cal if isinstance(cal, list) else [cal]
        first = entries[0]
        when = "%02d:%02d" % (first.get("Hour", 0), first.get("Minute", 0))
        return "daily at %s" % when + (" (+%d more)" % (len(entries) - 1) if len(entries) > 1 else "")
    if p.get("KeepAlive"):
        return "always on"
    if p.get("RunAtLoad"):
        return "at login"
    return "on demand"


class Familiars:
    def __init__(self, cfg):
        self.launchctl = cfg.get("launchctl_bin") or shutil.which("launchctl") or "/bin/launchctl"

    def _listing(self):
        try:
            out = subprocess.run([self.launchctl, "list"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        jobs = {}
        for line in out.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) == 3:
                jobs[parts[2]] = {"pid": parts[0] if parts[0] != "-" else "", "status": parts[1]}
        return jobs

    def jobs(self):
        listing = self._listing()
        out = []
        for plist in sorted(launch_agents_dir().glob("*.plist")):
            try:
                with open(plist, "rb") as f:
                    p = plistlib.load(f)
            except Exception:
                continue
            label = p.get("Label") or plist.stem
            state = listing.get(label)
            logs = [x for x in (p.get("StandardOutPath"), p.get("StandardErrorPath")) if x]
            last_run = max((os.path.getmtime(x) for x in logs if os.path.exists(x)), default=0)
            status = state["status"] if state else ""
            error = _tail_error(p["StandardErrorPath"]) if p.get("StandardErrorPath") else ""
            program = p.get("Program") or " ".join(p.get("ProgramArguments", [])[:3])
            out.append({
                "id": _id(label), "label": label, "loaded": bool(state), "running": bool(state and state["pid"]),
                "exit": status, "failed": bool(status and status not in ("0", "-")), "last_run": last_run,
                "schedule": _schedule(p), "program": os.path.basename(str(program).split(" ")[0]) + (" …" if " " in str(program) else ""),
                "error": error, "ours": label.endswith("agent-office"),
            })
        out.sort(key=lambda j: (not j["failed"], not j["running"], j["label"]))
        return out

    def run_now(self, jid):
        for job in self.jobs():
            if job["id"] == jid:
                if job["ours"]:
                    raise RuntimeError("restart the guild hall from the Mac, not from inside itself")
                target = "gui/%d/%s" % (os.getuid(), job["label"])
                r = subprocess.run([self.launchctl, "kickstart", target], capture_output=True, text=True, timeout=10)
                if r.returncode:
                    raise RuntimeError((r.stderr or r.stdout or "launchctl failed").strip()[:200])
                return job["label"]
        raise LookupError("no such job")


# ---- Vault: disk space, big folders, fixed cleaners --------------------------

BIG_FOLDERS = ["Library/Caches", "Library/Developer/Xcode/DerivedData", "Library/Developer/CoreSimulator",
               "Library/Developer/Xcode/iOS DeviceSupport", "Library/Application Support", "Library/Containers",
               ".npm", ".cache", ".cargo/registry", ".gradle", ".m2", ".Trash", "Downloads", "Movies",
               "Library/Caches/Homebrew", ".claude/projects", "Agents", "Projects"]

# id -> (title, what it does, folder it empties OR fixed command). Nothing else can ever run.
CLEANERS = {
    "xcode": ("Xcode DerivedData", "Deletes build products; Xcode rebuilds them.", "Library/Developer/Xcode/DerivedData", None),
    "simulators": ("Old iOS simulators", "Removes simulators for runtimes you no longer have.", None,
                   ["xcrun", "simctl", "delete", "unavailable"]),
    "npm": ("npm cache", "Clears the npm download cache.", None, ["npm", "cache", "clean", "--force"]),
    "brew": ("Homebrew leftovers", "Removes old versions and the download cache.", None, ["brew", "cleanup", "-s", "--prune=all"]),
    "pip": ("pip cache", "Clears pip's wheel cache.", None, ["python3", "-m", "pip", "cache", "purge"]),
    "trash": ("Empty the Trash", "Permanently deletes what is in ~/.Trash.", ".Trash", None),
}


def du_kb(path):
    try:
        out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=120).stdout
        return int(out.split()[0]) if out.strip() else 0
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 0


def empty_folder(folder):
    """Delete the contents of one fixed folder, never following links out of it."""
    freed = 0
    if not folder.is_dir() or folder.is_symlink():
        return 0
    for child in folder.iterdir():
        try:
            if child.is_symlink() or child.is_file():
                freed += child.lstat().st_size
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        except OSError:
            pass
    return freed


class Vault:
    def __init__(self):
        self.sizes = {}
        self.scanned = 0.0
        self.scanning = False
        self.lock = threading.Lock()
        self.last_clean = {}

    def free(self):
        u = shutil.disk_usage(str(Path.home()))
        return {"free": u.free, "total": u.total}

    def _scan(self):
        home = Path.home()
        sizes = {}
        for rel in BIG_FOLDERS:
            p = home / rel
            if p.exists() and not p.is_symlink():
                sizes[rel] = du_kb(p) * 1024
        with self.lock:
            self.sizes = sizes
            self.scanned = time.time()
            self.scanning = False

    def report(self, rescan=False):
        with self.lock:
            stale = time.time() - self.scanned > 600
            if (stale or rescan) and not self.scanning:
                self.scanning = True
                threading.Thread(target=self._scan, daemon=True).start()
            sizes = dict(self.sizes)
        folders = sorted(({"path": "~/" + k, "bytes": v} for k, v in sizes.items() if v), key=lambda f: -f["bytes"])
        cleaners = []
        for cid, (title, what, folder, cmd) in CLEANERS.items():
            ok = bool(folder) or bool(shutil.which(cmd[0]))
            cleaners.append({"id": cid, "title": title, "what": what, "available": ok,
                             "bytes": sizes.get(folder, 0) if folder else None, "last": self.last_clean.get(cid)})
        return dict(self.free(), folders=folders[:14], cleaners=cleaners, scanning=self.scanning,
                    scanned=self.scanned)

    def clean(self, cid):
        if cid not in CLEANERS:
            raise LookupError("no such cleaner")
        title, _, folder, cmd = CLEANERS[cid]
        before = self.free()["free"]
        if folder:
            empty_folder(Path.home() / folder)
        else:
            exe = shutil.which(cmd[0])
            if not exe:
                raise RuntimeError("%s is not installed" % cmd[0])
            r = subprocess.run([exe] + cmd[1:], capture_output=True, text=True, timeout=900)
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or "the cleaner failed")
        freed = max(0, self.free()["free"] - before)
        self.last_clean[cid] = {"ts": time.time(), "freed": freed}
        self.report(rescan=True)
        return {"title": title, "freed": freed}


# ---- Scrying: search across every transcript -----------------------------------

def _texts(entry):
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        yield sessions.clean_user_text(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                yield sessions.clean_user_text(block.get("text", "")) if entry.get("type") == "user" else block.get("text", "")


def search(office, query, limit=40, budget=4.0):
    q = " ".join(str(query).split())[:200]
    if len(q) < 2:
        return {"results": [], "query": q}
    needle = q.lower()
    started = time.time()
    files = []
    root = sessions.projects_dir()
    for folder in root.iterdir() if root.exists() else []:
        if folder.is_dir():
            files += [f for f in folder.glob("*.jsonl")]
    files.sort(key=lambda f: -f.stat().st_mtime)
    results, truncated = [], False
    for f in files:
        if len(results) >= limit or time.time() - started > budget:
            truncated = True
            break
        hits = 0
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if needle not in line.lower():
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("type") not in ("user", "assistant") or entry.get("isSidechain"):
                        continue
                    for text in _texts(entry):
                        i = text.lower().find(needle)
                        if i < 0:
                            continue
                        start = max(0, i - 70)
                        results.append({"id": f.stem, "role": entry["type"], "ts": sessions.parse_ts(entry.get("timestamp")),
                                        "before": ("…" if start else "") + text[start:i], "match": text[i:i + len(q)],
                                        "after": text[i + len(q):i + len(q) + 90]})
                        hits += 1
                        break
                    if hits >= 3 or len(results) >= limit:
                        break
        except OSError:
            continue
    names = {}
    for r in results:
        if r["id"] not in names:
            t = office.transcript(r["id"])
            names[r["id"]] = (office.name_of(t), Path(t.cwd).name if t.cwd else "") if t else (r["id"][:8], "")
        r["name"], r["project"] = names[r["id"]]
    return {"results": results, "query": q, "truncated": truncated}


# ---- Ledger: what was done from the phone ---------------------------------------

_ledger_origin = threading.local()


def set_ledger_origin(origin):
    """Mark ledger lines written on this thread. Pass "" to clear it when the request ends."""
    _ledger_origin.value = origin or None


def current_ledger_origin():
    return getattr(_ledger_origin, "value", None) or None


class Ledger:
    MAX_LINES = 2000

    def __init__(self):
        self.path = store.path("ledger.jsonl")
        self.lock = threading.Lock()

    def add(self, kind, text, **extra):
        origin = extra.get("origin") or current_ledger_origin()
        if origin:
            extra["origin"] = origin
        line = json.dumps(dict(extra, ts=time.time(), kind=kind, text=str(text)[:300])) + "\n"
        with self.lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
            if self.path.stat().st_size > 600_000:  # keep the newest lines, rewritten atomically
                lines = self.path.read_text(encoding="utf-8").splitlines()[-self.MAX_LINES // 2:]
                tmp = self.path.with_name(".ledger.tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.replace(tmp, self.path)

    def recent(self, n=60):
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out
