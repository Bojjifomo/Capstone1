"""Bridge to the Node executor (Meteora SDK) and a raw-transaction relay.

The executor builds, simulates and sends with the key in .env (DLMM_PRIVATE_KEY or
DLMM_KEYPAIR). The key is read by the Node process only; Python and the browser only
ever see the wallet's address.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
import urllib.request
from pathlib import Path

EXECUTOR = Path(__file__).resolve().parent / "executor"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


def rpc_url() -> str:
    return os.environ.get("SOLANA_RPC_URL") or PUBLIC_RPC


def ready() -> str | None:
    """None when the executor can run, else what is missing."""
    if not shutil.which("node"):
        return "Node.js belum ada (brew install node)"
    if not (EXECUTOR / "node_modules" / "@meteora-ag" / "dlmm").exists():
        import shlex
        return f"jalanin sekali: cd {shlex.quote(str(EXECUTOR))} && npm install"
    return None


USAGE_LOG = Path(__file__).resolve().parent / "data" / "rpc_usage.jsonl"


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


def log_rpc(script: str, args: list[str], result: dict) -> None:
    """Every executor run reports its RPC requests ({"rpc": {calls, byHost, byMethod}}): keep them,
    so `python -m dlmm_terminal rpc` can show what the Helius free tier is spent on."""
    st = (result or {}).get("rpc")
    if not st:
        return
    _meter_executor("dlmm_terminal", st.get("byHost"), [os.environ.get("SOLANA_RPC_URL") or "", PUBLIC_RPC])
    mode = next((a[2:] for a in args if a in ("--plan", "--dry-run", "--build")), "send")
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "script": script, "mode": mode,
           "ok": result.get("ok"), **st}
    try:
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def last_json(stdout: str) -> dict | None:
    line = next((l for l in reversed(stdout.strip().splitlines()) if l.startswith("{")), None)
    try:
        return json.loads(line) if line else None
    except json.JSONDecodeError:
        return None


def run(script: str, args: list[str], timeout: int = 240) -> dict:
    missing = ready()
    if missing:
        return {"ok": False, "error": missing}
    env = {**os.environ, "SOLANA_RPC_URL": rpc_url()}
    r = subprocess.run(["node", str(EXECUTOR / script), *args], capture_output=True, text=True, timeout=timeout, env=env)
    res = last_json(r.stdout)
    if res is not None:
        log_rpc(script, args, res)
        return res
    return {"ok": r.returncode == 0, "error": (r.stderr.strip() or r.stdout.strip())[-600:], "stdout": r.stdout[-2000:]}


_wallet_cache: dict = {}


def key_configured() -> bool:
    return bool(os.environ.get("DLMM_PRIVATE_KEY") or os.environ.get("DLMM_KEYPAIR"))


def trade_wallet(force: bool = False) -> dict:
    """{"ok", "pubkey"} for the key in .env, worked out by the executor. The key itself never
    leaves the Node process; only its public address comes back. Worked out again when the key
    in the environment changes (the web re-reads .env), and a failure is retried after 20 s."""
    import hashlib
    import time
    fp = hashlib.sha256((os.environ.get("DLMM_PRIVATE_KEY", "") + "|" + os.environ.get("DLMM_KEYPAIR", "")).encode()).hexdigest()
    hit = _wallet_cache.get("v")
    stale = not hit or hit["fp"] != fp or (not hit["res"].get("ok") and time.time() - hit["t"] > 20)
    if force or stale:
        res = run("wallet.mjs", [], timeout=60) if key_configured() else {"ok": False, "error": "no key"}
        _wallet_cache["v"] = {"fp": fp, "t": time.time(), "res": res}
    return _wallet_cache["v"]["res"]


def ladder_args(pool: str, sol: float, slices: int, bottom: float, top: float = 0, strategy: str = "spot") -> list[str]:
    return ["--pool", pool, "--sol", str(sol), "--slices", str(int(slices)), "--bottom", str(bottom),
            "--top", str(top), "--strategy", strategy]
