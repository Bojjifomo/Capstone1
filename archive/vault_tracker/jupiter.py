"""Token swaps through the Jupiter Swap API (v1): quote -> swap transaction -> sign -> send."""

import base64
import os
import time

import requests
from solders.transaction import VersionedTransaction


def _base():
    key = os.environ.get("JUPITER_API_KEY")
    if key:
        return "https://api.jup.ag/swap/v1", {"x-api-key": key}
    return "https://lite-api.jup.ag/swap/v1", {}


def quote(input_mint, output_mint, amount, slippage_bps=100):
    base, headers = _base()
    r = requests.get(f"{base}/quote", headers=headers, timeout=15, params={
        "inputMint": input_mint, "outputMint": output_mint, "amount": str(int(amount)),
        "slippageBps": str(int(slippage_bps)), "restrictIntermediateTokens": "true"})
    r.raise_for_status()
    q = r.json()
    if "outAmount" not in q:
        raise RuntimeError(f"Jupiter tidak memberi quote: {str(q)[:200]}")
    return q


def swap(rpc, keypair, quote_response, max_priority_lamports=1_000_000, timeout=60):
    """Execute a quoted swap with `keypair`. Returns the signature once confirmed."""
    base, headers = _base()
    r = requests.post(f"{base}/swap", headers=headers, timeout=20, json={
        "quoteResponse": quote_response,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
            "maxLamports": max_priority_lamports, "priorityLevel": "high"}},
    })
    r.raise_for_status()
    raw = base64.b64decode(r.json()["swapTransaction"])
    unsigned = VersionedTransaction.from_bytes(raw)
    signed = VersionedTransaction(unsigned.message, [keypair])
    sig = rpc.call("sendTransaction", [base64.b64encode(bytes(signed)).decode(), {
        "encoding": "base64", "skipPreflight": False, "maxRetries": 5}])
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = rpc.call("getSignatureStatuses", [[sig]])["value"][0]
        if st:
            if st.get("err"):
                raise RuntimeError(f"swap gagal on-chain: {st['err']} ({sig})")
            if st.get("confirmationStatus") in ("confirmed", "finalized"):
                return sig
        time.sleep(2)
    raise RuntimeError(f"swap belum terkonfirmasi setelah {timeout} dtk: {sig}")
