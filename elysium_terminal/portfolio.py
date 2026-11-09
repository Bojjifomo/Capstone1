"""A wallet's v3 and v4 LP positions on Elysium, one row per ladder (pool), from chain.

v4: position NFTs from the PositionManager's Transfer logs (cached), ownerOf to confirm.
    Unclaimed fees from StateView fee growth. Same idea as RH Terminal.
v3: ERC-721 enumeration (balanceOf + tokenOfOwnerByIndex) on ElysiumSwap's NPM and on
    the official Uniswap NPM. Unclaimed fees are a static collect() (eth_call from the
    owner) — nothing is sent. No explorer.

Deposit is the quote a single-sided ladder slice needed for its liquidity. It does not
depend on the price at mint. Claimed fees are not in PnL: PnL = value + unclaimed - deposit.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import chain

Q128 = 1 << 128
MAX128 = (1 << 128) - 1
_lock = threading.Lock()


def _sqrt_at(t: int) -> float:
    return 1.0001 ** (t / 2)


def amounts(liq: int, lo: int, hi: int, tick: int, sqrt_x96: int) -> tuple[float, float]:
    """Raw token amounts (currency0, currency1)."""
    L = float(liq)
    sa, sb, sp = _sqrt_at(lo), _sqrt_at(hi), sqrt_x96 / 2 ** 96
    if tick < lo:
        return L * (1 / sa - 1 / sb), 0.0
    if tick < hi:
        return L * (1 / sp - 1 / sb), L * (sp - sa)
    return 0.0, L * (sb - sa)


def single_sided_quote(liq: int, lo: int, hi: int, quote_side: int) -> float:
    L = float(liq)
    sa, sb = _sqrt_at(lo), _sqrt_at(hi)
    return L * (1 / sa - 1 / sb) if quote_side == 0 else L * (sb - sa)


# A 60% ladder is ~9k ticks. Full-range v3 positions span ~1.7M ticks and were not
# minted quote-only, so the ladder deposit formula explodes and the PnL row is garbage.
LADDER_MAX_TICKS = 200_000


def ladder_deposit_usd(liq: int, lo: int, hi: int, quote_side: int, decimals: int, qusd: float | None) -> float | None:
    if qusd is None or hi - lo > LADDER_MAX_TICKS:
        return None
    return single_sided_quote(liq, lo, hi, quote_side) / 10 ** decimals * qusd


def _ids_file(data: Path, wallet: str) -> Path:
    return data / f"ids_{wallet.lower()}.json"


def v4_position_ids(data: Path, wallet: str) -> tuple[list[int], dict]:
    w = wallet.lower()
    f = _ids_file(data, w)
    try:
        st = json.loads(f.read_text())
    except (OSError, ValueError):
        st = {"scannedTo": 0, "ids": {}}
    head = chain.block_number()
    topic_to = "0x" + chain.addr_word(w)
    start = int(st.get("scannedTo") or 0) + 1
    if start <= head:
        logs = chain.get_logs(chain.V4_POSITION_MANAGER, [chain.TOPIC["Transfer"], None, topic_to], start, head,
                               chunk=200_000, pause=0.02)
        for lg in logs:
            if len(lg.get("topics") or []) < 4:
                continue
            tid = int(lg["topics"][3], 16)
            minted = int(lg["topics"][1], 16) == 0
            cur = st["ids"].get(str(tid))
            st["ids"][str(tid)] = int(lg["blockNumber"], 16) if minted else (cur if cur is not None else None)
        st["scannedTo"] = head
    ids = sorted(int(i) for i in st["ids"])
    owners = chain.multicall([(chain.V4_POSITION_MANAGER, chain.SEL["ownerOf"] + chain.u256(i)) for i in ids]) if ids else []
    held = [i for i, o in zip(ids, owners) if o and chain.word_addr(chain.words(o)[0]) == w]
    data.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st))
    return held, {i: st["ids"].get(str(i)) for i in held}


def read_v4(ids: list[int]) -> list[dict]:
    calls = []
    for i in ids:
        calls += [(chain.V4_POSITION_MANAGER, chain.SEL["getPoolAndPositionInfo"] + chain.u256(i)),
                  (chain.V4_POSITION_MANAGER, chain.SEL["getPositionLiquidity"] + chain.u256(i))]
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
        out.append({"tokenId": i, "key": key, "pool": pid, "tickLower": lo, "tickUpper": hi, "liquidity": L,
                    "dex": "v4", "venue": "uniswap-v4", "manager": chain.V4_POSITION_MANAGER})
    return out


def unclaimed_v4(positions: list[dict]) -> dict[int, tuple[int, int]]:
    calls = []
    for p in positions:
        tl, tu = chain.u256(p["tickLower"]), chain.u256(p["tickUpper"])
        calls.append((chain.V4_STATE_VIEW, chain.SEL["getFeeGrowthInside"] + p["pool"][2:] + tl + tu))
        calls.append((chain.V4_STATE_VIEW, chain.SEL["getPositionInfo"] + p["pool"][2:] + chain.addr_word(chain.V4_POSITION_MANAGER)
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
        out[("v4", p["tokenId"])] = (f0, f1)
    return out


def v3_token_ids(npm: str, wallet: str) -> list[int]:
    bal = chain.eth_call(npm, chain.SEL["balanceOf"] + chain.addr_word(wallet))
    n = chain.to_int(chain.words(bal)[0]) if bal and bal != "0x" else 0
    if n <= 0:
        return []
    if n > 200:
        n = 200
    calls = [(npm, chain.SEL["tokenOfOwnerByIndex"] + chain.addr_word(wallet) + chain.u256(i)) for i in range(n)]
    out = []
    for r in chain.multicall(calls):
        if r:
            out.append(chain.to_int(chain.words(r)[0]))
    return out


def read_v3(npm: str, venue: str, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    res = chain.multicall([(npm, chain.SEL["positions"] + chain.u256(i)) for i in ids])
    out = []
    for i, ret in zip(ids, res):
        if not ret:
            continue
        w = chain.words(ret)
        if len(w) < 12:
            continue
        key = {"dex": "v3", "venue": venue, "currency0": chain.word_addr(w[2]), "currency1": chain.word_addr(w[3]),
               "fee": chain.to_int(w[4], 24), "tickSpacing": 0, "hooks": chain.ZERO, "npm": npm, "router": chain.V3_VENUES[venue]["router"]}
        lo = chain.to_int(w[5], 24, True)
        hi = chain.to_int(w[6], 24, True)
        L = chain.to_int(w[7])
        if L == 0 and chain.to_int(w[10]) == 0 and chain.to_int(w[11]) == 0:
            continue
        pool = chain.v3_pool_address(chain.V3_VENUES[venue]["factory"], key["currency0"], key["currency1"], key["fee"])
        key["pool"] = pool
        out.append({"tokenId": i, "key": key, "pool": pool, "tickLower": lo, "tickUpper": hi, "liquidity": L,
                    "dex": "v3", "venue": venue, "manager": npm})
    return out


def unclaimed_v3(wallet: str, positions: list[dict]) -> dict[tuple, tuple[int, int]]:
    """Static collect() from the owner. The call is not a transaction."""
    out = {}
    for p in positions:
        data = "0x" + chain.SEL["collect"] + chain.u256(p["tokenId"]) + chain.addr_word(wallet) + chain.u256(MAX128) + chain.u256(MAX128)
        try:
            ret = chain.eth_call(p["manager"], data, sender=wallet)
        except Exception:
            continue
        w = chain.words(ret)
        if len(w) >= 2:
            out[("v3", p["manager"], p["tokenId"])] = (chain.to_int(w[0]), chain.to_int(w[1]))
    return out


def wallet_balances(wallet: str) -> dict:
    calls = [(chain.MULTICALL3, chain.SEL["getEthBalance"] + chain.addr_word(wallet)),
             (chain.USDC, chain.SEL["balanceOf"] + chain.addr_word(wallet)),
             (chain.WHYPE, chain.SEL["balanceOf"] + chain.addr_word(wallet))]
    r = chain.multicall(calls)
    v = lambda x, d: chain.to_int(chain.words(x)[0]) / 10 ** d if x else None
    return {"hype": v(r[0], 18), "usdc": v(r[1], 6), "whype": v(r[2], 18)}


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


def portfolio(wallet: str, cfg: dict, data: Path, book) -> dict:
    now = time.time()
    with _lock:
        v4_ids, mint_blocks = v4_position_ids(data, wallet)
    pos = read_v4(v4_ids)
    for venue, spec in chain.V3_VENUES.items():
        try:
            ids = v3_token_ids(spec["npm"], wallet)
            pos += read_v3(spec["npm"], venue, ids)
        except Exception:
            pass
    v4s = [p for p in pos if p["dex"] == "v4"]
    v3s = [p for p in pos if p["dex"] == "v3"]
    fees = unclaimed_v4(v4s)
    fees.update(unclaimed_v3(wallet, v3s))
    pools = sorted({p["pool"] for p in pos})
    s0 = {}
    if v4s:
        s0.update(chain.slot0s_v4(sorted({p["pool"] for p in v4s})))
    if v3s:
        s0.update(chain.slot0s_v3(sorted({p["pool"] for p in v3s})))
    toks = sorted({p["key"][c] for p in pos for c in ("currency0", "currency1")})
    meta = book.tokens(toks) if toks else {}
    hype = book.ix.hype_usd()
    book.add_pools(pools)
    ladders: dict[str, dict] = {}
    for p in pos:
        k, sl = p["key"], s0.get(p["pool"])
        if not sl:
            continue
        q = chain.quote_side(k)
        if q is None:
            q = 0
        qaddr = k["currency0" if q == 0 else "currency1"]
        taddr = k["currency1" if q == 0 else "currency0"]
        qusd = 1.0 if qaddr == chain.USDC else hype if qaddr in (chain.ZERO, chain.WHYPE) else None
        d0 = chain.DECIMALS.get(k["currency0"]) or (meta.get(k["currency0"]) or {}).get("decimals", 18)
        d1 = chain.DECIMALS.get(k["currency1"]) or (meta.get(k["currency1"]) or {}).get("decimals", 18)
        dq, dt = (d0, d1) if q == 0 else (d1, d0)
        p01 = chain.price_of(sl["sqrtPriceX96"], d0, d1)
        tok_q = p01 if q == 1 else (1 / p01 if p01 else None)
        a0, a1 = amounts(p["liquidity"], p["tickLower"], p["tickUpper"], sl["tick"], sl["sqrtPriceX96"])
        aq, at = (a0, a1) if q == 0 else (a1, a0)
        fkey = ("v4", p["tokenId"]) if p["dex"] == "v4" else ("v3", p["manager"], p["tokenId"])
        f0, f1 = fees.get(fkey, (0, 0))
        fq, ft = (f0, f1) if q == 0 else (f1, f0)
        to_usd = lambda rq, rt: ((rq / 10 ** dq) + (rt / 10 ** dt) * (tok_q or 0)) * qusd if qusd else None
        dep = ladder_deposit_usd(p["liquidity"], p["tickLower"], p["tickUpper"], q, dq, qusd)
        in_range = p["tickLower"] <= sl["tick"] < p["tickUpper"]
        g = ladders.setdefault(p["pool"], {
            "pool": p["pool"], "dex": p["dex"], "venue": p["venue"], "manager": p["manager"], "key": k,
            "token": taddr, "quote": qaddr, "quoteSymbol": chain.QUOTE_SYMBOL.get(qaddr, "?"),
            "symbol": (meta.get(taddr) or {}).get("symbol") or taddr[2:8], "quoteSide": q, "flip": q == 0,
            "tick": sl["tick"], "lpFeePct": (sl.get("lpFee") or k.get("fee") or 0) / 1e4, "positions": [],
            "valueUsd": 0.0, "feesUsd": 0.0, "depositUsd": 0.0, "inRangeCount": 0,
            "tokenUsd": tok_q * qusd if tok_q and qusd else None,
            "amountQuote": 0.0, "amountToken": 0.0, "feesQuote": 0.0, "feesToken": 0.0, "mintedAt": None,
            "supply": (meta.get(taddr) or {}).get("supply"), "decimals": dt,
        })
        mt = mint_blocks.get(p["tokenId"]) if p["dex"] == "v4" else None
        g["positions"].append({"tokenId": p["tokenId"], "tickLower": p["tickLower"], "tickUpper": p["tickUpper"],
                               "liquidity": str(p["liquidity"]), "inRange": in_range, "mintedAt": mt,
                               "valueUsd": to_usd(aq, at), "feesUsd": to_usd(fq, ft), "depositUsd": dep,
                               "dex": p["dex"], "manager": p["manager"]})
        for field, val in (("valueUsd", to_usd(aq, at)), ("feesUsd", to_usd(fq, ft)), ("depositUsd", dep)):
            if val is not None and g[field] is not None:
                g[field] += val
            else:
                g[field] = None
        g["amountQuote"] += aq / 10 ** dq
        g["amountToken"] += at / 10 ** dt
        g["feesQuote"] += fq / 10 ** dq
        g["feesToken"] += ft / 10 ** dt
        g["inRangeCount"] += int(in_range)
    rows = []
    for g in ladders.values():
        lo = min(x["tickLower"] for x in g["positions"])
        hi = max(x["tickUpper"] for x in g["positions"])
        frac = (g["tick"] - lo) / (hi - lo) if hi > lo else None
        if frac is not None and g["flip"]:
            frac = 1 - frac
        g.update(tickLower=lo, tickUpper=hi, posCount=len(g["positions"]),
                 rangePct=frac * 100 if frac is not None else None,
                 pnlUsd=(g["valueUsd"] + g["feesUsd"] - g["depositUsd"]) if None not in (g["valueUsd"], g["feesUsd"], g["depositUsd"]) else None)
        g["pnlPct"] = g["pnlUsd"] / g["depositUsd"] * 100 if g["pnlUsd"] is not None and g["depositUsd"] else None
        g["heldHours"] = None
        if g["supply"] and g["tokenUsd"]:
            cap = g["supply"] / 10 ** g["decimals"] * g["tokenUsd"]
            g["mcapUsd"] = cap
            if hi - lo > LADDER_MAX_TICKS:
                g["capLo"] = g["capHi"] = None
            else:
                atp = lambda t, cap=cap: cap * 1.0001 ** ((-(t - g["tick"])) if g["flip"] else (t - g["tick"]))
                g["capLo"], g["capHi"] = sorted((atp(lo), atp(hi)))
        else:
            g["capLo"] = g["capHi"] = g["mcapUsd"] = None
        g["state"] = ("in" if g["inRangeCount"] else "above" if frac is not None and frac > 1
                      else "below" if frac is not None and frac < 0 else "gap")
        g["events"] = exit_events(g, cfg)
        rows.append(g)
    rows.sort(key=lambda r: -abs(r["pnlUsd"] or 0))
    def tot(k):
        vals = [r[k] for r in rows if r[k] is not None]
        return sum(vals) if vals else None
    bal = wallet_balances(wallet)
    return {"wallet": wallet.lower(), "t": now, "rows": rows, "ethUsd": hype, "hypeUsd": hype,
            "valueUsd": tot("valueUsd"), "feesUsd": tot("feesUsd"), "depositUsd": tot("depositUsd"),
            "pnlUsd": tot("pnlUsd"), "balances": bal, "exitsCfg": cfg["exits"],
            "walletUsd": (bal["usdc"] or 0) + ((bal["hype"] or 0) + (bal["whype"] or 0)) * (hype or 0)}
