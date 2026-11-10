"""Elysium mainnet (99901) reads: JSON-RPC with fallback, Multicall3, v3 and v4 helpers.

Reads go to the public RPC first, then Alchemy (ALCHEMY_API_KEY) if that fails.
A 429, 403 or a timeout moves to the next endpoint. Standard library only.

Addresses below were checked against https://rpc.elysium.kinetiq.xyz on 9 Nov 2026:
each has code, and the wiring calls (factory / WETH9 / poolManager) match.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

from .keccak import keccak256, selector, topic

CHAIN_ID = 99901
PUBLIC_RPCS = ("https://rpc.elysium.kinetiq.xyz",)

# Uniswap v4 — developers.uniswap.org deployments, chain 99901. Code + poolManager() checked.
V4_POOL_MANAGER = "0xe2e105d3f7209a9da11f83ccf3e7398a753823f1"
V4_POSITION_MANAGER = "0x1d1c51b9d07fd27d36412a2b66cc23d19eb3e518"
V4_STATE_VIEW = "0x27ed73ae0652fdcf71d6793412a7525f32be9dfe"
V4_QUOTER = "0x379f556c5a748bd7cc9baf0f7a344094ac1814f5"
V4_UNIVERSAL_ROUTER = "0x1b341fdd55d56f8e08409a2c25ddfad93eb03c1a"   # Universal Router 2.2.0
V4_UNIVERSAL_ROUTER_212 = "0xa13f7611115752deb2b13813bdde16a5073e4b45"
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"

# ElysiumSwap v3 (docs.elysiumswap.finance, deployed 31 Oct 2026). factory()/WETH9() checked.
ES_V3_FACTORY = "0x7266c0ecff2a687b4ad0ead7b24840929540b697"
ES_V3_ROUTER = "0x9fae201fa4c0740b1b92debecddffe74c1ecd8b3"          # classic SwapRouter (deadline inside the struct)
ES_V3_QUOTER = "0xc256c98819bb29661dd66e3dbce3ca71875d8143"
ES_V3_NPM = "0xc6dc519451f58dfd27bb2920eefae57597bd91f5"
ES_V2_FACTORY = "0xc48c733f9ceac05d79d0d583a15f3c68a14b8715"
ES_V2_ROUTER = "0x194a77bca0ada3eb15d7b3af201aca197dbe3b26"
ES_AGGREGATOR = "0xd9106be99e93cc88a0c81522f416ff4e8394fe51"
# Pool init code hash from the ElysiumSwap docs. CREATE2 of the USDC/WHYPE 1% pool matches getPool.
V3_INIT_CODE_HASH = "e34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54"

# Official Uniswap v3 on Elysium (separate factory from ElysiumSwap). NPM.factory() and WETH9() checked.
# Uniswap's v3 deployments page does not list 99901; these addresses are the ones NPM/UR bytecode point at.
UNI_V3_FACTORY = "0x1707327f626496a7b5b3872e7e4d2879df7d7a9f"
UNI_V3_NPM = "0xf025e0fe9e331a0ef05c2ad3c4e9c64b625cda6f"

# USDC/WHYPE 1% on ElysiumSwap. getPool and CREATE2 both return this. Used as a HYPE price fallback.
REFERENCE_HYPE_POOL = "0xec2e846b66eee17bc856129aa9e9b1baee4d521d"

ZERO = "0x" + "0" * 40
USDC = "0xb47c4c8f73f009cc0c0d3f92b388d13e293f0b65"
WHYPE = "0xba876237b0f021a6c6d852d6c26e107997cc5e81"

# Quote rank: USDC first, then native HYPE / WHYPE. The other token is the one being LP'd.
QUOTE_RANK = {USDC: 2, ZERO: 1, WHYPE: 1}
QUOTE_SYMBOL = {USDC: "USDC", ZERO: "HYPE", WHYPE: "WHYPE"}
DECIMALS = {USDC: 6, ZERO: 18, WHYPE: 18}

V3_VENUES = {
    "elysiumswap": {"factory": ES_V3_FACTORY, "npm": ES_V3_NPM, "router": ES_V3_ROUTER, "quoter": ES_V3_QUOTER, "swap": "router"},
    "uniswap-v3": {"factory": UNI_V3_FACTORY, "npm": UNI_V3_NPM, "router": None, "quoter": None, "swap": "universal"},
}
FACTORY_VENUE = {v["factory"]: name for name, v in V3_VENUES.items()}

SIG = {
    "slot0": "slot0()", "tickSpacing": "tickSpacing()", "liquidity": "liquidity()",
    "token0": "token0()", "token1": "token1()", "fee": "fee()", "factory": "factory()",
    "positions": "positions(uint256)", "collect": "collect((uint256,address,uint128,uint128))",
    "balanceOf": "balanceOf(address)", "tokenOfOwnerByIndex": "tokenOfOwnerByIndex(address,uint256)",
    "ownerOf": "ownerOf(uint256)", "symbol": "symbol()", "decimals": "decimals()", "totalSupply": "totalSupply()",
    "getSlot0": "getSlot0(bytes32)", "getLiquidity": "getLiquidity(bytes32)", "poolKeys": "poolKeys(bytes25)",
    "getPoolAndPositionInfo": "getPoolAndPositionInfo(uint256)", "getPositionLiquidity": "getPositionLiquidity(uint256)",
    "getFeeGrowthInside": "getFeeGrowthInside(bytes32,int24,int24)",
    "getPositionInfo": "getPositionInfo(bytes32,address,int24,int24,bytes32)",
    "tryAggregate": "tryAggregate(bool,(address,bytes)[])", "getEthBalance": "getEthBalance(address)",
    "getPool": "getPool(address,address,uint24)",
}
SEL = {k: selector(v) for k, v in SIG.items()}
TOPIC = {
    "v4Swap": topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"),
    "v4Init": topic("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"),
    "v3Swap": topic("Swap(address,address,int256,int256,uint160,uint128,int24)"),
    "v3PoolCreated": topic("PoolCreated(address,address,uint24,int24,address)"),
    "Transfer": topic("Transfer(address,address,uint256)"),
}
DYNAMIC_FEE = 0x800000


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CONTEXT = _ssl_context()
UA = "elysium-terminal/1"


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
            if e.code not in (403, 429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


_meter = None


def _meter_hit(url, outcome="ok", n=1):
    global _meter
    if _meter is None:
        _meter = False
        try:
            import importlib.util
            import sys
            from pathlib import Path
            mod = sys.modules.get("rpc_meter")
            if mod is None:
                path = Path(__file__).resolve().parents[1] / "rpc_monitor" / "meter.py"
                spec = importlib.util.spec_from_file_location("rpc_meter", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                sys.modules["rpc_meter"] = mod
            _meter = mod.hit
        except Exception:
            pass
    if _meter:
        _meter(os.environ.get("RPC_METER_BOT") or "elysium_terminal", url, outcome, n)


class RpcError(Exception):
    pass


def rpc_urls() -> list[str]:
    urls = list(PUBLIC_RPCS)
    custom = (os.environ.get("ELYSIUM_RPC_URL") or "").strip()
    if custom:
        urls.append(custom)
    key = (os.environ.get("ALCHEMY_API_KEY") or "").strip()
    if key and all(c.isalnum() or c in "-_" for c in key):
        urls.append(f"https://elysium-mainnet.g.alchemy.com/v2/{key}")
    return urls


_stats_lock = threading.Lock()
STATS: dict = {"calls": 0, "byHost": {}, "errors": 0}
_cool: dict[str, float] = {}


def _host(url: str) -> str:
    return "alchemy" if "alchemy" in url else url.split("/")[2]


def rpc(method: str, params: list, timeout: float = 30, _round: int = 0):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    fails = []
    urls = rpc_urls()
    now = time.time()
    order = [u for u in urls if _cool.get(u, 0) <= now] + [u for u in urls if _cool.get(u, 0) > now]
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
            _meter_hit(url, "limited" if e.code in (403, 429) else "error")
            fails.append(f"{_host(url)} HTTP {e.code}")
            _cool[url] = time.time() + (20 if e.code in (403, 429) else 10)
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            _meter_hit(url, "error")
            fails.append(f"{_host(url)}: {str(e)[:60]}")
            _cool[url] = time.time() + 10
            continue
        if "error" in j:
            msg = str((j["error"] or {}).get("message") or j["error"])
            if "rate" in msg.lower() or "too many" in msg.lower() or "forbidden" in msg.lower():
                _meter_hit(url, "limited")
                _cool[url] = time.time() + 20
                fails.append(f"{_host(url)}: {msg[:80]}")
                continue
            _meter_hit(url)
            raise RpcError(msg)
        _meter_hit(url)
        return j.get("result")
    if _round < 2 and fails:
        time.sleep(2 + 2 * _round)
        for u in urls:
            _cool.pop(u, None)
        return rpc(method, params, timeout, _round + 1)
    with _stats_lock:
        STATS["errors"] += 1
    raise RpcError("; ".join(fails) or "no RPC endpoint")


def chain_id() -> int:
    return int(rpc("eth_chainId", []), 16)


def block_number() -> int:
    return int(rpc("eth_blockNumber", []), 16)


def block_time(n: int | str) -> int:
    b = rpc("eth_getBlockByNumber", [hex(n) if isinstance(n, int) else n, False])
    return int(b["timestamp"], 16)


def eth_call(to: str, data: str, block: str = "latest", sender: str | None = None) -> str:
    tx = {"to": to, "data": data if data.startswith("0x") else "0x" + data}
    if sender:
        tx["from"] = sender
    return rpc("eth_call", [tx, block])


def get_logs(address, topics: list, start: int, end: int, chunk: int = 2000, pause: float = 0.05) -> list[dict]:
    """Logs in [start, end]. `address` may be a string, a list, or None (topic only).
    A chunk the node refuses (too many logs, too wide) is halved and retried."""
    if start > end:
        return []
    out = []
    a = start
    step = max(1, chunk)
    while a <= end:
        b = min(end, a + step - 1)
        filt = {"topics": topics, "fromBlock": hex(a), "toBlock": hex(b)}
        if address:
            filt["address"] = address
        try:
            out += rpc("eth_getLogs", [filt], timeout=60) or []
        except RpcError as e:
            msg = str(e).lower()
            if any(s in msg for s in ("exceed", "limit", "range", "narrow", "too many", "10000")) and step > 50:
                step = max(50, step // 2)
                continue
            raise
        a = b + 1
        if pause and a <= end:
            time.sleep(pause)
    return out


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
        return bytes.fromhex(h).rstrip(b"\x00").decode("utf-8", "replace")
    except Exception:
        return ""


def pool_id(c0: str, c1: str, fee: int, tick_spacing: int, hooks: str) -> str:
    enc = addr_word(c0) + addr_word(c1) + u256(fee) + u256(tick_spacing) + addr_word(hooks)
    return "0x" + keccak256(bytes.fromhex(enc)).hex()


def v3_pool_address(factory: str, token0: str, token1: str, fee: int, init_hash: str = V3_INIT_CODE_HASH) -> str:
    """CREATE2 address of a Uniswap v3 pool (abi.encode salt + init code hash)."""
    salt = keccak256(bytes.fromhex(addr_word(token0) + addr_word(token1) + u256(fee)))
    raw = b"\xff" + bytes.fromhex(factory.lower().replace("0x", "")) + salt + bytes.fromhex(init_hash)
    return "0x" + keccak256(raw).hex()[-40:]


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


def decode_pool_key(ret: str | None) -> dict | None:
    if not ret or len(ret) < 2 + 64 * 5:
        return None
    w = words(ret)
    c0, c1 = word_addr(w[0]), word_addr(w[1])
    if c0 == ZERO and c1 == ZERO:
        return None
    return {"dex": "v4", "venue": "uniswap-v4", "currency0": c0, "currency1": c1,
            "fee": to_int(w[2], 24), "tickSpacing": to_int(w[3], 24, True), "hooks": word_addr(w[4])}


def pool_keys(ids: list[str]) -> dict[str, dict | None]:
    calls = [(V4_POSITION_MANAGER, SEL["poolKeys"] + i[2:52].ljust(64, "0")) for i in ids]
    return {i.lower(): decode_pool_key(r) for i, r in zip(ids, multicall(calls))}


def decode_v4_init(log: dict) -> tuple[str, dict]:
    pid = log["topics"][1].lower()
    w = words(log["data"])
    key = {"dex": "v4", "venue": "uniswap-v4",
           "currency0": "0x" + log["topics"][2][-40:], "currency1": "0x" + log["topics"][3][-40:],
           "fee": to_int(w[0], 24), "tickSpacing": to_int(w[1], 24, True), "hooks": word_addr(w[2]),
           "createdBlock": int(log["blockNumber"], 16)}
    return pid, key


def v4_keys_from_init(ids: list[str]) -> dict[str, dict]:
    """Initialize logs for pool ids poolKeys() does not know (no position minted yet)."""
    out = {}
    ids = [i.lower() for i in ids]
    for i in range(0, len(ids), 20):
        part = ids[i:i + 20]
        head = block_number()
        logs = get_logs(V4_POOL_MANAGER, [TOPIC["v4Init"], part], 1, head, chunk=head, pause=0)
        for lg in logs:
            pid, key = decode_v4_init(lg)
            if pid in part:
                out[pid] = key
    return out


def decode_slot0_v4(ret: str | None) -> dict | None:
    if not ret or len(ret) < 2 + 64 * 4:
        return None
    w = words(ret)
    return {"sqrtPriceX96": to_int(w[0], 160), "tick": to_int(w[1], 24, True),
            "protocolFee": to_int(w[2], 24), "lpFee": to_int(w[3], 24)}


def decode_slot0_v3(ret: str | None) -> dict | None:
    if not ret or len(ret) < 2 + 64 * 6:
        return None
    w = words(ret)
    return {"sqrtPriceX96": to_int(w[0], 160), "tick": to_int(w[1], 24, True),
            "feeProtocol": to_int(w[5], 8), "lpFee": None}


def slot0s_v4(ids: list[str]) -> dict[str, dict | None]:
    if not ids:
        return {}
    return {i.lower(): decode_slot0_v4(r) for i, r in zip(ids, multicall([(V4_STATE_VIEW, SEL["getSlot0"] + i[2:]) for i in ids]))}


def slot0s_v3(pools: list[str]) -> dict[str, dict | None]:
    if not pools:
        return {}
    return {p.lower(): decode_slot0_v3(r) for p, r in zip(pools, multicall([(p, SEL["slot0"]) for p in pools]))}


def decode_pool_created(log: dict, venue: str) -> tuple[str, dict]:
    spec = V3_VENUES[venue]
    w = words(log["data"])
    pool = word_addr(w[1])
    key = {"dex": "v3", "venue": venue, "currency0": "0x" + log["topics"][1][-40:],
           "currency1": "0x" + log["topics"][2][-40:], "fee": int(log["topics"][3], 16),
           "tickSpacing": to_int(w[0], 24, True), "hooks": ZERO, "pool": pool,
           "npm": spec["npm"], "router": spec["router"], "createdBlock": int(log["blockNumber"], 16)}
    return pool, key


def v3_keys(pools: list[str]) -> dict[str, dict | None]:
    """token0/token1/fee/tickSpacing/factory for pools that were not in the PoolCreated index yet."""
    calls = []
    for p in pools:
        calls += [(p, SEL["token0"]), (p, SEL["token1"]), (p, SEL["fee"]), (p, SEL["tickSpacing"]), (p, SEL["factory"])]
    res = multicall(calls) if calls else []
    out = {}
    for k, p in enumerate(pools):
        t0, t1, fee, ts, fac = res[5 * k:5 * k + 5]
        if not (t0 and t1 and fee and fac):
            out[p.lower()] = None
            continue
        factory = word_addr(words(fac)[0])
        venue = FACTORY_VENUE.get(factory)
        if not venue:
            out[p.lower()] = None
            continue
        spec = V3_VENUES[venue]
        out[p.lower()] = {"dex": "v3", "venue": venue, "currency0": word_addr(words(t0)[0]),
                          "currency1": word_addr(words(t1)[0]), "fee": to_int(words(fee)[0], 24),
                          "tickSpacing": to_int(words(ts)[0], 24, True) if ts else 0, "hooks": ZERO,
                          "pool": p.lower(), "npm": spec["npm"], "router": spec["router"]}
    return out


def token_meta(addrs: list[str]) -> dict[str, dict]:
    out = {}
    real = [a for a in addrs if a != ZERO]
    calls = []
    for a in real:
        calls += [(a, SEL["symbol"]), (a, SEL["decimals"]), (a, SEL["totalSupply"])]
    res = multicall(calls) if calls else []
    for k, a in enumerate(real):
        s, d, t = res[3 * k:3 * k + 3]
        known = DECIMALS.get(a)
        out[a] = {"symbol": QUOTE_SYMBOL.get(a) or (decode_string(s) if s else a[2:8]),
                  "decimals": known if known is not None else (to_int(words(d)[0]) if d else 18),
                  "supply": to_int(words(t)[0]) if t else None}
    if ZERO in addrs:
        out[ZERO] = {"symbol": "HYPE", "decimals": 18, "supply": None}
    return out


def price_of(sqrt_price_x96: int, dec0: int, dec1: int) -> float:
    """Human price of currency0 in currency1."""
    return (sqrt_price_x96 / 2 ** 96) ** 2 * 10 ** (dec0 - dec1)


def quote_side(key: dict) -> int | None:
    """0 or 1: which currency is the quote (USDC, then HYPE/WHYPE). None if neither."""
    r0, r1 = QUOTE_RANK.get(key["currency0"], 0), QUOTE_RANK.get(key["currency1"], 0)
    if r0 == r1 == 0:
        return None
    return 0 if r0 > r1 else 1 if r1 > r0 else 0


def hype_usd_from_slot() -> float | None:
    """USDC per HYPE from the ElysiumSwap USDC/WHYPE 1% pool, if the index has no trade yet."""
    try:
        s0 = decode_slot0_v3(eth_call(REFERENCE_HYPE_POOL, SEL["slot0"]))
    except Exception:
        return None
    if not s0 or not s0["sqrtPriceX96"]:
        return None
    p = price_of(s0["sqrtPriceX96"], 6, 18)   # WHYPE per USDC
    return (1 / p) if p else None
