"""A wallet's Uniswap V4 positions on Robinhood Chain, one row per ladder (pool), from chain state.

Which position NFTs the wallet holds comes from the PositionManager's Transfer logs to the
wallet (10M-block windows, cached and extended incrementally), confirmed with ownerOf. No
explorer, no key. Everything shown is read from chain: the PoolKey and range of each NFT,
its liquidity, the pool's price, and unclaimed fees (StateView fee growth).

Deposit is recovered from liquidity the way RH Portfolio does it: a ladder slice is minted
single-sided on the quote side, and the quote amount that liquidity needed over its range
does not depend on the price at mint. It is in today's quote price (ETH ladders: ETH now).
Claimed fees are not tracked: PnL = value + unclaimed fees - deposit.

The range strip of a ladder is its own pool's range against that pool's price (one pool per
row, so the RH Portfolio v119 bug of mixing pools cannot happen here).
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

from . import chain

Q128 = 1 << 128
_lock = threading.Lock()


def _sqrt_at(t: int) -> float:
    return 1.0001 ** (t / 2)


def amounts(liq: int, lo: int, hi: int, tick: int, sqrt_x96: int) -> tuple[float, float]:
    """Raw token amounts of a position (currency0, currency1)."""
    L = float(liq)
    sa, sb, sp = _sqrt_at(lo), _sqrt_at(hi), sqrt_x96 / 2 ** 96
    if tick < lo:
        return L * (1 / sa - 1 / sb), 0.0
    if tick < hi:
        return L * (1 / sp - 1 / sb), L * (sp - sa)
    return 0.0, L * (sb - sa)


def single_sided_quote(liq: int, lo: int, hi: int, quote_side: int) -> float:
    """Raw quote amount the position needed if it was minted entirely in the quote (a ladder slice)."""
    L = float(liq)
    sa, sb = _sqrt_at(lo), _sqrt_at(hi)
    return L * (1 / sa - 1 / sb) if quote_side == 0 else L * (sb - sa)


# ------------------------------------------------------------ discovery ----
def _ids_file(data: Path, wallet: str) -> Path:
    return data / f"ids_{wallet.lower()}.json"


def position_ids(data: Path, wallet: str) -> tuple[list[int], dict]:
    """Position NFTs the wallet holds now, and {id: mint block} for those minted to it."""
    w = wallet.lower()
    f = _ids_file(data, w)
    try:
        st = json.loads(f.read_text())
    except (OSError, ValueError):
        st = {"scannedTo": 0, "ids": {}}
    head = chain.block_number()
    topic_to = "0x" + chain.addr_word(w)
    start = st["scannedTo"] + 1
    if start <= head:
        logs = chain.get_logs(chain.POSITION_MANAGER, [chain.TOPIC["Transfer"], None, topic_to], start, head,
                              chunk=10_000_000, pause=0.1)
        for l in logs:
            tid = int(l["topics"][3], 16)
            minted = int(l["topics"][1], 16) == 0
            cur = st["ids"].get(str(tid))
            st["ids"][str(tid)] = int(l["blockNumber"], 16) if minted else (cur if cur is not None else None)
        st["scannedTo"] = head
    ids = sorted(int(i) for i in st["ids"])
    owners = chain.multicall([(chain.POSITION_MANAGER, chain.SEL["ownerOf"] + chain.u256(i)) for i in ids]) if ids else []
    held = [i for i, o in zip(ids, owners) if o and chain.word_addr(chain.words(o)[0]) == w]
    data.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st))
    return held, {i: st["ids"].get(str(i)) for i in held}


def _block_times(data: Path, blocks: list[int]) -> dict[int, int]:
    f = data / "block_times.json"
    try:
        cache = {int(k): v for k, v in json.loads(f.read_text()).items()}
    except (OSError, ValueError):
        cache = {}
    for b in [b for b in set(blocks) if b and b not in cache][:40]:
        try:
            cache[b] = chain.block_time(b)
        except Exception:
            pass
    f.write_text(json.dumps(cache))
    return cache


# ------------------------------------------------------------ positions ----
def read_positions(ids: list[int]) -> list[dict]:
    calls = []
    for i in ids:
        calls += [(chain.POSITION_MANAGER, chain.SEL["getPoolAndPositionInfo"] + chain.u256(i)),
                  (chain.POSITION_MANAGER, chain.SEL["getPositionLiquidity"] + chain.u256(i))]
    res = chain.multicall(calls) if calls else []
    out = []
    for k, i in enumerate(ids):
        info, liq = res[2 * k], res[2 * k + 1]
        if not info or not liq:
            continue
        w = chain.words(info)
        key = chain.decode_pool_key(info)
        L = chain.to_int(chain.words(liq)[0])
        if not key or L == 0:
            continue
        packed = chain.to_int(w[5])
        lo = chain.to_int(format((packed >> 8) & 0xFFFFFF, "x"), 24, True)
        hi = chain.to_int(format((packed >> 32) & 0xFFFFFF, "x"), 24, True)
        pid = chain.pool_id(key["currency0"], key["currency1"], key["fee"], key["tickSpacing"], key["hooks"])
        out.append({"tokenId": i, "key": key, "pool": pid, "tickLower": lo, "tickUpper": hi, "liquidity": L})
    return out


def unclaimed(positions: list[dict]) -> dict[int, tuple[int, int]]:
    """Raw fees owed per position: liquidity x (fee growth inside now - at last touch) / 2^128."""
    calls = []
    for p in positions:
        tl, tu = chain.u256(p["tickLower"]), chain.u256(p["tickUpper"])
        calls.append((chain.STATE_VIEW, chain.SEL["getFeeGrowthInside"] + p["pool"][2:] + tl + tu))
        calls.append((chain.STATE_VIEW, chain.SEL["getPositionInfo"] + p["pool"][2:] + chain.addr_word(chain.POSITION_MANAGER)
                      + tl + tu + chain.u256(p["tokenId"])))
    res = chain.multicall(calls) if calls else []
    out = {}
    for k, p in enumerate(positions):
        g, info = res[2 * k], res[2 * k + 1]
        if not g or not info:
            continue
        gw, iw = chain.words(g), chain.words(info)
        L = chain.to_int(iw[0])
        f0 = L * ((chain.to_int(gw[0]) - chain.to_int(iw[1])) % (1 << 256)) // Q128
        f1 = L * ((chain.to_int(gw[1]) - chain.to_int(iw[2])) % (1 << 256)) // Q128
        out[p["tokenId"]] = (f0, f1)
    return out


def wallet_balances(wallet: str) -> dict:
    calls = [(chain.MULTICALL3, chain.SEL["getEthBalance"] + chain.addr_word(wallet)),
             (chain.USDG, chain.SEL["balanceOf"] + chain.addr_word(wallet)),
             (chain.WETH, chain.SEL["balanceOf"] + chain.addr_word(wallet))]
    r = chain.multicall(calls)
    v = lambda x, d: chain.to_int(chain.words(x)[0]) / 10 ** d if x else None
    return {"eth": v(r[0], 18), "usdg": v(r[1], 6), "weth": v(r[2], 18)}


# ------------------------------------------------------------ exits ----
def exit_events(lad: dict, cfg: dict) -> list[dict]:
    x, ev = cfg["exits"], []
    pnl = lad.get("pnlPct")
    if x.get("alertAtPnlPct") is not None and pnl is not None and pnl <= x["alertAtPnlPct"]:
        ev.append({"kind": "alert", "rule": "pnl", "msg": f"PnL {pnl:+.1f}% ≤ {x['alertAtPnlPct']}%"})
    rp = lad.get("rangePct")
    if x.get("alertBottomThirdOfRange") and rp is not None and 0 <= rp < 100 / 3 and lad["inRangeCount"]:
        ev.append({"kind": "alert", "rule": "bottomThird", "msg": f"harga di 1/3 bawah range ({rp:.0f}%)"})
    if x.get("alertFullyConverted") and rp is not None and rp < 0:
        ev.append({"kind": "alert", "rule": "converted", "msg": "harga lewat bawah semua slice: sekarang pegang token"})
    if x.get("takeProfitPct") is not None and pnl is not None and pnl >= x["takeProfitPct"]:
        ev.append({"kind": "close", "rule": "takeProfit", "msg": f"take profit: PnL {pnl:+.1f}% ≥ +{x['takeProfitPct']}%"})
    if x.get("stopLossPct") is not None and pnl is not None and pnl <= x["stopLossPct"]:
        ev.append({"kind": "close", "rule": "stopLoss", "msg": f"stop loss: PnL {pnl:+.1f}% ≤ {x['stopLossPct']}%"})
    return ev


# ------------------------------------------------------------ build ----
def portfolio(wallet: str, cfg: dict, data: Path, book) -> dict:
    now = time.time()
    with _lock:
        ids, mint_blocks = position_ids(data, wallet)
    pos = read_positions(ids)
    pools = sorted({p["pool"] for p in pos})
    s0 = chain.slot0s(pools) if pools else {}
    fees = unclaimed(pos)
    toks = sorted({p["key"][c] for p in pos for c in ("currency0", "currency1")})
    meta = book.tokens(toks) if toks else {}
    eth = book.ix.eth_usd()
    times = _block_times(data, [b for b in mint_blocks.values() if b])
    book.add_pools(pools)
    ladders: dict[str, dict] = {}
    for p in pos:
        k, sl = p["key"], s0.get(p["pool"])
        if not sl:
            continue
        q = chain.quote_side(k)
        if q is None:                         # pair with no USDG/ETH side: show it, value unknown
            q = 0
        qaddr = k["currency0" if q == 0 else "currency1"]
        taddr = k["currency1" if q == 0 else "currency0"]
        qusd = 1.0 if qaddr == chain.USDG else eth if qaddr in (chain.ZERO, chain.WETH) else None
        d0 = chain.DECIMALS.get(k["currency0"]) or (meta.get(k["currency0"]) or {}).get("decimals", 18)
        d1 = chain.DECIMALS.get(k["currency1"]) or (meta.get(k["currency1"]) or {}).get("decimals", 18)
        dq, dt = (d0, d1) if q == 0 else (d1, d0)
        p01 = chain.price_of(sl["sqrtPriceX96"], d0, d1)       # currency0 in currency1
        tok_q = p01 if q == 1 else (1 / p01 if p01 else None)   # token price in quote
        a0, a1 = amounts(p["liquidity"], p["tickLower"], p["tickUpper"], sl["tick"], sl["sqrtPriceX96"])
        aq, at = (a0, a1) if q == 0 else (a1, a0)
        f0, f1 = fees.get(p["tokenId"], (0, 0))
        fq, ft = (f0, f1) if q == 0 else (f1, f0)
        to_usd = lambda rq, rt: ((rq / 10 ** dq) + (rt / 10 ** dt) * (tok_q or 0)) * qusd if qusd else None
        dep = single_sided_quote(p["liquidity"], p["tickLower"], p["tickUpper"], q) / 10 ** dq * qusd if qusd else None
        in_range = p["tickLower"] <= sl["tick"] < p["tickUpper"]
        g = ladders.setdefault(p["pool"], {
            "pool": p["pool"], "key": k, "token": taddr, "quote": qaddr, "quoteSymbol": chain.QUOTE_SYMBOL.get(qaddr, "?"),
            "symbol": (meta.get(taddr) or {}).get("symbol") or taddr[2:8], "quoteSide": q, "flip": q == 0,
            "tick": sl["tick"], "lpFeePct": sl["lpFee"] / 1e4, "positions": [], "valueUsd": 0.0, "feesUsd": 0.0,
            "depositUsd": 0.0, "inRangeCount": 0, "tokenUsd": tok_q * qusd if tok_q and qusd else None,
            "amountQuote": 0.0, "amountToken": 0.0, "feesQuote": 0.0, "feesToken": 0.0, "mintedAt": None,
            "supply": (meta.get(taddr) or {}).get("supply"), "decimals": dt,
        })
        mt = times.get(mint_blocks.get(p["tokenId"]) or 0)
        g["positions"].append({"tokenId": p["tokenId"], "tickLower": p["tickLower"], "tickUpper": p["tickUpper"],
                               "liquidity": str(p["liquidity"]), "inRange": in_range, "mintedAt": mt,
                               "valueUsd": to_usd(aq, at), "feesUsd": to_usd(fq, ft), "depositUsd": dep})
        for f, v in (("valueUsd", to_usd(aq, at)), ("feesUsd", to_usd(fq, ft)), ("depositUsd", dep)):
            if v is not None and g[f] is not None:
                g[f] += v
            else:
                g[f] = None
        g["amountQuote"] += aq / 10 ** dq
        g["amountToken"] += at / 10 ** dt
        g["feesQuote"] += fq / 10 ** dq
        g["feesToken"] += ft / 10 ** dt
        g["inRangeCount"] += in_range
        if mt and (not g["mintedAt"] or mt < g["mintedAt"]):
            g["mintedAt"] = mt
    rows = []
    for g in ladders.values():
        lo = min(x["tickLower"] for x in g["positions"])
        hi = max(x["tickUpper"] for x in g["positions"])
        f = (g["tick"] - lo) / (hi - lo) if hi > lo else None
        if f is not None and g["flip"]:
            f = 1 - f
        g.update(tickLower=lo, tickUpper=hi, posCount=len(g["positions"]),
                 rangePct=f * 100 if f is not None else None,
                 pnlUsd=(g["valueUsd"] + g["feesUsd"] - g["depositUsd"]) if None not in (g["valueUsd"], g["feesUsd"], g["depositUsd"]) else None)
        g["pnlPct"] = g["pnlUsd"] / g["depositUsd"] * 100 if g["pnlUsd"] is not None and g["depositUsd"] else None
        g["heldHours"] = (now - g["mintedAt"]) / 3600 if g["mintedAt"] else None
        # Market cap at each end of the range: supply x the price at that tick (same pool as the tick).
        if g["supply"] and g["tokenUsd"]:
            cap = g["supply"] / 10 ** g["decimals"] * g["tokenUsd"]
            at = lambda t: cap * 1.0001 ** ((-(t - g["tick"])) if g["flip"] else (t - g["tick"]))
            g["capLo"], g["capHi"] = sorted((at(lo), at(hi)))
            g["mcapUsd"] = cap
        else:
            g["capLo"] = g["capHi"] = g["mcapUsd"] = None
        # where the price sits: above the ladder (all quote), in it, or below (all token)
        g["state"] = ("in" if g["inRangeCount"] else "above" if f is not None and f > 1
                      else "below" if f is not None and f < 0 else "gap")
        g["events"] = exit_events(g, cfg)
        g["feePctDay"] = (g["feesUsd"] / g["valueUsd"] * 100 / (g["heldHours"] / 24)
                          if g["feesUsd"] and g["valueUsd"] and g["heldHours"] and g["heldHours"] > 0.25 else None)
        rows.append(g)
    rows.sort(key=lambda r: -abs(r["pnlUsd"] or 0))
    tot = lambda k: sum(r[k] for r in rows if r[k] is not None)
    bal = wallet_balances(wallet)
    return {"wallet": wallet.lower(), "t": now, "rows": rows, "ethUsd": eth, "valueUsd": tot("valueUsd"),
            "feesUsd": tot("feesUsd"), "depositUsd": tot("depositUsd"), "pnlUsd": tot("pnlUsd"),
            "balances": bal, "exitsCfg": cfg["exits"],
            "walletUsd": (bal["usdg"] or 0) + ((bal["eth"] or 0) + (bal["weth"] or 0)) * (eth or 0)}
