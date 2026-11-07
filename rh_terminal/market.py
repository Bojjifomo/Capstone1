"""DexScreener: TVL, market cap, price changes, buys/sells, pool age, icon. Free, no key.

The chain index says which pools trade and what they earn; DexScreener fills in what the
chain cannot cheaply say (TVL in dollars across both sides, mcap, 24h history before the
index started). Pairs are fetched 30 ids per request and cached for CACHE_SEC.
"""
from __future__ import annotations

import threading
import time
import urllib.parse

from . import chain

BASE = "https://api.dexscreener.com"
CACHE_SEC = 60
_lock = threading.Lock()
_pairs: dict[str, tuple[float, dict | None]] = {}


def _pair_row(p: dict) -> dict:
    vol, tx, pc = p.get("volume") or {}, p.get("txns") or {}, p.get("priceChange") or {}
    t = lambda k: tx.get(k) or {}
    return {
        "pool": (p.get("pairAddress") or "").lower(),
        "dexId": p.get("dexId"), "labels": p.get("labels") or [],
        "base": {"address": (p.get("baseToken") or {}).get("address", "").lower(),
                 "symbol": (p.get("baseToken") or {}).get("symbol"), "name": (p.get("baseToken") or {}).get("name")},
        "quote": {"address": (p.get("quoteToken") or {}).get("address", "").lower(),
                  "symbol": (p.get("quoteToken") or {}).get("symbol")},
        "priceUsd": float(p["priceUsd"]) if p.get("priceUsd") else None,
        "priceNative": float(p["priceNative"]) if p.get("priceNative") else None,
        "tvlUsd": (p.get("liquidity") or {}).get("usd"),
        "vol": {"5m": vol.get("m5"), "1h": vol.get("h1"), "6h": vol.get("h6"), "24h": vol.get("h24")},
        "chg": {"5m": pc.get("m5"), "1h": pc.get("h1"), "6h": pc.get("h6"), "24h": pc.get("h24")},
        "txns": {k: {"buys": t(k).get("buys") or 0, "sells": t(k).get("sells") or 0} for k in ("m5", "h1", "h6", "h24")},
        "mcapUsd": p.get("marketCap") or p.get("fdv"),
        "createdAt": (p.get("pairCreatedAt") or 0) / 1000 or None,
        "icon": (p.get("info") or {}).get("imageUrl"),
    }


def pairs(ids: list[str], max_age: float = CACHE_SEC) -> dict[str, dict]:
    """{pool id: pair row}. Pools DexScreener does not know map to nothing; a failed batch
    keeps whatever was cached for those ids."""
    now = time.time()
    ids = [i.lower() for i in ids]
    with _lock:
        need = [i for i in ids if i not in _pairs or now - _pairs[i][0] > max_age]
    for k in range(0, len(need), 30):
        part = need[k:k + 30]
        try:
            j = chain.get_json(f"{BASE}/latest/dex/pairs/robinhood/{','.join(part)}", timeout=12)
        except Exception:
            continue
        got = {r["pool"]: r for r in (_pair_row(p) for p in (j.get("pairs") or []))}
        with _lock:
            for i in part:
                _pairs[i] = (now, got.get(i))
    with _lock:
        return {i: _pairs[i][1] for i in ids if i in _pairs and _pairs[i][1]}


def _robinhood_v4(p: dict) -> bool:
    return p.get("chainId") == "robinhood" and "uniswap" in str(p.get("dexId") or "") \
        and any(str(l).lower() == "v4" for l in (p.get("labels") or []))


def search(q: str) -> list[dict]:
    """Uniswap V4 pairs on Robinhood Chain for a name, symbol, token or pool address."""
    q = q.strip()
    if not q:
        return []
    if q.lower().startswith("0x") and len(q) == 66:
        return list(pairs([q], max_age=0).values())
    if q.lower().startswith("0x") and len(q) == 42:
        j = chain.get_json(f"{BASE}/latest/dex/tokens/{q}", timeout=12)
    else:
        j = chain.get_json(f"{BASE}/latest/dex/search?q={urllib.parse.quote(q)}", timeout=12)
    rows = [_pair_row(p) for p in (j.get("pairs") or []) if _robinhood_v4(p)]
    now = time.time()
    with _lock:
        for r in rows:
            _pairs[r["pool"]] = (now, r)
    return rows
