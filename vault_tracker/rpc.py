"""Minimal Solana JSON-RPC client with retry/backoff for rate-limited RPCs."""

import os
import time

import requests

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


def default_rpc_url():
    url = os.environ.get("SOLANA_RPC_URL")
    if url:
        return url
    key = os.environ.get("ALCHEMY_API_KEY")
    if key:
        return f"https://solana-mainnet.g.alchemy.com/v2/{key}"
    return PUBLIC_RPC


class RpcError(RuntimeError):
    pass


class Rpc:
    def __init__(self, url=None, min_interval=0.0, retries=6, timeout=60):
        self.url = url or default_rpc_url()
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self.session = requests.Session()
        self._last_call = 0.0

    def call(self, method, params=None, retries=None, timeout=None):
        retries = self.retries if retries is None else retries
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        last_err = None
        for attempt in range(retries):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                r = self.session.post(self.url, json=payload, timeout=timeout or self.timeout)
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.0 * (2 ** attempt))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_err = RpcError(f"HTTP {r.status_code}")
                time.sleep(1.0 * (2 ** attempt))
                continue
            try:
                j = r.json()
            except ValueError:
                raise RpcError(f"HTTP {r.status_code}: {r.text[:200]}")
            err = j.get("error")
            if err:
                if err.get("code") in (429, -32429) or "rate" in str(err.get("message", "")).lower():
                    last_err = RpcError(err)
                    time.sleep(1.0 * (2 ** attempt))
                    continue
                raise RpcError(err)
            return j.get("result")
        raise RpcError(f"{method} failed after {retries} attempts: {last_err}")

    # ---- convenience wrappers -------------------------------------------------

    def get_multiple_accounts(self, pubkeys, encoding="base64"):
        """Returns list aligned with pubkeys; None for missing accounts."""
        out = []
        pubkeys = list(pubkeys)
        for i in range(0, len(pubkeys), 100):
            chunk = pubkeys[i:i + 100]
            res = self.call("getMultipleAccounts", [chunk, {"encoding": encoding}])
            out.extend(res["value"])
        return out

    def get_signatures(self, address, limit=1000, before=None, until=None):
        opts = {"limit": min(limit, 1000)}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        return self.call("getSignaturesForAddress", [address, opts])

    def get_transaction(self, signature):
        return self.call("getTransaction", [signature, {
            "encoding": "json",
            "maxSupportedTransactionVersion": 1,
            "commitment": "confirmed",
        }])

    def get_transactions(self, signatures, workers=4):
        """Fetch many transactions concurrently. Returns {signature: tx or None}."""
        from concurrent.futures import ThreadPoolExecutor
        import threading
        local = threading.local()

        def one(sig):
            if not hasattr(local, "rpc"):
                local.rpc = Rpc(self.url, self.min_interval * workers, self.retries, self.timeout)
            try:
                return sig, local.rpc.get_transaction(sig)
            except Exception:
                return sig, None
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return dict(ex.map(one, list(signatures)))

    def get_balance(self, address):
        return self.call("getBalance", [address])["value"]

    def get_token_accounts(self, owner):
        out = []
        for program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            res = self.call("getTokenAccountsByOwner",
                            [owner, {"programId": program}, {"encoding": "jsonParsed"}])
            out.extend(res["value"])
        return out


TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
