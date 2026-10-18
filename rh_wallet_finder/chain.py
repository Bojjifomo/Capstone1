"""Uniswap V4 decoding, liquidity math and Multicall3 encoding.

Everything here is pure (no network), so it is what the tests cover.
"""
import math

from keccak import topic

MODIFY_LIQUIDITY = topic("ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)")
TRANSFER = topic("Transfer(address,address,uint256)")

SEL_GET_SLOT0 = "c815641c"           # StateView.getSlot0(bytes32)
SEL_POSITION_INFO = "dacf1d2f"       # StateView.getPositionInfo(bytes32,address,int24,int24,bytes32)
SEL_FEE_GROWTH_INSIDE = "53e9c1fb"   # StateView.getFeeGrowthInside(bytes32,int24,int24)
SEL_POOL_KEYS = "86b6be7d"           # PositionManager.poolKeys(bytes25)
SEL_AGGREGATE3 = "82ad56cb"          # Multicall3.aggregate3((address,bool,bytes)[])
SEL_ETH_BALANCE = "4d2301cc"         # Multicall3.getEthBalance(address)
SEL_BALANCE_OF = "70a08231"
SEL_SYMBOL = "95d89b41"
SEL_DECIMALS = "313ce567"
SEL_TOTAL_SUPPLY = "18160ddd"

Q128 = 1 << 128
Q96 = 1 << 96
U256 = 1 << 256
FULL_RANGE_TICK = 887000


# ---- words -----------------------------------------------------------------
def word(v):
    """int (signed ok) -> 64 hex chars"""
    return format(v % U256, "064x")


def addr_word(a):
    return "0" * 24 + a.lower().removeprefix("0x")


def signed(h):
    v = int(h, 16)
    return v - U256 if v >= 1 << 255 else v


def words(data):
    d = data[2:] if data.startswith("0x") else data
    return [d[i:i + 64] for i in range(0, len(d), 64)]


# ---- events ----------------------------------------------------------------
def decode_modify(log):
    t = log["topics"]
    w = words(log["data"])
    return {
        "block": int(log["blockNumber"], 16),
        "log_index": int(log["logIndex"], 16),
        "tx": log["transactionHash"].lower(),
        "ts": int(log["blockTimestamp"], 16) if log.get("blockTimestamp") else None,
        "pool": t[1].lower(),
        "sender": "0x" + t[2][-40:].lower(),
        "tl": signed(w[0]),
        "tu": signed(w[1]),
        "delta": signed(w[2]),
        "salt": int(w[3], 16),
    }


def decode_nft_transfer(log):
    t = log["topics"]
    return {
        "block": int(log["blockNumber"], 16),
        "log_index": int(log["logIndex"], 16),
        "tx": log["transactionHash"].lower(),
        "ts": int(log["blockTimestamp"], 16) if log.get("blockTimestamp") else None,
        "from": "0x" + t[1][-40:].lower(),
        "to": "0x" + t[2][-40:].lower(),
        "token_id": int(t[3], 16),
    }


# ---- liquidity math --------------------------------------------------------
def sqrt_at_tick(tick):
    return math.pow(1.0001, tick / 2)


def amounts_for_liquidity(liq, sqrt_price_x96, tl, tu):
    """Raw token amounts held by `liq` liquidity in [tl, tu] at a pool price."""
    if liq == 0 or sqrt_price_x96 == 0:
        return 0.0, 0.0
    sp = sqrt_price_x96 / Q96
    sa, sb = sqrt_at_tick(tl), sqrt_at_tick(tu)
    liq = float(abs(liq))
    if sp <= sa:
        return liq * (sb - sa) / (sa * sb), 0.0
    if sp >= sb:
        return 0.0, liq * (sb - sa)
    return liq * (sb - sp) / (sp * sb), liq * (sp - sa)


def fees_owed(liq, fg_now, fg_last):
    """Fees a position earned since it last checkpointed, raw token units."""
    return liq * ((fg_now - fg_last) % U256) // Q128


def range_width_pct(tl, tu):
    """How wide the range is: upper price / lower price - 1, in %."""
    return (math.pow(1.0001, tu - tl) - 1) * 100


def price1_per0(sqrt_price_x96, dec0, dec1):
    """Human price of token0 in token1."""
    p = (sqrt_price_x96 / Q96) ** 2
    return p * 10 ** (dec0 - dec1)


# ---- calldata --------------------------------------------------------------
def cd_slot0(pool):
    return "0x" + SEL_GET_SLOT0 + pool[2:]


def cd_position_info(pool, owner, tl, tu, salt):
    return "0x" + SEL_POSITION_INFO + pool[2:] + addr_word(owner) + word(tl) + word(tu) + word(salt)


def cd_fee_growth_inside(pool, tl, tu):
    return "0x" + SEL_FEE_GROWTH_INSIDE + pool[2:] + word(tl) + word(tu)


def cd_pool_keys(pool):
    return "0x" + SEL_POOL_KEYS + pool[2:52] + "0" * 14


def cd_simple(sel, arg_addr=None):
    return "0x" + sel + (addr_word(arg_addr) if arg_addr else "")


def decode_slot0(ret):
    w = words(ret)
    if len(w) < 4:
        return None
    return {"sqrtp": int(w[0], 16), "tick": signed(w[1]), "lp_fee": int(w[3], 16)}


def decode_position_info(ret):
    w = words(ret)
    return {"liq": int(w[0], 16), "fg0": int(w[1], 16), "fg1": int(w[2], 16)}


def decode_fee_growth(ret):
    w = words(ret)
    return int(w[0], 16), int(w[1], 16)


def decode_pool_key(ret):
    w = words(ret)
    if len(w) < 5:
        return None
    return {"c0": "0x" + w[0][-40:], "c1": "0x" + w[1][-40:], "fee": int(w[2], 16),
            "spacing": signed(w[3]), "hooks": "0x" + w[4][-40:]}


def decode_string(ret):
    """ABI string, or a bytes32 symbol (old tokens)."""
    try:
        d = bytes.fromhex(ret[2:])
        if len(d) == 32:
            return d.rstrip(b"\0").decode("utf-8", "replace")
        off = int.from_bytes(d[0:32], "big")
        n = int.from_bytes(d[off:off + 32], "big")
        return d[off + 32:off + 32 + n].decode("utf-8", "replace")
    except (ValueError, IndexError):
        return ""


def encode_aggregate3(calls):
    """calls: [(target, calldata_hex), ...], every call allowed to fail."""
    n = len(calls)
    heads, tails = [], []
    offset = 32 * n
    for target, data in calls:
        b = data[2:] if data.startswith("0x") else data
        blen = len(b) // 2
        padded = b + "0" * (-len(b) % 64)
        elem = addr_word(target) + word(1) + word(0x60) + word(blen) + padded
        heads.append(word(offset))
        tails.append(elem)
        offset += len(elem) // 2
    return "0x" + SEL_AGGREGATE3 + word(0x20) + word(n) + "".join(heads) + "".join(tails)


def decode_aggregate3(ret):
    """-> [returnData hex or None if that call failed]"""
    d = ret[2:]

    def at(byte_off):
        return int(d[byte_off * 2:byte_off * 2 + 64], 16)

    arr = at(0)
    n = at(arr)
    base = arr + 32
    out = []
    for i in range(n):
        el = base + at(base + 32 * i)
        ok = at(el)
        boff = el + at(el + 32)
        blen = at(boff)
        data = d[(boff + 32) * 2:(boff + 32 + blen) * 2]
        out.append("0x" + data if ok and blen else None)
    return out


def short(a):
    return a[:6] + "…" + a[-4:]
