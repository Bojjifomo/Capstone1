"""Meteora DLMM (lb_clmm) account decoding, price math and event parsing.

Offsets come from the on-chain Anchor IDL of LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo
(lb_clmm 0.12.0). All accounts are bytemuck (zero-copy) so offsets are fixed.
"""

import base64
import math
from dataclasses import dataclass, field

import base58
from solders.pubkey import Pubkey

DLMM_PROGRAM = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
_DLMM_PK = Pubkey.from_string(DLMM_PROGRAM)

BASIS_POINT_MAX = 10_000
BINS_PER_ARRAY = 70
DEFAULT_BIN_PER_POSITION = 70
SCALE_OFFSET = 64

DISC_LB_PAIR = bytes([33, 11, 49, 98, 181, 101, 177, 13])
DISC_POSITION_V2 = bytes([117, 176, 212, 199, 245, 180, 133, 182])
DISC_BIN_ARRAY = bytes([92, 142, 92, 220, 5, 148, 70, 181])

# Anchor emit_cpi! events arrive as a self-invoke whose data starts with this tag.
EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")
EVENTS = {
    bytes([144, 142, 252, 84, 157, 53, 37, 121]): "PositionCreate",
    bytes([255, 196, 16, 107, 28, 202, 53, 128]): "PositionClose",
    bytes([31, 94, 125, 90, 227, 52, 61, 186]): "AddLiquidity",
    bytes([116, 244, 97, 232, 103, 31, 152, 58]): "RemoveLiquidity",
    bytes([0, 109, 117, 179, 61, 91, 199, 200]): "Rebalancing",
    bytes([75, 122, 154, 48, 140, 74, 123, 163]): "ClaimFee",
}

POSITION_BASE_SIZE = 8120
POSITION_BIN_DATA_SIZE = 112
BIN_SIZE = 144


def _pk(b):
    return base58.b58encode(bytes(b)).decode()


def _u(b, off, n):
    return int.from_bytes(b[off:off + n], "little")


def _i(b, off, n):
    return int.from_bytes(b[off:off + n], "little", signed=True)


def account_bytes(acc):
    """Raw bytes from an RPC account dict (base64 encoding)."""
    if not acc:
        return None
    data = acc["data"]
    return base64.b64decode(data[0] if isinstance(data, list) else data)


# ---- accounts -------------------------------------------------------------------

@dataclass
class LbPair:
    address: str
    active_id: int
    bin_step: int
    status: int
    token_x_mint: str
    token_y_mint: str
    reserve_x: str
    reserve_y: str


def decode_lb_pair(address, data):
    if data is None or data[:8] != DISC_LB_PAIR:
        raise ValueError(f"{address} is not a DLMM LbPair")
    return LbPair(
        address=address,
        active_id=_i(data, 76, 4),
        bin_step=_u(data, 80, 2),
        status=data[82],
        token_x_mint=_pk(data[88:120]),
        token_y_mint=_pk(data[120:152]),
        reserve_x=_pk(data[152:184]),
        reserve_y=_pk(data[184:216]),
    )


@dataclass
class PositionBin:
    share: int
    fee_x_complete: int
    fee_y_complete: int
    fee_x_pending: int
    fee_y_pending: int


@dataclass
class Position:
    address: str
    lb_pair: str
    owner: str
    lower_bin_id: int
    upper_bin_id: int
    last_updated_at: int
    total_claimed_fee_x: int
    total_claimed_fee_y: int
    operator: str
    fee_owner: str
    bins: list = field(default_factory=list)

    @property
    def width(self):
        return self.upper_bin_id - self.lower_bin_id + 1


def decode_position(address, data):
    if data is None or data[:8] != DISC_POSITION_V2:
        raise ValueError(f"{address} is not a DLMM PositionV2")
    lower = _i(data, 7912, 4)
    upper = _i(data, 7916, 4)
    width = upper - lower + 1
    bins = []
    for idx in range(width):
        if idx < DEFAULT_BIN_PER_POSITION:
            share = _u(data, 72 + idx * 16, 16)
            f = 4552 + idx * 48
        else:
            base = POSITION_BASE_SIZE + (idx - DEFAULT_BIN_PER_POSITION) * POSITION_BIN_DATA_SIZE
            share = _u(data, base, 16)
            f = base + 64
        bins.append(PositionBin(
            share=share,
            fee_x_complete=_u(data, f, 16),
            fee_y_complete=_u(data, f + 16, 16),
            fee_x_pending=_u(data, f + 32, 8),
            fee_y_pending=_u(data, f + 40, 8),
        ))
    return Position(
        address=address,
        lb_pair=_pk(data[8:40]),
        owner=_pk(data[40:72]),
        lower_bin_id=lower,
        upper_bin_id=upper,
        last_updated_at=_i(data, 7920, 8),
        total_claimed_fee_x=_u(data, 7928, 8),
        total_claimed_fee_y=_u(data, 7936, 8),
        operator=_pk(data[7960:7992]),
        fee_owner=_pk(data[8001:8033]),
        bins=bins,
    )


@dataclass
class Bin:
    amount_x: int
    amount_y: int
    liquidity_supply: int
    fee_x_per_token: int
    fee_y_per_token: int


def decode_bin_array(data):
    """Returns (index, {bin_id: Bin})."""
    if data is None or data[:8] != DISC_BIN_ARRAY:
        raise ValueError("not a DLMM BinArray")
    index = _i(data, 8, 8)
    out = {}
    for k in range(BINS_PER_ARRAY):
        o = 56 + k * BIN_SIZE
        out[index * BINS_PER_ARRAY + k] = Bin(
            amount_x=_u(data, o, 8),
            amount_y=_u(data, o + 8, 8),
            liquidity_supply=_u(data, o + 32, 16),
            fee_x_per_token=_u(data, o + 80, 16),
            fee_y_per_token=_u(data, o + 96, 16),
        )
    return index, out


# ---- PDAs -----------------------------------------------------------------------

def bin_array_index(bin_id):
    # floor division matches the SDK's binIdToBinArrayIndex for negatives
    return bin_id // BINS_PER_ARRAY


def bin_array_address(lb_pair, index):
    seed = index.to_bytes(8, "little", signed=True)
    pda, _ = Pubkey.find_program_address(
        [b"bin_array", bytes(Pubkey.from_string(lb_pair)), seed], _DLMM_PK)
    return str(pda)


def bin_arrays_for_range(lb_pair, lower_bin_id, upper_bin_id):
    return [bin_array_address(lb_pair, i)
            for i in range(bin_array_index(lower_bin_id), bin_array_index(upper_bin_id) + 1)]


# ---- price math -----------------------------------------------------------------

def bin_price(bin_id, bin_step, decimals_x, decimals_y):
    """UI price of token X denominated in token Y for a bin."""
    return (1 + bin_step / BASIS_POINT_MAX) ** bin_id * 10 ** (decimals_x - decimals_y)


def price_to_bin_id(price, bin_step, decimals_x, decimals_y, round_down=True):
    raw = price / 10 ** (decimals_x - decimals_y)
    b = math.log(raw) / math.log(1 + bin_step / BASIS_POINT_MAX)
    return math.floor(b) if round_down else math.ceil(b)


@dataclass
class PositionAmounts:
    amount_x: int
    amount_y: int
    fee_x: int
    fee_y: int
    bins_with_liquidity: int


def position_amounts(position, bins_by_id):
    """Token amounts and unclaimed fees held by a position (raw units).

    Mirrors DLMM SDK processPosition: amount = share * bin_amount / supply,
    fee = ((share >> 64) * (fee_per_token_stored - fee_per_token_complete)) >> 64 + pending.
    """
    ax = ay = fx = fy = n = 0
    for idx, pb in enumerate(position.bins):
        b = bins_by_id.get(position.lower_bin_id + idx)
        if b is None:
            continue
        if pb.share and b.liquidity_supply:
            ax += pb.share * b.amount_x // b.liquidity_supply
            ay += pb.share * b.amount_y // b.liquidity_supply
            n += 1
        if pb.share:
            fx += ((pb.share >> SCALE_OFFSET) * max(0, b.fee_x_per_token - pb.fee_x_complete)) >> SCALE_OFFSET
            fy += ((pb.share >> SCALE_OFFSET) * max(0, b.fee_y_per_token - pb.fee_y_complete)) >> SCALE_OFFSET
        fx += pb.fee_x_pending
        fy += pb.fee_y_pending
    return PositionAmounts(ax, ay, fx, fy, n)


# ---- events ---------------------------------------------------------------------

def decode_event(data):
    """Decode a DLMM emit_cpi event payload. Returns dict or None."""
    if len(data) < 16 or data[:8] != EVENT_IX_TAG:
        return None
    name = EVENTS.get(bytes(data[8:16]))
    if not name:
        return None
    p = data[16:]
    if name == "PositionCreate":
        return {"event": name, "lb_pair": _pk(p[0:32]), "position": _pk(p[32:64]), "owner": _pk(p[64:96])}
    if name == "PositionClose":
        return {"event": name, "position": _pk(p[0:32]), "owner": _pk(p[32:64])}
    if name in ("AddLiquidity", "RemoveLiquidity"):
        return {"event": name, "lb_pair": _pk(p[0:32]), "owner": _pk(p[32:64]), "position": _pk(p[64:96]),
                "amount_x": _u(p, 96, 8), "amount_y": _u(p, 104, 8), "active_bin_id": _i(p, 112, 4)}
    if name == "Rebalancing":
        return {"event": name, "lb_pair": _pk(p[0:32]), "position": _pk(p[32:64]), "owner": _pk(p[64:96]),
                "active_bin_id": _i(p, 96, 4),
                "x_withdrawn": _u(p, 100, 8), "x_added": _u(p, 108, 8),
                "y_withdrawn": _u(p, 116, 8), "y_added": _u(p, 124, 8),
                "old_min_id": _i(p, 148, 4), "old_max_id": _i(p, 152, 4),
                "new_min_id": _i(p, 156, 4), "new_max_id": _i(p, 160, 4)}
    if name == "ClaimFee":
        return {"event": name, "lb_pair": _pk(p[0:32]), "position": _pk(p[32:64]), "owner": _pk(p[64:96]),
                "fee_x": _u(p, 96, 8), "fee_y": _u(p, 104, 8)}
    return None


def tx_account_keys(tx):
    msg = tx["transaction"]["message"]
    keys = [k if isinstance(k, str) else k["pubkey"] for k in msg["accountKeys"]]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return keys + loaded.get("writable", []) + loaded.get("readonly", [])


def tx_dlmm_events(tx):
    """All DLMM position events in a transaction (top-level and inner), in order."""
    keys = tx_account_keys(tx)
    msg = tx["transaction"]["message"]
    meta = tx.get("meta") or {}
    ixs = list(msg.get("instructions", []))
    for inner in meta.get("innerInstructions") or []:
        ixs.extend(inner.get("instructions", []))
    events = []
    for ix in ixs:
        pid = ix.get("programIdIndex")
        if pid is None or pid >= len(keys) or keys[pid] != DLMM_PROGRAM:
            continue
        try:
            data = base58.b58decode(ix["data"])
        except (KeyError, ValueError):
            continue
        ev = decode_event(data)
        if ev:
            events.append(ev)
    return events
