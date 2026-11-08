"""Bridge to the Node executor (ethers), which holds the key and signs.

The key (RH_PRIVATE_KEY) is handed to the Node process in its environment; Python and the
browser only ever see the address that comes back.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

EXECUTOR = Path(__file__).resolve().parent / "executor"
USAGE_LOG = Path(__file__).resolve().parent / "data" / "rpc_usage.jsonl"


def ready() -> str | None:
    """None when the executor can run, else what is missing."""
    if not shutil.which("node"):
        return "Node.js belum ada (brew install node)"
    if not (EXECUTOR / "node_modules" / "ethers").exists():
        import shlex
        return f"jalanin sekali: cd {shlex.quote(str(EXECUTOR))} && npm install"
    return None


def last_json(stdout: str) -> dict | None:
    line = next((l for l in reversed(stdout.strip().splitlines()) if l.startswith("{")), None)
    try:
        return json.loads(line) if line else None
    except json.JSONDecodeError:
        return None


def _meter_executor(bot: str, by_host: dict, urls: list[str]) -> None:
    """Hand the executor's request counts ({label: n}) to the RPC Monitor (../rpc_monitor/meter.py).
    A label is "helius" / "alchemy" / "public" or a hostname; it is matched back to the link it came from."""
    try:
        import importlib.util
        import sys
        from urllib.parse import urlsplit
        mod = sys.modules.get("rpc_meter")
        if mod is None:
            path = Path(__file__).resolve().parents[1] / "rpc_monitor" / "meter.py"
            spec = importlib.util.spec_from_file_location("rpc_meter", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules["rpc_meter"] = mod
        for label, n in (by_host or {}).items():
            url = next((u for u in urls if u and (label in (urlsplit(u).hostname or "") or
                        (label == "public" and u == urls[-1]))), None)
            if url:
                mod.hit(bot, url, "ok", int(n))
    except Exception:
        pass


def _log(script: str, args: list[str], res: dict) -> None:
    st = (res or {}).get("rpc")
    if not st:
        return
    try:
        from . import chain
        _meter_executor("rh_terminal", st.get("byHost"), list(reversed(chain.rpc_urls())))
    except Exception:
        pass
    mode = next((a[2:] for a in args if a in ("--plan", "--dry-run")), "send")
    try:
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "script": script,
                                "mode": mode, "ok": res.get("ok"), **st}) + "\n")
    except OSError:
        pass


def run(script: str, args: list[str], timeout: int = 300) -> dict:
    missing = ready()
    if missing:
        return {"ok": False, "error": missing}
    try:
        r = subprocess.run(["node", str(EXECUTOR / script), *args], capture_output=True, text=True,
                           timeout=timeout, env=dict(os.environ))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{script} nggak selesai dalam {timeout} detik (cek tx di explorer sebelum coba lagi)"}
    res = last_json(r.stdout)
    if res is not None:
        _log(script, args, res)
        return res
    return {"ok": False, "error": (r.stderr.strip() or r.stdout.strip())[-600:]}


def key_configured() -> bool:
    return bool((os.environ.get("RH_PRIVATE_KEY") or "").strip())


_wallet_cache: dict = {}


def trade_wallet(force: bool = False) -> dict:
    """{"ok", "address"} for the key, worked out by the executor. Again when the key changes."""
    fp = hashlib.sha256(os.environ.get("RH_PRIVATE_KEY", "").encode()).hexdigest()
    hit = _wallet_cache.get("v")
    stale = not hit or hit["fp"] != fp or (not hit["res"].get("ok") and time.time() - hit["t"] > 20)
    if force or stale:
        res = run("wallet.mjs", [], timeout=60) if key_configured() else {"ok": False, "error": "no key"}
        _wallet_cache["v"] = {"fp": fp, "t": time.time(), "res": res}
    return _wallet_cache["v"]["res"]


def ladder_args(pool: str, usd: float, slices: int, top: float, bottom: float, eth_usd: float | None) -> list[str]:
    a = ["--pool", pool, "--usd", str(usd), "--slices", str(int(slices)), "--top", str(top), "--bottom", str(abs(bottom))]
    if eth_usd:
        a += ["--ethusd", str(eth_usd)]
    return a
