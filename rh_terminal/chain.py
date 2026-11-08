"""Robinhood Chain (4663) reads: JSON-RPC with fallback, Multicall3, logs, ABI helpers.

Reads go to the chain's own public endpoint first, then PublicNode, then the Alchemy key
from .env (ALCHEMY_API_KEY) if there is one. A 429 or a timeout moves to the next
endpoint, so the free endpoints carry the load and the key is only spent when they fail.

Standard library only.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

from .keccak import keccak256

CHAIN_ID = 4663

# Uniswap V4 on Robinhood Chain
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
UNIVERSAL_ROUTER = "0x8876789976decbfcbbbe364623c63652db8c0904"
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"

ZERO = "0x" + "0" * 40                       # native ETH in a PoolKey
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
# The money side of a pair, best first: the other token is the one being LP'd.
QUOTE_RANK = {USDG: 2, ZERO: 1, WETH: 1}
QUOTE_SYMBOL = {USDG: "USDG", ZERO: "ETH", WETH: "WETH"}
DECIMALS = {USDG: 6, ZERO: 18, WETH: 18}

SEL = {
    "poolKeys": "86b6be7d", "getSlot0": "c815641c", "getLiquidity": "fa6793d5",
    "getPoolAndPositionInfo": "7ba03aad", "getPositionLiquidity": "1efeed33",
    "ownerOf": "6352211e", "balanceOf": "70a08231", "symbol": "95d89b41", "decimals": "313ce567",
    "totalSupply": "18160ddd", "getPositionInfo": "dacf1d2f", "getFeeGrowthInside": "53e9c1fb",
    "tryAggregate": "bce38bd7", "getEthBalance": "4d2301cc",
}
TOPIC = {
    "Swap": "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f",
    "Initialize": "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438",
    "Transfer": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
}


def _ssl_context() -> ssl.SSLContext:
    """macOS Pythons often ship without the system CA store; certifi fixes that when present."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CONTEXT = _ssl_context()
UA = "rh-terminal/1"


def urlopen(req, timeout: float = 20, data: bytes | None = None):
    return urllib.request.urlopen(req, data=data, timeout=timeout, context=SSL_CONTEXT)


def get_json(url: str, timeout: float = 15, retries: int = 2):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    last = None
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


# ---------------------------------------------------------------- RPC ----
class RpcError(Exception):
    pass


class LogRangeError(RpcError):
    """No reachable endpoint reads that many blocks of logs in one request."""


# Free public endpoints (chainlist, chain 4663) and how many blocks of logs each serves per request,
# measured 2026-11: the chain's own node and PublicNode 10M blocks (10k logs), Daybreak 2k, dRPC 100
# (its error says 10k; 101 blocks is what it serves). Alchemy's free tier allows 10 blocks, so it is
# used for calls and sends but never for logs.
PUBLIC_ENDPOINTS = (
    ("https://rpc.mainnet.chain.robinhood.com", 10_000_000),
    ("https://robinhood-rpc.publicnode.com", 10_000_000),
    ("https://robinhood.daybreak-rpc.network", 2_000),
    ("https://robinhood.drpc.org", 100),
)
PUBLIC_RPCS = tuple(u for u, _ in PUBLIC_ENDPOINTS)


def endpoints() -> list[tuple[str, int]]:
    """[(url, max log span)] in the order they are tried."""
    eps = list(PUBLIC_ENDPOINTS)
    custom = (os.environ.get("RH_RPC_URL") or "").strip()
    if custom:
        try:
            span = int(os.environ.get("RH_RPC_LOG_RANGE") or 2000)
        except ValueError:
            span = 2000
        eps.insert(0, (custom, span))
    key = (os.environ.get("ALCHEMY_API_KEY") or "").strip()
    if key and all(c.isalnum() or c in "-_" for c in key):
        eps.append((f"https://robinhood-mainnet.g.alchemy.com/v2/{key}", 10))
    return eps


def rpc_urls() -> list[str]:
    return [u for u, _ in endpoints()]


_stats_lock = threading.Lock()
STATS: dict = {"calls": 0, "byHost": {}, "errors": 0}
_cool: dict[str, float] = {}        # url -> skipped until then (rate limit: 20 s, refused / blocked: 5 min)
_span: dict[str, int] = {}          # url -> log span it actually accepted (learned from its errors)
_why: dict[str, str] = {}           # url -> last failure, for doctor and the page


def _host(url: str) -> str:
    return "alchemy" if "alchemy" in url else url.split("/")[2]


def _log_span(params: list) -> int | None:
    try:
        f = params[0]
        return int(f["toBlock"], 16) - int(f["fromBlock"], 16) + 1
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def max_log_span() -> int:
    """Widest log query an endpoint that is not resting can serve (falls back to all endpoints)."""
    now = time.time()
    eps = endpoints()
    live = [_span.get(u, m) for u, m in eps if _cool.get(u, 0) <= now and _span.get(u, m) > 10]
    every = [_span.get(u, m) for u, m in eps if _span.get(u, m) > 10]
    return max(live or every or [0])


def health() -> list[dict]:
    """Per endpoint: resting or not, why, and the log span it serves."""
    now = time.time()
    return [{"host": _host(u), "logSpan": _span.get(u, m), "restingSec": max(0, round(_cool.get(u, 0) - now)),
             "lastError": _why.get(u)} for u, m in endpoints()]


def _range_limit(msg: str) -> int | None:
    """'...up to a 10 block range', 'exceeds max block range 2000', 'over 10000 blocks' -> the number."""
    import re
    m = re.search(r"(\d[\d,]*)\s*(?:block|blocks)", msg) or re.search(r"range\s*(?:of\s*)?(\d[\d,]*)", msg)
    return int(m.group(1).replace(",", "")) if m else None


def rpc(method: str, params: list, timeout: float = 30, _round: int = 0):
    """One JSON-RPC call. Endpoints in order; one that is blocked, refuses or rate limits rests and
    the next is tried; eth_getLogs only goes to endpoints that serve that many blocks. An RPC error
    about the request itself (a revert, a bad argument) is raised at once."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    span = _log_span(params) if method == "eth_getLogs" else None
    fails = []
    eps = [(u, _span.get(u, m)) for u, m in endpoints()]
    if span is not None:
        eps = [(u, m) for u, m in eps if m >= span]
        if not eps:
            raise LogRangeError(f"no RPC serves {span} blocks of logs per request")
    now = time.time()
    order = [u for u, _ in eps if _cool.get(u, 0) <= now] + [u for u, _ in eps if _cool.get(u, 0) > now]
    for url in order:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
        with _stats_lock:
            STATS["calls"] += 1
            h = _host(url)
            STATS["byHost"][h] = STATS["byHost"].get(h, 0) + 1
        try:
            with urlopen(req, timeout=timeout) as r:
                j = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read(300).decode("utf-8", "replace")
            except Exception:
                detail = ""
            lim = _range_limit(detail) if span is not None and e.code == 400 else None
            if lim is not None:                          # a free tier's log range: remember it, try the next one
                # some nodes name a limit they do not keep; refused below it means half is the most to try
                _span[url] = lim if lim < span else max(10, span // 2)
                fails.append(f"{_host(url)}: logs max {_span[url]} blocks")
                continue
            fails.append(f"{_host(url)} HTTP {e.code}")
            _why[url] = f"HTTP {e.code} {detail[:120]}".strip()
            _cool[url] = time.time() + (20 if e.code == 429 else 300 if e.code in (401, 403) else 30)
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            fails.append(f"{_host(url)}: {str(e)[:70]}")
            _why[url] = str(e)[:160]
            refused = "refused" in str(e).lower() or "nodename" in str(e).lower() or "name or service" in str(e).lower()
            _cool[url] = time.time() + (300 if refused else 30)
            continue
        if "error" in j:
            msg = str((j["error"] or {}).get("message") or j["error"])
            if "rate" in msg.lower() or "too many" in msg.lower():
                _cool[url] = time.time() + 20
                fails.append(f"{_host(url)}: {msg[:60]}")
                continue
            lim = _range_limit(msg) if span is not None else None
            if lim is not None and ("block" in msg.lower() or "range" in msg.lower()):
                _span[url] = lim if lim < span else max(10, span // 2)
                fails.append(f"{_host(url)}: logs max {_span[url]} blocks")
                continue
            raise RpcError(msg)
        _why.pop(url, None)
        return j.get("result")
    if _round < 2 and fails and any("429" in f or "rate" in f.lower() or "too many" in f.lower() for f in fails):
        time.sleep(3 + 3 * _round)
        for u, _ in eps:
            if _cool.get(u, 0) - time.time() < 60:
                _cool.pop(u, None)
        return rpc(method, params, timeout, _round + 1)
    with _stats_lock:
        STATS["errors"] += 1
    if span is not None and fails and all("logs max" in f for f in fails):
        raise LogRangeError("; ".join(fails))
    raise RpcError("; ".join(fails) or "no RPC endpoint")


def block_number() -> int:
    return int(rpc("eth_blockNumber", []), 16)


def block_time(n: int | str) -> int:
    # eth_blockNumber and eth_getBlockByNumber can land on different nodes (or endpoints): one a block
    # or two behind answers null for the newest block. Ask again, then step back a few blocks.
    tag = hex(n) if isinstance(n, int) else n
    for i in range(6):
        b = rpc("eth_getBlockByNumber", [tag, False])
        if b and b.get("timestamp"):
            return int(b["timestamp"], 16)
        time.sleep(0.5)
        if isinstance(n, int) and i >= 2:
            tag = hex(n - 5 * (i - 1))
    raise RuntimeError(f"block {n} belum ada di RPC (node ketinggalan)")


def eth_call(to: str, data: str, block: str = "latest") -> str:
    return rpc("eth_call", [{"to": to, "data": data if data.startswith("0x") else "0x" + data}, block])


def get_logs(address: str, topics: list, start: int, end: int, chunk: int = 3000, pause: float = 0.15) -> list[dict]:
    """All logs in [start, end], in chunks no wider than the reachable endpoints serve; a chunk a
    node refuses as too big (too many logs or blocks) is halved."""
    out = []
    a = start
    step = max(1, min(chunk, max_log_span() or chunk))
    while a <= end:
        b = min(end, a + step - 1)
        try:
            out += rpc("eth_getLogs", [{"address": address, "topics": topics, "fromBlock": hex(a), "toBlock": hex(b)}]) or []
        except LogRangeError:
            narrower = max_log_span()
            if 0 < narrower < step:
                step = narrower
                continue
            raise
        except RpcError as e:
            msg = str(e)
            if ("exceeds" in msg or "limit" in msg or "range" in msg or "narrow" in msg) and step > 50:
                step = max(50, step // 2)
                continue
            raise
        a = b + 1
        if pause:
            time.sleep(pause)
    return out


# ---------------------------------------------------------------- ABI ----
def u256(v: int) -> str:
    return format(v % (1 << 256), "064x")


def addr_word(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


def words(hexdata: str) -> list[str]:
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    return [h[i:i + 64] for i in range(0, len(h), 64)]


def to_int(w: str, bits: int = 256, signed: bool = False) -> int:
    v = int(w or "0", 16) & ((1 << bits) - 1)
    if signed and v >> (bits - 1):
        v -= 1 << bits
    return v


def word_addr(w: str) -> str:
    return "0x" + w[-40:]


def decode_string(hexdata: str) -> str:
    h = hexdata[2:] if hexdata.startswith("0x") else hexdata
    try:
        if len(h) >= 128:
            off = int(h[:64], 16) * 2
            n = int(h[off:off + 64], 16)
            return bytes.fromhex(h[off + 64:off + 64 + n * 2]).decode("utf-8", "replace").strip("\x00")
        return bytes.fromhex(h).rstrip(b"\x00").decode("utf-8", "replace")   # bytes32 symbols
    except Exception:
        return ""


def pool_id(c0: str, c1: str, fee: int, tick_spacing: int, hooks: str) -> str:
    enc = addr_word(c0) + addr_word(c1) + u256(fee) + u256(tick_spacing) + addr_word(hooks)
    return "0x" + keccak256(bytes.fromhex(enc)).hex()


def multicall(calls: list[tuple[str, str]], chunk: int = 60) -> list[str | None]:
    """[(target, calldata)] -> [returndata or None], through Multicall3.tryAggregate(false, ...)."""
    out: list[str | None] = []
    for i in range(0, len(calls), chunk):
        part = calls[i:i + chunk]
        head = SEL["tryAggregate"] + u256(0) + u256(64) + u256(len(part))
        tuples = []
        for target, data in part:
            d = data[2:] if data.startswith("0x") else data
            n = len(d) // 2
            tuples.append(addr_word(target) + u256(64) + u256(n) + d + "0" * ((-len(d)) % 64))
        offs, cur = "", 32 * len(part)
        for t in tuples:
            offs += u256(cur)
            cur += len(t) // 2
        res = eth_call(MULTICALL3, "0x" + head + offs + "".join(tuples))
        h = res[2:]
        base = int(h[:64], 16) * 2
        n = int(h[base:base + 64], 16)
        start = base + 64
        for k in range(n):
            at = start + int(h[start + k * 64:start + k * 64 + 64], 16) * 2
            ok = int(h[at:at + 64], 16) != 0
            doff = at + int(h[at + 64:at + 128], 16) * 2
            dl = int(h[doff:doff + 64], 16)
            out.append("0x" + h[doff + 64:doff + 64 + dl * 2] if ok else None)
    return out


# ------------------------------------------------------------ V4 reads ----
def decode_pool_key(ret: str | None) -> dict | None:
    if not ret or len(ret) < 2 + 64 * 5:
        return None
    w = words(ret)
    c0, c1 = word_addr(w[0]), word_addr(w[1])
    if c0 == ZERO and c1 == ZERO:
        return None
    return {"currency0": c0, "currency1": c1, "fee": to_int(w[2], 24), "tickSpacing": to_int(w[3], 24, True),
            "hooks": word_addr(w[4])}


def pool_keys(ids: list[str]) -> dict[str, dict | None]:
    """PositionManager.poolKeys(bytes25): every pool anyone minted a position in through it."""
    calls = [(POSITION_MANAGER, SEL["poolKeys"] + i[2:52].ljust(64, "0")) for i in ids]
    return {i: decode_pool_key(r) for i, r in zip(ids, multicall(calls))}


def decode_slot0(ret: str | None) -> dict | None:
    if not ret or len(ret) < 2 + 64 * 4:
        return None
    w = words(ret)
    return {"sqrtPriceX96": to_int(w[0], 160), "tick": to_int(w[1], 24, True),
            "protocolFee": to_int(w[2], 24), "lpFee": to_int(w[3], 24)}


def slot0s(ids: list[str]) -> dict[str, dict | None]:
    return {i: decode_slot0(r) for i, r in zip(ids, multicall([(STATE_VIEW, SEL["getSlot0"] + i[2:]) for i in ids]))}


def token_meta(addrs: list[str]) -> dict[str, dict]:
    """symbol, decimals, totalSupply (raw) per ERC-20. Native ETH is answered locally."""
    out = {}
    real = [a for a in addrs if a != ZERO]
    calls = []
    for a in real:
        calls += [(a, SEL["symbol"]), (a, SEL["decimals"]), (a, SEL["totalSupply"])]
    res = multicall(calls) if calls else []
    for k, a in enumerate(real):
        s, d, t = res[3 * k:3 * k + 3]
        out[a] = {"symbol": decode_string(s) if s else a[2:8], "decimals": to_int(words(d)[0]) if d else 18,
                  "supply": to_int(words(t)[0]) if t else None}
    if ZERO in addrs:
        out[ZERO] = {"symbol": "ETH", "decimals": 18, "supply": None}
    return out


def price_of(sqrt_price_x96: int, dec0: int, dec1: int) -> float:
    """Human price of currency0 in currency1."""
    return (sqrt_price_x96 / 2 ** 96) ** 2 * 10 ** (dec0 - dec1)


def quote_side(key: dict) -> int | None:
    """0 or 1: which currency of the pair is the quote (USDG, then ETH/WETH); None if neither."""
    r0, r1 = QUOTE_RANK.get(key["currency0"], 0), QUOTE_RANK.get(key["currency1"], 0)
    if r0 == r1 == 0:
        return None
    return 0 if r0 > r1 else 1 if r1 > r0 else 0
