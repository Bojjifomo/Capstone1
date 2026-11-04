"""Tiny Meteora DLMM data API client (stdlib only, no key needed)."""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://dlmm.datapi.meteora.ag"
SOL_MINT = "So11111111111111111111111111111111111111112"


def _ssl_context() -> ssl.SSLContext:
    """macOS Pythons (python.org, some Anaconda setups) often ship without the system
    CA store, so HTTPS fails with CERTIFICATE_VERIFY_FAILED. certifi's bundle fixes that
    when it is installed (Anaconda has it); otherwise use the default store."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CONTEXT = _ssl_context()


def urlopen(req, timeout: float = 20, data: bytes | None = None):
    return urllib.request.urlopen(req, data=data, timeout=timeout, context=SSL_CONTEXT)


def get(path: str, params: dict | None = None, retries: int = 3) -> dict:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"User-Agent": "dlmm-terminal/1", "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=20) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"GET {url} failed: {exc}") from exc
            time.sleep(2 ** attempt)
    return {}


def pool(address: str) -> dict:
    return get(f"/pools/{address}")


def list_pools(filter_by: str, sort_by: str = "fee_tvl_ratio_24h:desc", page: int = 1, page_size: int = 100) -> list[dict]:
    return get("/pools", {"filter_by": filter_by, "sort_by": sort_by, "page": page, "page_size": page_size}).get("data", [])


def ohlcv(address: str, timeframe: str, start: int, end: int) -> list[dict]:
    return get(f"/pools/{address}/ohlcv", {"timeframe": timeframe, "start_time": start, "end_time": end}).get("data", [])


def open_portfolio(wallet: str) -> list[dict]:
    pools, page = [], 1
    while True:
        d = get("/portfolio/open", {"user": wallet, "page": page, "page_size": 50})
        pools += d.get("pools", [])
        if not d.get("hasNext"):
            return pools
        page += 1


def position_pnl(pool_address: str, wallet: str, status: str = "open") -> list[dict]:
    out, page = [], 1
    while True:
        d = get(f"/positions/{pool_address}/pnl", {"user": wallet, "status": status, "page": page, "page_size": 100})
        out += d.get("positions", [])
        if not d.get("hasNext"):
            return out
        page += 1
