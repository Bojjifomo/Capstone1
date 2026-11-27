"""Claude plan limits: how much of the 5-hour session and the week is used, and when each resets.

Claude Code keeps the OAuth token in the macOS keychain ("Claude Code-credentials") or in
~/.claude/.credentials.json. The usage endpoint answers with a block per window:

    {"five_hour": {"utilization": 23.0, "resets_at": "2026-11-11T14:00:00Z"},
     "seven_day": {"utilization": 80.0, "resets_at": "..."}, "seven_day_opus": {...}, ...}

The token never leaves this module and is never written to a log or an answer. Results are cached for a
few minutes; a failure keeps the last good answer and says why the refresh failed.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_SECONDS = 300
WINDOWS = (("five_hour", "Session", "5 h"), ("seven_day", "Week", "7 d"),
           ("seven_day_opus", "Opus week", "7 d"), ("seven_day_sonnet", "Sonnet week", "7 d"))

_state = {"at": 0, "data": None, "error": "", "checking": False}
_lock = threading.Lock()


def token_from_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    return str((oauth or {}).get("accessToken") or "")


def token_from_keychain():
    if sys.platform != "darwin":
        return ""
    try:
        p = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if p.returncode or not p.stdout.strip():
        return ""
    try:
        data = json.loads(p.stdout.strip())
    except ValueError:
        return ""
    return str(((data.get("claudeAiOauth") or {}) if isinstance(data, dict) else {}).get("accessToken") or "")


def token(cfg=None):
    cfg = cfg or {}
    return token_from_file(os.path.expanduser(cfg.get("credentials_file") or "~/.claude/.credentials.json")) \
        or token_from_keychain()


def parse(payload):
    """The windows in a usage answer, in display order, with utilization 0..100 and the reset time."""
    out = []
    if not isinstance(payload, dict):
        return out
    for key, label, span in WINDOWS:
        block = payload.get(key)
        if not isinstance(block, dict) or block.get("utilization") is None:
            continue
        try:
            pct = max(0.0, min(100.0, float(block.get("utilization"))))
        except (TypeError, ValueError):
            continue
        resets = block.get("resets_at")
        ts = None
        if isinstance(resets, str):
            try:
                ts = datetime.fromisoformat(resets.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = None
        elif isinstance(resets, (int, float)):
            ts = float(resets)
        out.append({"key": key, "label": label, "span": span, "pct": round(pct, 1), "resets": ts})
    return out


def fetch(cfg=None, timeout=8):
    """One request to the usage endpoint. Raises on any failure; never includes the token in the error."""
    cfg = cfg or {}
    tok = token(cfg)
    if not tok:
        raise RuntimeError("no Claude login found on this Mac")
    req = urllib.request.Request(cfg.get("limits_url") or USAGE_URL, headers={
        "Authorization": "Bearer " + tok, "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "agent-office", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            payload = json.loads(res.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("usage endpoint answered %d" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError("usage endpoint unreachable: %s" % str(getattr(e, "reason", e))[:80])
    return parse(payload)


def snapshot(cfg=None, force=False):
    """The cached windows, refreshing in the background when stale. Safe to call on every poll."""
    cfg = cfg or {}
    if cfg.get("limits") is False:
        return {"on": False, "windows": [], "checked": 0, "error": ""}
    with _lock:
        stale = force or time.time() - _state["at"] > CACHE_SECONDS
        start = stale and not _state["checking"]
        if start:
            _state["checking"] = True
        public = {"on": True, "windows": list(_state["data"] or []), "checked": _state["at"], "error": _state["error"],
                  "checking": _state["checking"]}
    if start:
        threading.Thread(target=_refresh, args=(cfg,), daemon=True, name="limits").start()
    return public


def _refresh(cfg):
    try:
        data = fetch(cfg)
        with _lock:
            _state.update(data=data, error="", at=time.time())
    except Exception as e:  # noqa: BLE001 - the header shows the reason
        with _lock:
            _state.update(error=str(e)[:160], at=time.time())
    finally:
        with _lock:
            _state["checking"] = False


def text(snap):
    if not snap.get("on"):
        return "limits: off"
    if not snap["windows"]:
        return "limits: " + (snap.get("error") or "not checked yet")
    parts = []
    for w in snap["windows"]:
        when = ""
        if w.get("resets"):
            when = " (resets %s)" % datetime.fromtimestamp(w["resets"], timezone.utc).astimezone().strftime("%a %H:%M")
        parts.append("%s %d%%%s" % (w["label"], round(w["pct"]), when))
    return "limits: " + ", ".join(parts)
