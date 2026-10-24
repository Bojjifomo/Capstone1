"""Robinhood Chain's Blockscout explorer API: a wallet's position NFT transfers
and its transactions to the PositionManager, a page (50) per request, newest
first, without spending RPC quota. Anything that isn't a JSON answer (down,
rate limited, a Cloudflare check) raises ExplorerError, and the loader falls
back to eth_getLogs.
"""
import calendar
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config

API = config.EXPLORER + "/api/v2"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
MAX_PAGES = 200  # 10k items: a bot past that is read through the RPC instead


class ExplorerError(Exception):
    pass


def get(path, params=None, timeout=20):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise ExplorerError(f"explorer answered HTTP {e.code}") from e
        except (OSError, ValueError) as e:  # timeouts, resets, an HTML challenge page instead of JSON
            if attempt < 2 and not isinstance(e, ValueError):
                time.sleep(1)
                continue
            raise ExplorerError(f"explorer unavailable ({type(e).__name__})") from e
    raise ExplorerError("explorer unavailable")


def iso_ts(s):
    """'2026-10-20T08:15:30.000000Z' -> unix seconds."""
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


def _addr(x):
    if isinstance(x, dict):
        x = x.get("hash") or x.get("address_hash") or x.get("address")
    return (x or "").lower()


def _pages(path, params, since_ts):
    """Items of a paged list, newest first, until they get older than since_ts."""
    params = dict(params)
    for _ in range(MAX_PAGES):
        page = get(path, params)
        items = page.get("items")
        if items is None:
            raise ExplorerError("unexpected explorer answer")
        for it in items:
            if it.get("timestamp") and iso_ts(it["timestamp"]) < since_ts:
                return
            yield it
        nxt = page.get("next_page_params")
        if not nxt:
            return
        params.update({k: v for k, v in nxt.items() if v is not None})
    raise ExplorerError("too many pages")


def nft_transfers(addr, since_ts):
    """The wallet's PositionManager NFT transfers since since_ts ->
    [{block, log_index, tx, ts, token_id, from, to}]"""
    out = []
    for it in _pages(f"/addresses/{addr}/token-transfers", {"type": "ERC-721", "token": config.POSITION_MANAGER},
                     since_ts):
        tok = it.get("token") or {}
        if _addr(tok.get("address_hash") or tok.get("address")) not in ("", config.POSITION_MANAGER):
            continue
        tid = (it.get("total") or {}).get("token_id")
        if tid is None or it.get("block_number") is None:
            continue
        out.append({"block": int(it["block_number"]), "log_index": int(it.get("log_index") or 0),
                    "tx": it["transaction_hash"].lower(), "ts": iso_ts(it["timestamp"]), "token_id": int(tid),
                    "from": _addr(it.get("from")), "to": _addr(it.get("to"))})
    return out


def lp_transactions(addr, since_ts):
    """Transactions the wallet sent to the PositionManager since since_ts (every
    add, remove and fee claim it did itself) -> [{tx, block, ts}]"""
    out = []
    for it in _pages(f"/addresses/{addr}/transactions", {"filter": "from"}, since_ts):
        if _addr(it.get("to")) == config.POSITION_MANAGER and it.get("block_number") is not None:
            out.append({"tx": it["hash"].lower(), "block": int(it["block_number"]), "ts": iso_ts(it["timestamp"])})
    return out
