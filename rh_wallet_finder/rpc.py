"""JSON-RPC client: retries, batching, and eth_getLogs that finds the
provider's block-range limit on its own."""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


class RPCError(Exception):
    pass


class RangeError(RPCError):
    """eth_getLogs asked for too many blocks or got too many results."""

    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.hint = hint  # a block count the provider says will work


_RANGE_WORDS = ("block range", "range is too", "too many", "more than", "exceed", "limit",
                "response size", "query timeout", "too large", "max range")


def _range_hint(msg):
    m = re.search(r"\[(0x[0-9a-f]+),\s*(0x[0-9a-f]+)\]", msg)
    if m:
        return int(m.group(2), 16) - int(m.group(1), 16) + 1
    m = re.search(r"up to a (\d+) block range", msg) or re.search(r"(\d+) blocks?", msg)
    return int(m.group(1)) if m else None


class RPC:
    def __init__(self, url, workers=6, timeout=60):
        if not url:
            raise RPCError("RPC_URL is empty. Paste your RPC link in Settings or in rh_wallet_finder/.env")
        self.url = url
        self.timeout = timeout
        self.workers = max(1, workers)
        self.calls = 0
        self.batch_ok = True
        self._lock = threading.Lock()
        self._gate = threading.Semaphore(self.workers)
        self._pause_until = 0.0

    # ---- transport -------------------------------------------------------
    def _post(self, payload):
        body = json.dumps(payload).encode()
        last = None
        for attempt in range(7):
            wait = self._pause_until - time.time()
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(self.url, data=body, headers={
                "content-type": "application/json", "user-agent": "rh-wallet-finder/1.0"})
            try:
                with self._gate:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        out = json.load(resp)
                with self._lock:
                    self.calls += len(payload) if isinstance(payload, list) else 1
                if isinstance(out, dict) and _throttled(out.get("error")):
                    raise _Throttle()
                return out
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (429, 500, 502, 503, 504):
                    self._backoff(attempt, e.code == 429)
                    continue
                try:
                    return json.load(e)  # many providers put a JSON-RPC error in the 4xx body
                except ValueError:
                    raise RPCError(f"HTTP {e.code} from RPC") from e
            except _Throttle:
                last = "rate limited"
                self._backoff(attempt, True)
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                last = e
                self._backoff(attempt, False)
        raise RPCError(f"RPC kept failing: {last}")

    def _backoff(self, attempt, throttled):
        delay = min(30, 0.5 * 2 ** attempt)
        if throttled:  # everyone waits, not just this thread
            self._pause_until = max(self._pause_until, time.time() + delay)
        time.sleep(delay)

    # ---- calls -----------------------------------------------------------
    def call(self, method, params):
        out = self._post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        return _result(out, method)

    def batch(self, calls, size=40):
        """[(method, params), ...] -> [result or RPCError, ...] in order."""
        results = [None] * len(calls)
        chunks = [list(range(i, min(i + size, len(calls)))) for i in range(0, len(calls), size)]

        def run(idx):
            for attempt in range(6):
                if not (self.batch_ok and len(idx) > 1):
                    break
                payload = [{"jsonrpc": "2.0", "id": i, "method": calls[i][0], "params": calls[i][1]}
                           for i in idx]
                out = self._post(payload)
                if not isinstance(out, list):
                    self.batch_ok = False  # this provider doesn't do batches
                    break
                by_id = {o.get("id"): o for o in out}
                for i in idx:
                    o = by_id.get(i)
                    try:
                        results[i] = _result(o, calls[i][0]) if o else RPCError("missing from batch reply")
                    except RPCError as e:
                        results[i] = e
                # a provider can rate limit single entries of a batch: back off, resend those
                idx = [i for i in idx if isinstance(results[i], RPCError) and _throttled_err(results[i])]
                if not idx:
                    return
                self._backoff(attempt, True)
            for i in idx:
                results[i] = self._single(calls[i])

        with ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(run, chunks))
        return results

    def _single(self, c):
        try:
            return self.call(*c)
        except RPCError as e:
            return e

    # ---- logs ------------------------------------------------------------
    def get_logs(self, flt, start, end, chunk, progress=None):
        """All logs for flt between start and end (inclusive), oldest first.

        chunk is a starting guess; it shrinks when the provider refuses and
        grows back while answers stay small. Returns (logs, chunk).
        """
        out = []
        b = start
        state = {"chunk": max(1, chunk), "ceiling": 200_000}
        while b <= end:
            # fetch up to `workers` chunks at once
            spans = []
            s = b
            for _ in range(self.workers):
                if s > end:
                    break
                e = min(end, s + state["chunk"] - 1)
                spans.append((s, e))
                s = e + 1

            def fetch(span):
                f = dict(flt, fromBlock=hex(span[0]), toBlock=hex(span[1]))
                try:
                    return self.call("eth_getLogs", [f])
                except RangeError as err:
                    return err

            with ThreadPoolExecutor(len(spans)) as ex:
                got = list(ex.map(fetch, spans))
            ok_until = b - 1
            shrink = None
            for span, res in zip(spans, got):
                if isinstance(res, RangeError):
                    shrink = res
                    break
                out.extend(res)
                ok_until = span[1]
            if shrink is not None:
                size = state["chunk"]
                new = shrink.hint if shrink.hint and shrink.hint < size else size // 4
                if size <= 1:
                    raise shrink
                state["chunk"] = max(1, new)
                state["ceiling"] = min(state["ceiling"], max(1, new if shrink.hint else size - 1))
            elif got and max(len(r) for r in got) < 2000 and state["chunk"] < state["ceiling"]:
                state["chunk"] = min(state["ceiling"], int(state["chunk"] * 1.5) + 1)
            b = ok_until + 1
            if progress:
                progress(b - start, end - start + 1, len(out))
        return out, state["chunk"]


class _Throttle(Exception):
    pass


def _throttled(err):
    if not err:
        return False
    msg = str(err.get("message", "")).lower() if isinstance(err, dict) else str(err).lower()
    code = err.get("code") if isinstance(err, dict) else None
    return code == 429 or any(w in msg for w in ("rate limit", "compute units", "too many requests",
                                                   "capacity", "throughput"))


def _throttled_err(e):
    return _throttled(str(e)) or "missing from batch" in str(e)


def _result(out, method):
    if not isinstance(out, dict):
        raise RPCError(f"bad reply to {method}")
    err = out.get("error")
    if err:
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        if method == "eth_getLogs" and any(w in msg.lower() for w in _RANGE_WORDS):
            raise RangeError(msg, _range_hint(msg))
        raise RPCError(f"{method}: {msg}")
    return out.get("result")


def make_rpcs(cfg):
    """(logs RPC, calls RPC). They are the same client unless CALL_RPC_URL is set."""
    workers = int(cfg.get("RPC_WORKERS") or 6)
    logs = RPC(cfg["RPC_URL"], workers=workers)
    calls = RPC(cfg["CALL_RPC_URL"], workers=workers) if cfg.get("CALL_RPC_URL") else logs
    return logs, calls
