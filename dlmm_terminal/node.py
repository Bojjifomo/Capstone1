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
        return f"jalanin sekali: cd {EXECUTOR} && npm install"
    return None


def run(script: str, args: list[str], timeout: int = 240) -> dict:
    missing = ready()
    if missing:
        return {"ok": False, "error": missing}
    env = {**os.environ, "SOLANA_RPC_URL": rpc_url()}
    r = subprocess.run(["node", str(EXECUTOR / script), *args], capture_output=True, text=True, timeout=timeout, env=env)
    line = next((l for l in reversed(r.stdout.strip().splitlines()) if l.startswith("{")), None)
    if line:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            pass
    return {"ok": r.returncode == 0, "error": (r.stderr.strip() or r.stdout.strip())[-600:], "stdout": r.stdout[-2000:]}


_wallet_cache: dict = {}


def key_configured() -> bool:
    return bool(os.environ.get("DLMM_PRIVATE_KEY") or os.environ.get("DLMM_KEYPAIR"))


def trade_wallet() -> dict:
    """{"ok", "pubkey"} for the key in .env, worked out once by the executor. The key itself
    never leaves the Node process; only its public address comes back."""
    if "v" not in _wallet_cache:
        _wallet_cache["v"] = run("wallet.mjs", [], timeout=60) if key_configured() else {"ok": False, "error": "no key"}
    return _wallet_cache["v"]


def ladder_args(pool: str, sol: float, slices: int, bottom: float, top: float = 0, strategy: str = "spot") -> list[str]:
    return ["--pool", pool, "--sol", str(sol), "--slices", str(int(slices)), "--bottom", str(bottom),
            "--top", str(top), "--strategy", strategy]
