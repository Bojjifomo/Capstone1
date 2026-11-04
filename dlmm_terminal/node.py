"""Bridge to the Node executor (Meteora SDK) and a raw-transaction relay.

The executor builds, simulates and (with DLMM_KEYPAIR) sends. For the web, it only
builds: the transactions go to the browser, the wallet (Phantom) signs them there, and
`send_signed` relays the signed bytes to the RPC. No private key ever reaches the server.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
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


def ladder_args(pool: str, sol: float, slices: int, bottom: float, top: float = 0, strategy: str = "spot") -> list[str]:
    return ["--pool", pool, "--sol", str(sol), "--slices", str(int(slices)), "--bottom", str(bottom),
            "--top", str(top), "--strategy", strategy]


def _rpc(method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(rpc_url(), body, {"Content-Type": "application/json"})
    from .api import urlopen
    with urlopen(req, timeout=30) as r:
        d = json.load(r)
    if "error" in d:
        raise RuntimeError(d["error"].get("message", str(d["error"])))
    return d["result"]


def send_signed(txs_b64: list[str], confirm_s: int = 60) -> list[dict]:
    """Send wallet-signed transactions one at a time, each confirmed before the next.
    A ladder's slices are independent positions: if one fails, the rest stop and stay SOL."""
    out = []
    for i, t in enumerate(txs_b64):
        base64.b64decode(t, validate=True)  # refuse anything that isn't a transaction blob
        try:
            sig = _rpc("sendTransaction", [t, {"encoding": "base64", "preflightCommitment": "confirmed", "maxRetries": 5}])
        except Exception as exc:
            out.append({"i": i, "ok": False, "error": str(exc)[:300]})
            break
        status, t0 = None, time.time()
        while time.time() - t0 < confirm_s:
            st = (_rpc("getSignatureStatuses", [[sig]])["value"] or [None])[0]
            if st and st.get("err"):
                status = {"ok": False, "error": json.dumps(st["err"])}
                break
            if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
                status = {"ok": True}
                break
            time.sleep(1.5)
        status = status or {"ok": False, "error": "not confirmed in time; check Solscan"}
        out.append({"i": i, "signature": sig, **status})
        if not status["ok"]:
            break
    return out
