"""Liquidity depth of one pool around a range: how much liquidity sits at each price.

Read straight from the pool: the tick bitmap says which ticks are initialized, each of
those has a liquidityNet, and walking them outward from the current tick (whose active
liquidity the pool reports) gives the active liquidity of every stretch in between.
Two multicalls for a normal pool. Nothing is estimated.
"""
from __future__ import annotations

from . import chain

MAX_WORDS = 48          # bitmap words read per request (each covers 256 tick spacings)
MAX_TICKS = 480         # initialized ticks read per request, nearest the price first

SEL = {
    "v4Bitmap": chain.selector("getTickBitmap(bytes32,int16)"),
    "v4Tick": chain.selector("getTickLiquidity(bytes32,int24)"),
    "v3Bitmap": chain.selector("tickBitmap(int16)"),
    "v3Tick": chain.selector("ticks(int24)"),
}


def window(tick: int, lo: int, hi: int, spacing: int) -> tuple[int, int]:
    """Ticks to show: the range and the price, with room on both sides."""
    a, b = min(lo, tick), max(hi, tick)
    pad = max((hi - lo) * 2, (b - a), spacing * 20)
    return a - pad, b + pad


def segments(tick: int, active: int, nets: dict[int, int], lo: int, hi: int) -> list[dict]:
    """Active liquidity of each stretch between initialized ticks, inside [lo, hi).

    `nets` is {initialized tick: liquidityNet}. Crossing a tick upward adds its net,
    crossing it downward removes it.
    """
    ts = sorted(t for t in nets if lo < t < hi)
    up = [t for t in ts if t > tick]
    down = [t for t in ts if t <= tick][::-1]
    out = []
    liq, start = active, tick
    edges_up = up + [hi]
    # the stretch holding the price runs from the nearest tick below to the nearest above
    floor = down[0] if down else lo
    out.append({"a": floor, "b": edges_up[0], "L": max(0, liq)})
    for i, t in enumerate(up):
        liq += nets[t]
        out.append({"a": t, "b": edges_up[i + 1], "L": max(0, liq)})
    liq = active
    edges_down = down + [lo]
    for i, t in enumerate(down):
        liq -= nets[t]
        out.append({"a": edges_down[i + 1], "b": t, "L": max(0, liq)})
    out.sort(key=lambda s: s["a"])
    return [s for s in out if s["b"] > s["a"]]


def read(pool: str, dex: str, spacing: int, tick: int, lo: int, hi: int) -> dict:
    """Depth of `pool` around [lo, hi). `tick` is the pool's current tick."""
    spacing = abs(int(spacing))
    if spacing <= 0:
        raise ValueError("tick spacing pool tidak ketahuan")
    v4 = dex == "v4"
    w_lo, w_hi = window(tick, lo, hi, spacing)
    cur_word = (tick // spacing) >> 8
    first, last = (w_lo // spacing) >> 8, (w_hi // spacing) >> 8
    clipped = False
    if last - first + 1 > MAX_WORDS:                       # keep the words nearest the price
        half = MAX_WORDS // 2
        first, last = max(first, cur_word - half), min(last, cur_word + half - 1)
        clipped = True
    word_ids = list(range(first, last + 1))
    pid = pool[2:]
    if v4:
        calls = [(chain.V4_STATE_VIEW, chain.SEL["getLiquidity"] + pid)]
        calls += [(chain.V4_STATE_VIEW, SEL["v4Bitmap"] + pid + chain.u256(w)) for w in word_ids]
    else:
        calls = [(pool, chain.SEL["liquidity"])]
        calls += [(pool, SEL["v3Bitmap"] + chain.u256(w)) for w in word_ids]
    res = chain.multicall(calls, chunk=80)
    if not res or not res[0]:
        raise chain.RpcError("likuiditas pool gagal dibaca")
    active = chain.to_int(chain.words(res[0])[0])
    ticks = []
    for w, ret in zip(word_ids, res[1:]):
        if not ret:
            raise chain.RpcError("tick bitmap gagal dibaca")
        bits = chain.to_int(chain.words(ret)[0])
        while bits:
            low = bits & -bits
            ticks.append(((w << 8) + low.bit_length() - 1) * spacing)
            bits ^= low
    a = max(w_lo, (first << 8) * spacing)
    b = min(w_hi, ((last + 1) << 8) * spacing)
    ticks = [t for t in ticks if a < t < b]
    if len(ticks) > MAX_TICKS:
        ticks = sorted(ticks, key=lambda t: abs(t - tick))[:MAX_TICKS]
        a, b = max(a, min(ticks) - spacing), min(b, max(ticks) + spacing)
        clipped = True
    ticks.sort()
    if v4:
        calls = [(chain.V4_STATE_VIEW, SEL["v4Tick"] + pid + chain.u256(t)) for t in ticks]
    else:
        calls = [(pool, SEL["v3Tick"] + chain.u256(t)) for t in ticks]
    nets = {}
    for t, ret in zip(ticks, chain.multicall(calls, chunk=120) if calls else []):
        if not ret:
            raise chain.RpcError("likuiditas tick gagal dibaca")
        nets[t] = chain.to_int(chain.words(ret)[1], 128, True)
    return {"pool": pool, "tick": tick, "lo": a, "hi": b, "active": active, "clipped": clipped,
            "ticks": len(ticks), "segments": segments(tick, active, nets, a, b)}
