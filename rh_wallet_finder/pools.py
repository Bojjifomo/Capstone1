"""Pool finder: where an LP earns the most fee per dollar right now.

Pools and their volume / TVL / market cap come from GeckoTerminal (free, no key,
covers every DEX on Robinhood Chain). The fee tier comes from the chain itself,
because the pool name lies often enough to matter:

- Uniswap V4: StateView.getSlot0 (the fee the pool charges now). Pools with a
  dynamic-fee hook show 0 there, so their fee is the median `fee` of their
  Swap events over the last hour.
- Uniswap / SushiSwap V3: the pool's fee().
- Other DEXes (Aerodrome, Ramses, …): the tier in the pool name.

Plenty of V4 pools with millions in "volume" charge 0% (or 0.0001%): an LP
earns nothing there. Reading the fee on chain is what drops them.

Per pool, from the fee and volume over the pool's TVL:
  fee/1k 1h   $ earned per $1,000 of liquidity in the last hour
  fee/1k 24h  $ earned per $1,000 of liquidity in the last 24 hours
  APR 1h / APR 24h  the same, annualised (simple, no compounding)
  pace        last hour's volume vs its 24h hourly average (2.0 = twice as busy)
  blend       geometric mean of APR 1h and APR 24h: high only if it pays now
              and paid all day (the default sort)

TVL is every LP in the pool, in range or not, so a tight range that stays in
range earns more than these numbers, and a range that falls out earns nothing.

Standard library only.
"""
import calendar
import json
import math
import statistics
import time
import urllib.error
import urllib.request

import chain
import config
from keccak import topic

GT_API = "https://api.geckoterminal.com/api/v2"
GT_NETWORK = "robinhood"
GT_POOL_LINK = "https://www.geckoterminal.com/robinhood/pools/{addr}"
DEXSCREENER_LINK = "https://dexscreener.com/robinhood/{addr}"
# Used for the fee reads when .env has no RPC: the chain's own public endpoint.
PUBLIC_RPC = "https://rpc.mainnet.chain.robinhood.com"

# Tokens that are "the money side" of a pair: the other token is the one whose
# market cap counts. Only addresses, never symbols.
QUOTES = {config.ZERO, config.WETH, config.USDG}

SEL_FEE = "ddca3f43"  # UniswapV3Pool.fee()
SWAP_V4 = topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
DYNAMIC_FEE_FLAG = 0x800000

CACHE = config.DATA_DIR / "pools.json"
CACHE_MINUTES = 5

DEFAULTS = {"min_mcap": 500_000, "min_tvl": 5_000, "pages": 10, "sort": "blend"}
SORTS = {
    "blend": "blend", "1h": "apr_1h", "24h": "apr_24h", "pace": "pace",
    "tvl": "tvl", "vol1h": "vol_1h", "vol24h": "vol_24h",
}


class PoolsError(Exception):
    pass


# ---- GeckoTerminal ---------------------------------------------------------
def _gt(path, params=None, retries=5):
    """GET one GeckoTerminal endpoint. The free API allows a few calls a
    minute, so a 429 waits and tries again instead of failing the scan."""
    q = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{GT_API}{path}" + (f"?{q}" if q else "")
    req = urllib.request.Request(url, headers={"accept": "application/json",
                                               "user-agent": "rh-wallet-finder/1.0"})
    wait = 15
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(wait)
                wait = min(wait * 2, 60)
                continue
            body = e.read()[:300].decode("utf-8", "replace")
            raise PoolsError(f"GeckoTerminal HTTP {e.code}: {body}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(3)
                continue
            raise PoolsError(f"Can't reach GeckoTerminal: {e}") from e
    raise PoolsError("GeckoTerminal kept rate limiting")


def fetch_raw(pages=10, progress=None):
    """Top pools by 24h volume (GeckoTerminal serves at most 10 pages of 20)
    plus the pools trending in the last hour, so a pool that only woke up
    recently is in too. -> (pool dicts, token dicts by id)"""
    jobs = [("/pools", {"page": p, "sort": "h24_volume_usd_desc"}) for p in range(1, pages + 1)]
    jobs += [("/trending_pools", {"page": p, "duration": "1h"}) for p in (1, 2)]
    pools, tokens = {}, {}
    for i, (path, params) in enumerate(jobs):
        if progress:
            progress(i, len(jobs))
        d = _gt(f"/networks/{GT_NETWORK}{path}", {**params, "include": "base_token,quote_token,dex"})
        for t in d.get("included") or []:
            if t.get("type") == "token":
                tokens[t["id"]] = t["attributes"]
        for p in d.get("data") or []:
            pools.setdefault(p["attributes"]["address"].lower(), p)
        if not d.get("data") and path == "/pools":
            break
        time.sleep(2.2)  # stay under the free rate limit
    return list(pools.values()), tokens


def fetch_token_mcaps(addrs):
    """{addr: (market cap or FDV, "mcap" | "fdv")} for tokens that are the quote
    side of their pool (the pool itself only carries the base token's)."""
    out, addrs = {}, sorted(set(addrs))
    for i in range(0, len(addrs), 30):
        d = _gt(f"/networks/{GT_NETWORK}/tokens/multi/{','.join(addrs[i:i + 30])}")
        for t in d.get("data") or []:
            a = t["attributes"]
            out[a["address"].lower()] = _cap(a.get("market_cap_usd"), a.get("fdv_usd"))
        time.sleep(2.2)
    return out


# ---- parsing ---------------------------------------------------------------
def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _cap(mcap, fdv):
    m, f = _f(mcap), _f(fdv)
    if m:
        return m, "mcap"
    if f:
        return f, "fdv"
    return None, None


def name_fee_pct(name):
    """'PONS / USDG 0.3%' -> 0.3; no tier in the name -> None."""
    last = (name or "").rsplit(" ", 1)[-1]
    if last.endswith("%"):
        return _f(last[:-1])
    return None


def pair_name(name):
    tail = (name or "").rsplit(" ", 1)
    return tail[0] if len(tail) == 2 and tail[1].endswith("%") else name


def parse_pool(p, tokens):
    a, rel = p["attributes"], p["relationships"]
    base_id, quote_id = rel["base_token"]["data"]["id"], rel["quote_token"]["data"]["id"]
    base, quote = tokens.get(base_id, {}), tokens.get(quote_id, {})
    base_addr = (base.get("address") or base_id.split("_", 1)[-1]).lower()
    quote_addr = (quote.get("address") or quote_id.split("_", 1)[-1]).lower()
    vol, tx = a.get("volume_usd") or {}, a.get("transactions") or {}
    chg = a.get("price_change_percentage") or {}
    # The asset is the side that isn't ETH / USDG; its market cap is what the
    # filter checks. Both sides money (ETH/USDG): no market cap, always passes.
    if base_addr not in QUOTES:
        asset, side = base_addr, "base"
    elif quote_addr not in QUOTES:
        asset, side = quote_addr, "quote"
    else:
        asset, side = None, None
    mcap, mcap_src = _cap(a.get("market_cap_usd"), a.get("fdv_usd")) if side == "base" else (None, None)
    t24, t1 = tx.get("h24") or {}, tx.get("h1") or {}
    created = a.get("pool_created_at")
    try:
        created_ts = calendar.timegm(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ")) if created else None
    except ValueError:
        created_ts = None
    addr = a["address"].lower()
    return {
        "pool": addr,
        "name": a.get("name"),
        "pair": pair_name(a.get("name")),
        "dex": rel["dex"]["data"]["id"].removesuffix("-robinhood"),
        "base": base.get("symbol"), "quote": quote.get("symbol"),
        "base_addr": base_addr, "quote_addr": quote_addr,
        "asset": asset, "asset_side": side,
        "mcap": mcap, "mcap_src": mcap_src,
        "tvl": _f(a.get("reserve_in_usd")) or 0.0,
        "vol_1h": _f(vol.get("h1")) or 0.0, "vol_6h": _f(vol.get("h6")) or 0.0,
        "vol_24h": _f(vol.get("h24")) or 0.0,
        "tx_1h": (t1.get("buys") or 0) + (t1.get("sells") or 0),
        "tx_24h": (t24.get("buys") or 0) + (t24.get("sells") or 0),
        "traders_24h": (t24.get("buyers") or 0) + (t24.get("sellers") or 0),
        "chg_1h": _f(chg.get("h1")), "chg_24h": _f(chg.get("h24")),
        "created_ts": created_ts,
        "name_fee_pct": name_fee_pct(a.get("name")),
        "gt": GT_POOL_LINK.format(addr=addr), "dexscreener": DEXSCREENER_LINK.format(addr=addr),
    }


# ---- fee tiers from the chain ----------------------------------------------
def _is_v4(p):
    return len(p["pool"]) == 66


def _reads_fee_onchain(p):
    return _is_v4(p) or p["dex"].startswith(("uniswap-v3", "sushiswap"))


def onchain_fees(pools, rpc, swap_window_s=3600):
    """{pool: (fee pips or None, source)}. 1 pip = 0.0001%, so 3000 = 0.3%."""
    picks = [p for p in pools if _reads_fee_onchain(p)]
    calls = []
    for p in picks:
        if _is_v4(p):
            calls += [(config.STATE_VIEW, chain.cd_slot0(p["pool"])),
                      (config.POSITION_MANAGER, chain.cd_pool_keys(p["pool"]))]
        else:
            calls += [(p["pool"], "0x" + SEL_FEE)] * 2
    out, dynamic = {}, []
    for i in range(0, len(calls), 200):
        ret = rpc.call("eth_call", [{"to": config.MULTICALL3, "data": chain.encode_aggregate3(calls[i:i + 200])},
                                    "latest"])
        res = chain.decode_aggregate3(ret)
        for j in range(0, len(res), 2):
            p = picks[(i + j) // 2]
            a, b = res[j], res[j + 1]
            if not _is_v4(p):
                if a:
                    out[p["pool"]] = (int(a, 16), "chain")
                continue
            s0 = chain.decode_slot0(a) if a else None
            if not s0 or not s0["sqrtp"]:
                continue  # not in this PoolManager (another V4 deployment): fall back to the name
            key = chain.decode_pool_key(b) if b else None
            if key and key["c0"] == key["c1"]:
                key = None  # never minted through the PositionManager: no PoolKey on record
            if (key and key["fee"] & DYNAMIC_FEE_FLAG) or (not key and not s0["lp_fee"]):
                dynamic.append(p["pool"])
            else:
                out[p["pool"]] = (s0["lp_fee"], "chain")
    if dynamic:
        out.update(swap_fees(dynamic, rpc, swap_window_s))
    return out


def swap_fees(pool_ids, rpc, window_s=3600):
    """Median fee charged by each V4 pool's swaps over the last window_s seconds
    (the Swap event carries it): what a dynamic-fee hook actually charged."""
    head = int(rpc.call("eth_blockNumber", []), 16)
    probe = 10_000
    t_head = int(rpc.call("eth_getBlockByNumber", [hex(head), False])["timestamp"], 16)
    t_old = int(rpc.call("eth_getBlockByNumber", [hex(head - probe), False])["timestamp"], 16)
    per_block = max((t_head - t_old) / probe, 0.01)
    span = int(window_s / per_block)
    fees = {pid: [] for pid in pool_ids}
    for i in range(0, len(pool_ids), 50):
        ids = pool_ids[i:i + 50]
        start, step = head - span, span
        while start <= head:
            end = min(start + step, head)
            try:
                logs = rpc.call("eth_getLogs", [{"address": config.POOL_MANAGER, "topics": [SWAP_V4, ids],
                                                 "fromBlock": hex(start), "toBlock": hex(end)}])
            except Exception:  # noqa: BLE001  range too large for this RPC: halve it
                if step <= 1000:
                    raise
                step //= 2
                continue
            for lg in logs:
                fees[lg["topics"][1].lower()].append(int(chain.words(lg["data"])[5], 16))
            start = end + 1
    return {pid: ((int(statistics.median(f)), "swaps") if f else (None, "dynamic, no swaps"))
            for pid, f in fees.items()}


# ---- metrics ---------------------------------------------------------------
def compute(p):
    """Fee yield of one pool. fee_pct None (unknown) -> yields None."""
    fee, tvl = p.get("fee_pct"), p["tvl"]
    rate = fee / 100 if fee is not None else None
    ok = rate is not None and tvl > 0
    f1h = p["vol_1h"] * rate if rate is not None else None
    f24 = p["vol_24h"] * rate if rate is not None else None
    p["fees_1h"], p["fees_24h"] = f1h, f24
    p["per1k_1h"] = f1h / tvl * 1000 if ok else None
    p["per1k_24h"] = f24 / tvl * 1000 if ok else None
    p["apr_1h"] = f1h * 24 * 365 / tvl * 100 if ok else None
    p["apr_24h"] = f24 * 365 / tvl * 100 if ok else None
    p["pace"] = p["vol_1h"] * 24 / p["vol_24h"] if p["vol_24h"] else None
    p["turnover_24h"] = p["vol_24h"] / tvl if tvl else None
    p["blend"] = math.sqrt(p["apr_1h"] * p["apr_24h"]) if ok and p["apr_1h"] > 0 and p["apr_24h"] > 0 else (
        0.0 if ok else None)
    flags = []
    if p["traders_24h"] and p["traders_24h"] < 20:
        flags.append("few traders")
    if p["turnover_24h"] and p["turnover_24h"] > 200:
        flags.append("volume ≫ TVL")
    if p.get("created_ts") and time.time() - p["created_ts"] < 86400:
        flags.append("new <24h")
    p["flags"] = flags
    return p


def keep(p, min_mcap, min_tvl, min_vol_1h=0, quote=None):
    if p["tvl"] < min_tvl or p["vol_1h"] < min_vol_1h:
        return False
    if p["asset"] and (p["mcap"] or 0) < min_mcap:
        return False
    if quote and quote.upper() not in {(p["base"] or "").upper(), (p["quote"] or "").upper()}:
        return False
    return True


# ---- the scan --------------------------------------------------------------
def _rpc(cfg):
    from rpc import RPC
    return RPC(cfg.get("CALL_RPC_URL") or cfg.get("RPC_URL") or PUBLIC_RPC, workers=2)


def scan(cfg, pages=10, progress=None, rpc=None):
    """Every pool GeckoTerminal lists for the chain, with on-chain fees and
    yields, unfiltered. Saved to data/pools.json."""
    raw, tokens = fetch_raw(pages, progress)
    pools = [parse_pool(p, tokens) for p in raw]
    need = {p["asset"] for p in pools if p["asset_side"] == "quote"}
    if need:
        caps = fetch_token_mcaps(need)
        for p in pools:
            if p["asset_side"] == "quote":
                p["mcap"], p["mcap_src"] = caps.get(p["asset"], (None, None))
    errors = []
    try:
        fees = onchain_fees(pools, rpc or _rpc(cfg))
    except Exception as e:  # noqa: BLE001  no chain: names only
        fees = {}
        errors.append(f"on-chain fee read failed, using the tier in the pool name: {e}")
    for p in pools:
        pips, src = fees.get(p["pool"], (None, None))
        if pips is not None:
            p["fee_pct"], p["fee_src"] = pips / 10_000, src
        elif p["name_fee_pct"] is not None:
            p["fee_pct"], p["fee_src"] = p["name_fee_pct"], "name"
        else:
            p["fee_pct"], p["fee_src"] = None, src or "unknown"
        compute(p)
    res = {"at": time.time(), "pools": pools, "errors": errors}
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(res))
    except OSError:
        pass
    return res


def cached(max_age_min=CACHE_MINUTES):
    try:
        res = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return None
    return res if time.time() - res.get("at", 0) <= max_age_min * 60 else None


def best(cfg, min_mcap=DEFAULTS["min_mcap"], min_tvl=DEFAULTS["min_tvl"], sort=DEFAULTS["sort"], n=25,
         min_vol_1h=0, quote=None, fresh=False, pages=DEFAULTS["pages"], progress=None):
    """The ranked board. Reuses a scan younger than CACHE_MINUTES unless fresh."""
    res = (None if fresh else cached()) or scan(cfg, pages, progress)
    key = SORTS.get(sort, "blend")
    pools = res["pools"]
    shown = [p for p in pools if keep(p, min_mcap, min_tvl, min_vol_1h, quote)]
    unknown = [p for p in shown if p["fee_pct"] is None]
    ranked = [p for p in shown if p["fee_pct"] is not None and (p.get("fees_24h") or 0) > 0]
    zero = [p for p in shown if p["fee_pct"] is not None and not (p.get("fees_24h") or 0) > 0]
    ranked.sort(key=lambda p: p.get(key) or 0, reverse=True)
    return {"at": res["at"], "scanned": len(pools), "passed_filters": len(shown),
            "filters": {"min_mcap": min_mcap, "min_tvl": min_tvl, "min_vol_1h": min_vol_1h, "quote": quote,
                        "sort": sort},
            "zero_fee": len(zero), "fee_unknown": [p["name"] for p in unknown],
            "errors": res.get("errors") or [], "rows": ranked[:n]}
