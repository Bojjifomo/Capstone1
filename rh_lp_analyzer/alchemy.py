"""Alchemy's Transfers API (alchemy_getAssetTransfers): a wallet's position NFT
transfers and its transactions to the PositionManager over any block range, up
to 1000 per request. Used when an Alchemy RPC is set and the network supports
it; anything else raises and the loader moves on."""
import calendar
import time

import config
from rpc import RPC


def url(cfg):
    return next((u for u in (cfg.get("CALL_RPC_URL"), cfg.get("RPC_URL")) if u and "alchemy.com" in u), None)


def _ts(s):
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) if s else None


def _pages(rpc, params):
    key = None
    for _ in range(100):
        res = rpc.call("alchemy_getAssetTransfers", [dict(params, **({"pageKey": key} if key else {}))])
        yield from res.get("transfers") or []
        key = res.get("pageKey")
        if not key:
            return


def wallet(u, addr, lo, hi):
    """-> (nft transfers [{block, log_index, tx, ts, token_id, from, to}], lp txs [{tx, block, ts}])"""
    rpc = RPC(u, workers=1)
    base = {"fromBlock": hex(lo), "toBlock": hex(hi), "withMetadata": True, "excludeZeroValue": False,
            "maxCount": hex(1000)}
    trs = []
    for side in ("toAddress", "fromAddress"):
        for t in _pages(rpc, dict(base, category=["erc721"], contractAddresses=[config.POSITION_MANAGER],
                                  **{side: addr})):
            tid = t.get("erc721TokenId") or t.get("tokenId")
            if tid is None:
                continue
            uid = (t.get("uniqueId") or "").split(":")
            trs.append({"block": int(t["blockNum"], 16), "log_index": int(uid[-1]) if len(uid) == 3 else 0,
                        "tx": t["hash"].lower(), "ts": _ts((t.get("metadata") or {}).get("blockTimestamp")),
                        "token_id": int(tid, 16), "from": (t.get("from") or config.ZERO).lower(),
                        "to": (t.get("to") or config.ZERO).lower()})
    txs = [{"tx": t["hash"].lower(), "block": int(t["blockNum"], 16),
            "ts": _ts((t.get("metadata") or {}).get("blockTimestamp"))}
           for t in _pages(rpc, dict(base, category=["external"], fromAddress=addr,
                                     toAddress=config.POSITION_MANAGER))]
    return trs, txs
