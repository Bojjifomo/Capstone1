"""JSON-RPC client: retries, batching, eth_getLogs that finds the provider's
block-range limit on its own, and a pool that runs several RPCs as one."""
import http.client
import itertools
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


class RPCError(Exception):
    pass


class TransportError(RPCError):
    """The provider didn't answer (rate limit, spent quota, broken connection),
    as opposed to answering with an error: another RPC may do better."""


class ThrottledError(TransportError):
    """Rate limited. A pool member raises it at once and rests until its
    cool_until while the others carry on; a lone RPC waits it out first."""


class QuotaError(TransportError):
    """The provider says the plan's allowance is used up (e.g. Alchemy's monthly
    compute units): retrying in a minute won't help."""


QUOTA_RETRY_S = 3600  # an RPC whose quota ran out is tried again after this long


class TruncatedError(RPCError):
    """The reply kept getting cut off mid-way (IncompleteRead): usually an
    eth_getLogs answer too big for the provider or the network in between."""


class RangeError(RPCError):
    """eth_getLogs asked for too many blocks or got too many results."""

    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.hint = hint  # a block count the provider says will work


class LogsInterrupted(RPCError):
    """get_logs stopped part-way: the logs it had, and where to carry on."""

    def __init__(self, cause, logs, next_block, chunk):
        super().__init__(str(cause))
        self.cause, self.logs, self.next_block, self.chunk = cause, logs, next_block, chunk


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
        self.host = urllib.parse.urlparse(url).hostname or "RPC"
        self.timeout = timeout
        self.workers = max(1, workers)
        self.patient = True    # False in a pool: rate limits are handed back instead of waited out
        self.cool_until = 0.0  # pool member resting after a rate limit, or after it kept failing
        self.spent_until = 0.0  # ...or after its quota ran out
        self.spent = None      # (when, message) of that
        self.strikes = 0       # rate limits in a row
        self.log_chunk = None  # the eth_getLogs block range that last worked here
        self.calls = 0
        self.batch_ok = True
        self._lock = threading.Lock()
        self._gate = threading.Semaphore(self.workers)
        self._pause_until = 0.0

    # ---- transport -------------------------------------------------------
    def _post(self, payload):
        body = json.dumps(payload).encode()
        last = None
        cut = 0
        throttled = False
        for attempt in range(9):  # 7 tries for broken connections, ~5 min of backing off for rate limits
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
                    raise _Throttle(str(out["error"].get("message", out["error"])
                                        if isinstance(out["error"], dict) else out["error"]))
                self.strikes = 0
                return out
            except urllib.error.HTTPError as e:
                reply = ""
                try:
                    reply = e.read().decode("utf-8", "replace")
                except (OSError, http.client.HTTPException):
                    pass
                if e.code == 429:
                    self._throttle(reply or "HTTP 429 Too Many Requests")
                    last, throttled = f"HTTP 429 Too Many Requests {reply[:160]}".strip(), True
                    self._backoff(attempt, True)
                    continue
                if e.code in (500, 502, 503, 504):
                    last = f"HTTP {e.code} {reply[:160]}".strip()
                    if attempt >= 6:
                        break
                    self._backoff(attempt, False)
                    continue
                try:
                    return json.loads(reply)  # many providers put a JSON-RPC error in the 4xx body
                except ValueError:
                    raise TransportError(f"{self.host}: HTTP {e.code} {reply[:160]}".strip()) from e
            except _Throttle as e:
                self._throttle(str(e))
                last, throttled = f"rate limited: {e}", True
                self._backoff(attempt, True)
            except http.client.IncompleteRead as e:
                last = e
                cut += 1
                if cut >= 2:  # same big answer cut off twice: let the caller ask for less
                    raise TruncatedError(f"{self.host}: reply cut off ({len(e.partial):,} bytes read)") from e
                self._backoff(attempt, False)
            except (OSError, http.client.HTTPException, json.JSONDecodeError) as e:
                # dropped / reset connections, TLS hiccups, timeouts, half-sent replies
                last = e
                if attempt >= 6:
                    break
                self._backoff(attempt, False)
        if throttled:
            raise ThrottledError(f"{self.host} kept rate limiting for 5 minutes ({last}). Add a second RPC "
                                 f"(Settings), lower \"Parallel RPC requests\", or use a plan with a higher limit.")
        raise TransportError(f"{self.host} kept failing: {last}")

    def _throttle(self, msg):
        """A rate-limit answer. A spent quota fails at once (waiting won't help);
        a pool member steps aside so the pool sends the work elsewhere."""
        msg = msg.strip()[:200]
        if _quota(msg):
            raise QuotaError(f"{self.host}: plan limit reached ({msg})")
        if not self.patient:
            delay = min(60, 2 * 2 ** self.strikes) * random.uniform(1, 1.25)
            self.strikes += 1
            self.cool_until = max(self.cool_until, time.time() + delay)
            raise ThrottledError(f"{self.host}: rate limited ({msg})")

    def _backoff(self, attempt, throttled):
        if throttled:  # everyone waits, not just this thread
            delay = min(60, 2 * 2 ** attempt) * random.uniform(1, 1.25)
            self._pause_until = max(self._pause_until, time.time() + delay)
        else:
            delay = min(30, 0.5 * 2 ** attempt)
        time.sleep(delay)

    # ---- calls -----------------------------------------------------------
    def call(self, method, params):
        out = self._post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        return _result(out, method, self.host)

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
                try:
                    out = self._post(payload)
                except TruncatedError:
                    break  # this batch's reply keeps getting cut off: ask one by one below
                except TransportError as e:
                    if self.patient:
                        raise
                    for i in idx:  # a pool member: the pool sends these elsewhere
                        results[i] = e
                    return
                if not isinstance(out, list):
                    self.batch_ok = False  # this provider doesn't do batches
                    break
                by_id = {o.get("id"): o for o in out}
                for i in idx:
                    o = by_id.get(i)
                    try:
                        results[i] = _result(o, calls[i][0], self.host) if o else RPCError("missing from batch reply")
                    except RPCError as e:
                        results[i] = e
                # a provider can rate limit single entries of a batch: back off, resend those
                idx = [i for i in idx if isinstance(results[i], RPCError) and _throttled_err(results[i])]
                if not idx:
                    return
                try:
                    self._throttle(next((str(results[i]) for i in idx if _quota(str(results[i]))),
                                        str(results[idx[0]])))
                except TransportError as e:
                    if self.patient:  # (only a spent quota gets here)
                        raise
                    for i in idx:
                        results[i] = e
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
                except TruncatedError as err:  # too much to come back in one piece: smaller ranges
                    return RangeError(str(err))

            try:
                with ThreadPoolExecutor(len(spans)) as ex:
                    got = list(ex.map(fetch, spans))
            except RPCError as err:  # what we have so far, and where to pick up (another RPC in a pool)
                raise LogsInterrupted(err, out, b, state["chunk"]) from err
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


_QUOTA_WORDS = ("monthly", "quota", "credits", "billing", "daily limit", "capacity limit")


def _quota(msg):
    """A rate-limit answer that means the plan's allowance is spent, not 'slow down'."""
    return any(w in msg.lower() for w in _QUOTA_WORDS)


def _throttled_err(e):
    return _throttled(str(e)) or "missing from batch" in str(e)


def _result(out, method, host="RPC"):
    if not isinstance(out, dict):
        raise RPCError(f"bad reply to {method}")
    err = out.get("error")
    if err:
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        if method == "eth_getLogs" and any(w in msg.lower() for w in _RANGE_WORDS):
            raise RangeError(msg, _range_hint(msg))
        # some providers answer a spent allowance as an error inside a normal reply
        # (SolidRPC: "Daily response quota exceeded"), not as HTTP 429
        if _quota(msg) and any(w in msg.lower() for w in ("exceed", "reached", "limit", "used up", "exhausted")):
            raise QuotaError(f"{host}: plan limit reached ({msg.strip()[:200]})")
        raise RPCError(f"{method}: {msg}")
    return out.get("result")


# answers that mean "this RPC can't", where another one might: no old block state
_ELSEWHERE = ("missing trie node", "pruned", "historical state", "state unavailable", "state not available",
              "state is not available", "header not found", "unknown block", "archive", "missing from batch")


def _elsewhere(err):
    return isinstance(err, TransportError) or (
        isinstance(err, RPCError) and any(w in str(err).lower() for w in _ELSEWHERE))


class RPCPool:
    """Several RPCs as one (e.g. SolidRPC + Alchemy). Calls and batches are
    shared out round-robin; an RPC that rate limits rests while the others carry
    on, one whose quota is spent sits out for an hour, and a call that needs old
    state one of them lacks is asked of another. eth_getLogs goes to the first
    RPC (the one with the big block ranges) and carries on from where it stopped
    on another while the first rests. Only when all of them rest does anything
    wait, for up to WAIT_S."""
    WAIT_S = 600

    def __init__(self, rpcs):
        self.members = list(rpcs)
        for m in self.members:
            m.patient = False
        self.host = " + ".join(m.host for m in self.members)
        self.workers = sum(m.workers for m in self.members)
        self._turn = itertools.count()

    @property
    def calls(self):
        return sum(m.calls for m in self.members)

    @property
    def exhausted(self):
        """(since, message) of a member sitting out a spent quota, or None."""
        now = time.time()
        return next((m.spent for m in self.members if m.spent_until > now), None)

    def _bench(self, m, err):
        if isinstance(err, QuotaError):
            m.spent_until = time.time() + QUOTA_RETRY_S
            m.spent = (time.time(), str(err))
        elif not isinstance(err, ThrottledError):  # (a rate-limited member set its own rest)
            m.cool_until = max(m.cool_until, time.time() + 60)

    def _pick(self, skip=(), first=False, deadline=None):
        """A member that can take a request now (round-robin, or the first one
        that can); waits while they all rest. None: nobody left, or out of time."""
        deadline = deadline or time.time() + self.WAIT_S
        while True:
            now = time.time()
            live = [m for m in self.members if m.spent_until <= now and m not in skip]
            if not live:
                return None
            ready = [m for m in live if m.cool_until <= now]
            if ready:
                return ready[0] if first else ready[next(self._turn) % len(ready)]
            if now >= deadline:
                return None
            time.sleep(max(0.05, min(deadline, min(m.cool_until for m in live)) - now))

    def _gave_up(self, last):
        if all(m.spent_until > time.time() for m in self.members):
            return QuotaError(" · ".join(m.spent[1] for m in self.members))
        if isinstance(last, ThrottledError) or last is None:
            return ThrottledError(f"{self.host}: all kept rate limiting for {self.WAIT_S // 60} minutes. "
                                  f"Lower \"Parallel RPC requests\" in Settings.")
        return last

    def call(self, method, params):
        skip, last = [], None
        deadline = time.time() + self.WAIT_S
        while True:
            m = self._pick(skip, deadline=deadline)
            if m is None:
                raise self._gave_up(last)
            try:
                return m.call(method, params)
            except RPCError as e:
                if not _elsewhere(e):
                    raise  # a real answer (reverted, range too big, cut off): the caller's to handle
                last = e
                self._bench(m, e)
                if not isinstance(e, ThrottledError):
                    skip.append(m)

    def batch(self, calls, size=40):
        """Like RPC.batch, chunks spread over the members; entries one couldn't
        answer go to another."""
        results = [None] * len(calls)
        cant = {}  # index -> members that couldn't answer it
        deadline = time.time() + self.WAIT_S
        todo = list(range(len(calls)))
        while todo:
            served = []

            def run(idx):
                skip = set().union(*(cant.get(i, set()) for i in idx))
                m = self._pick(skip, deadline=deadline)
                if m is None:
                    return
                served.append(1)
                try:
                    res = m.batch([calls[i] for i in idx], size)
                except RPCError as e:
                    res = [e] * len(idx)
                benched = False
                for i, r in zip(idx, res):
                    results[i] = r
                    if isinstance(r, RPCError) and _elsewhere(r):
                        if isinstance(r, TransportError) and not benched:
                            self._bench(m, r)
                            benched = True
                        if not isinstance(r, (ThrottledError, QuotaError)):
                            cant.setdefault(i, set()).add(m)
            chunks = [todo[i:i + size] for i in range(0, len(todo), size)]
            with ThreadPoolExecutor(max(1, min(self.workers, len(chunks)))) as ex:
                list(ex.map(run, chunks))
            todo = [i for i in todo if (results[i] is None or _elsewhere(results[i]))
                    and len(cant.get(i, ())) < len(self.members)]
            if not served or time.time() >= deadline:
                break
        gave_up = self._gave_up(None)
        return [gave_up if r is None else r for r in results]

    def get_logs(self, flt, start, end, chunk, progress=None):
        """RPC.get_logs on the first member that can; a stretch at a time on the
        others, so the first takes over again once it has rested."""
        out, b, skip, last = [], start, [], None
        deadline = time.time() + self.WAIT_S
        while b <= end:
            m = self._pick(skip, first=True, deadline=deadline)
            if m is None:
                raise self._gave_up(last)
            stop = end
            if m is not self.members[0]:
                stop = min(end, b + (m.log_chunk or chunk) * m.workers * 2 - 1)

            def prog(done, total, n, b0=b, n0=len(out)):
                if progress:
                    progress(b0 - start + done, end - start + 1, n0 + n)
            try:
                got, m.log_chunk = m.get_logs(flt, b, stop, m.log_chunk or chunk, prog)
                out += got
                b = stop + 1
                deadline = time.time() + self.WAIT_S
            except LogsInterrupted as e:
                out += e.logs
                if e.next_block > b:
                    deadline = time.time() + self.WAIT_S
                b, m.log_chunk, last = e.next_block, e.chunk, e.cause
                if not _elsewhere(e.cause):
                    raise e.cause from e
                self._bench(m, e.cause)
                if not isinstance(e.cause, ThrottledError):
                    skip.append(m)
        return out, self.members[0].log_chunk or chunk


def make_rpcs(cfg):
    """(logs RPC, calls RPC): one RPC, or RPC_URL + CALL_RPC_URL as one pool that
    shares the work (the same object twice)."""
    workers = int(cfg.get("RPC_WORKERS") or 6)
    urls = list(dict.fromkeys(u for u in (cfg["RPC_URL"], cfg.get("CALL_RPC_URL")) if u))
    if len(urls) <= 1:
        one = RPC(urls[0] if urls else "", workers=workers)
        return one, one
    pool = RPCPool([RPC(u, workers=workers) for u in urls])
    return pool, pool
