"""RPC meter: every bot counts its own RPC requests here, the RPC Monitor adds them up.

Standard library only, never raises, never blocks the bot. Each process keeps the counts in
memory and appends one line per minute per (bot, RPC) to

    ~/.rpc-monitor/usage-YYYY-MM-DD.jsonl      (UTC day; RPC_MONITOR_DIR moves it)

    {"t": 1794300000, "bot": "lp_call_bot", "ep": "rpc.solidrpc.io#3f9a0c1b2d", "host": "rpc.solidrpc.io",
     "ok": 41, "limited": 2, "quota": 0, "error": 0}

The RPC link itself (it holds the API key) is never written: `ep` is the host plus a short hash
of the link, so two bots on the same key land on the same line in the monitor.

    hit("lp_call_bot", url)                 one request that got an answer
    hit("lp_call_bot", url, "limited")      HTTP 429 / "rate limit" reply
    hit("lp_call_bot", url, "quota")        plan used up (monthly credits / compute units)
    hit("lp_call_bot", url, "error", n=3)   timeouts, 5xx, broken connections

This same file is copied into lp_exit_watcher (rpcmeter.py). Keep the two identical.
"""
import atexit
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

OUTCOMES = ("ok", "limited", "quota", "error")
FLUSH_S = 15
IN_TESTS = None  # None: off while unittest/pytest is loaded, so test runs never land in the real counts

_lock = threading.Lock()
_buf = {}            # (minute, bot, ep, host) -> {outcome: n}
_thread = None


def folder():
    return Path(os.environ.get("RPC_MONITOR_DIR") or (Path.home() / ".rpc-monitor"))


def normalize(url):
    return (url or "").strip().rstrip("/")


def host_of(url):
    try:
        return (urlsplit(normalize(url)).hostname or "?").lower()
    except ValueError:
        return "?"


def endpoint_id(url):
    """host#hash: the same link always gives the same id, and the key cannot be read back."""
    u = normalize(url)
    return "%s#%s" % (host_of(u), hashlib.sha256(u.encode("utf-8", "replace")).hexdigest()[:10])


def hit(bot, url, outcome="ok", n=1):
    if os.environ.get("RPC_METER_OFF") or not url:
        return
    if IN_TESTS is None and ("unittest" in sys.modules or "pytest" in sys.modules):
        return
    try:
        if outcome not in OUTCOMES:
            outcome = "error"
        key = (int(time.time() // 60 * 60), str(bot)[:40], endpoint_id(url), host_of(url))
        with _lock:
            row = _buf.setdefault(key, {})
            row[outcome] = row.get(outcome, 0) + int(n)
        _start()
    except Exception:
        pass


def flush():
    try:
        with _lock:
            rows = list(_buf.items())
            _buf.clear()
        if not rows:
            return
        d = folder()
        d.mkdir(parents=True, exist_ok=True)
        by_file = {}
        for (minute, bot, ep, host), counts in rows:
            line = {"t": minute, "bot": bot, "ep": ep, "host": host}
            line.update({o: counts.get(o, 0) for o in OUTCOMES})
            day = time.strftime("%Y-%m-%d", time.gmtime(minute))
            by_file.setdefault(d / ("usage-%s.jsonl" % day), []).append(json.dumps(line, separators=(",", ":")))
        for path, lines in by_file.items():
            # One short write per line in append mode: lines from several bots never interleave.
            with open(path, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
    except Exception:
        pass


def _loop():
    while True:
        time.sleep(FLUSH_S)
        flush()


def _start():
    global _thread
    if _thread is None:
        with _lock:
            if _thread is None:
                _thread = threading.Thread(target=_loop, name="rpc-meter", daemon=True)
                _thread.start()
                atexit.register(flush)

