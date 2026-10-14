"""Build, simulate and send Meteora DLMM transactions (open / close positions).

Instruction layouts follow the on-chain lb_clmm IDL; account order and the strategy
encoding mirror @meteora-ag/dlmm (initializePositionAndAddLiquidityByStrategy,
removeLiquidity with shouldClaimAndClose).
"""

import base64
import struct
import time

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.signature import Signature

from . import dlmm
from .tokens import SOL_MINT

P = Pubkey.from_string
DLMM = P(dlmm.DLMM_PROGRAM)
EVENT_AUTHORITY = Pubkey.find_program_address([b"__event_authority"], DLMM)[0]
SYSTEM = P("11111111111111111111111111111111")
RENT = P("SysvarRent111111111111111111111111111111111")
TOKEN = P("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022 = P("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA_PROGRAM = P("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
MEMO = P("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
WSOL = P(SOL_MINT)

D_INIT_POSITION = bytes([219, 192, 234, 71, 190, 191, 102, 80])
D_INIT_BIN_ARRAY = bytes([35, 86, 19, 185, 78, 212, 75, 211])
D_ADD_BY_STRATEGY2 = bytes([3, 221, 149, 218, 111, 141, 118, 213])
D_REMOVE_BY_RANGE2 = bytes([204, 2, 195, 145, 53, 145, 145, 205])
D_CLAIM_FEE2 = bytes([112, 191, 101, 171, 28, 144, 127, 187])
D_CLOSE_IF_EMPTY = bytes([59, 124, 212, 118, 91, 152, 110, 157])

# StrategyType enum index (IDL); the SDK uses the *ImBalanced variants with parameteres[0] = favor side
STRATEGY = {"spot": 6, "curve": 7, "bidask": 8}
BITMAP_HALF = 512          # internal bitmap covers bin array index [-512, 511]
MAX_BINS_PER_TX = 70       # DEFAULT_BIN_PER_POSITION: one position / one remove chunk
MAX_ACTIVE_BIN_SLIPPAGE = 3


def _m(pubkey, writable=False, signer=False):
    return AccountMeta(pubkey, is_signer=signer, is_writable=writable)


def ata(owner, mint, token_program=TOKEN):
    return Pubkey.find_program_address([bytes(owner), bytes(token_program), bytes(mint)], ATA_PROGRAM)[0]


def bitmap_extension(lb_pair):
    return Pubkey.find_program_address([b"bitmap", bytes(lb_pair)], DLMM)[0]


def _remaining_info(hook_x=0, hook_y=0):
    # RemainingAccountsInfo { slices: [TransferHookX(len), TransferHookY(len)] }
    return struct.pack("<I", 2) + bytes([0, hook_x, 1, hook_y])


# ---- generic instructions ----------------------------------------------------------

def ix_create_ata_idempotent(payer, owner, mint, token_program=TOKEN):
    return Instruction(ATA_PROGRAM, bytes([1]), [
        _m(payer, True, True), _m(ata(owner, mint, token_program), True), _m(owner), _m(mint),
        _m(SYSTEM), _m(token_program)])


def ix_transfer_sol(src, dst, lamports):
    return Instruction(SYSTEM, struct.pack("<IQ", 2, lamports), [_m(src, True, True), _m(dst, True)])


def ix_sync_native(account):
    return Instruction(TOKEN, bytes([17]), [_m(account, True)])


def ix_close_token_account(account, dest, owner, token_program=TOKEN):
    return Instruction(token_program, bytes([9]), [_m(account, True), _m(dest, True), _m(owner, False, True)])


# ---- DLMM instructions -------------------------------------------------------------

def ix_initialize_position(payer, position, lb_pair, owner, lower_bin_id, width):
    data = D_INIT_POSITION + struct.pack("<ii", lower_bin_id, width)
    return Instruction(DLMM, data, [
        _m(payer, True, True), _m(position, True, True), _m(lb_pair), _m(owner, False, True),
        _m(SYSTEM), _m(RENT), _m(EVENT_AUTHORITY), _m(DLMM)])


def ix_initialize_bin_array(lb_pair, index, funder):
    bin_array = P(dlmm.bin_array_address(str(lb_pair), index))
    return Instruction(DLMM, D_INIT_BIN_ARRAY + struct.pack("<q", index), [
        _m(lb_pair), _m(bin_array, True), _m(funder, True, True), _m(SYSTEM)])


def _bin_array_metas(lb_pair, lower, upper):
    return [_m(P(k), True) for k in dlmm.bin_arrays_for_range(str(lb_pair), lower, upper)]


def ix_add_liquidity_by_strategy(pool, position, owner, amount_x, amount_y, lower, upper, strategy="spot",
                                 single_sided_x=False, max_slippage_bins=MAX_ACTIVE_BIN_SLIPPAGE):
    params = bytearray(64)
    params[0] = 1 if single_sided_x else 0
    data = (D_ADD_BY_STRATEGY2
            + struct.pack("<QQii", amount_x, amount_y, pool.active_id, max_slippage_bins)
            + struct.pack("<ii", lower, upper) + bytes([STRATEGY[strategy]]) + bytes(params)
            + _remaining_info())
    lb = P(pool.address)
    accs = [
        _m(position, True), _m(lb, True), pool.bitmap_meta(lower, upper),
        _m(ata(owner, P(pool.token_x_mint), pool.program_x), True),
        _m(ata(owner, P(pool.token_y_mint), pool.program_y), True),
        _m(P(pool.reserve_x), True), _m(P(pool.reserve_y), True),
        _m(P(pool.token_x_mint)), _m(P(pool.token_y_mint)),
        _m(owner, False, True), _m(pool.program_x), _m(pool.program_y), _m(EVENT_AUTHORITY), _m(DLMM),
    ] + _bin_array_metas(lb, lower, upper)
    return Instruction(DLMM, data, accs)


def ix_remove_liquidity_by_range(pool, position, owner, lower, upper, bps=10_000):
    data = D_REMOVE_BY_RANGE2 + struct.pack("<iiH", lower, upper, bps) + _remaining_info()
    lb = P(pool.address)
    accs = [
        _m(position, True), _m(lb, True), pool.bitmap_meta(lower, upper),
        _m(ata(owner, P(pool.token_x_mint), pool.program_x), True),
        _m(ata(owner, P(pool.token_y_mint), pool.program_y), True),
        _m(P(pool.reserve_x), True), _m(P(pool.reserve_y), True),
        _m(P(pool.token_x_mint)), _m(P(pool.token_y_mint)),
        _m(owner, False, True), _m(pool.program_x), _m(pool.program_y), _m(MEMO), _m(EVENT_AUTHORITY), _m(DLMM),
    ] + _bin_array_metas(lb, lower, upper)
    return Instruction(DLMM, data, accs)


def ix_claim_fee(pool, position, owner, lower, upper):
    data = D_CLAIM_FEE2 + struct.pack("<ii", lower, upper) + _remaining_info()
    lb = P(pool.address)
    accs = [
        _m(lb, True), _m(position, True), _m(owner, False, True),
        _m(P(pool.reserve_x), True), _m(P(pool.reserve_y), True),
        _m(ata(owner, P(pool.token_x_mint), pool.program_x), True),
        _m(ata(owner, P(pool.token_y_mint), pool.program_y), True),
        _m(P(pool.token_x_mint)), _m(P(pool.token_y_mint)),
        _m(pool.program_x), _m(pool.program_y), _m(MEMO), _m(EVENT_AUTHORITY), _m(DLMM),
    ] + _bin_array_metas(lb, lower, upper)
    return Instruction(DLMM, data, accs)


def ix_close_position_if_empty(position, owner):
    return Instruction(DLMM, D_CLOSE_IF_EMPTY, [
        _m(position, True), _m(owner, False, True), _m(owner, True), _m(EVENT_AUTHORITY), _m(DLMM)])


# ---- pool context ------------------------------------------------------------------

class PoolContext:
    """What the builders need about a pool: state, token programs, bitmap extension."""

    def __init__(self, rpc, lb_pair):
        acc = rpc.call("getAccountInfo", [lb_pair, {"encoding": "base64"}])["value"]
        pair = dlmm.decode_lb_pair(lb_pair, dlmm.account_bytes(acc))
        self.__dict__.update(pair.__dict__)
        mints = rpc.get_multiple_accounts([pair.token_x_mint, pair.token_y_mint], encoding="jsonParsed")
        self.program_x, self.program_y = (P(m["owner"]) for m in mints)
        self.hooks = []
        self.decimals = []
        for mint, m in zip((pair.token_x_mint, pair.token_y_mint), mints):
            info = m["data"]["parsed"]["info"]
            self.decimals.append(info.get("decimals", 0))
            for ext in info.get("extensions") or []:
                if ext.get("extension") == "transferHook" and (ext.get("state") or {}).get("programId"):
                    self.hooks.append(mint)
        ext_key = bitmap_extension(P(lb_pair))
        self.bitmap_ext = ext_key if rpc.call("getAccountInfo", [str(ext_key), {"encoding": "base64",
                                              "dataSlice": {"offset": 0, "length": 0}}])["value"] else None

    def bitmap_meta(self, lower, upper):
        lo, hi = dlmm.bin_array_index(lower), dlmm.bin_array_index(upper)
        needed = lo < -BITMAP_HALF or hi > BITMAP_HALF - 1
        if needed and self.bitmap_ext is None:
            raise ValueError("range di luar bitmap pool dan bitmap extension belum ada")
        return _m(self.bitmap_ext, True) if (needed or self.bitmap_ext) else _m(DLMM)


# ---- transaction plans -------------------------------------------------------------

def missing_bin_arrays(rpc, lb_pair, lower, upper):
    keys = dlmm.bin_arrays_for_range(lb_pair, lower, upper)
    accs = rpc.get_multiple_accounts(keys, encoding="base64")
    first = dlmm.bin_array_index(lower)
    return [first + i for i, a in enumerate(accs) if a is None]


def open_position_ixs(rpc, pool, owner, position, lower, upper, amount_x, amount_y, strategy="spot",
                      max_slippage_bins=MAX_ACTIVE_BIN_SLIPPAGE):
    """Instructions to create a position over [lower, upper] and deposit the amounts (raw units)."""
    if upper - lower + 1 > MAX_BINS_PER_TX:
        raise ValueError(f"range {upper - lower + 1} bins; maksimal {MAX_BINS_PER_TX} bins per posisi")
    if pool.hooks:
        raise ValueError("token dengan transfer hook belum didukung")
    lb = P(pool.address)
    ixs = [ix_initialize_position(owner, position, lb, owner, lower, upper - lower + 1)]
    for idx in missing_bin_arrays(rpc, pool.address, lower, upper):
        ixs.append(ix_initialize_bin_array(lb, idx, owner))
    mx, my = P(pool.token_x_mint), P(pool.token_y_mint)
    ixs.append(ix_create_ata_idempotent(owner, owner, mx, pool.program_x))
    ixs.append(ix_create_ata_idempotent(owner, owner, my, pool.program_y))
    for mint, amount, prog in ((mx, amount_x, pool.program_x), (my, amount_y, pool.program_y)):
        if mint == WSOL and amount:
            acct = ata(owner, mint, prog)
            ixs += [ix_transfer_sol(owner, acct, amount), ix_sync_native(acct)]
    single_x = amount_y == 0 and amount_x > 0
    ixs.append(ix_add_liquidity_by_strategy(pool, position, owner, amount_x, amount_y, lower, upper, strategy,
                                            single_x, max_slippage_bins))
    if WSOL in (mx, my):
        ixs.append(ix_close_token_account(ata(owner, WSOL), owner, owner))
    return ixs


def close_position_ixs(pool, owner, position_state):
    """Remove everything, claim fees and close (positions up to 70 bins: one transaction)."""
    if pool.hooks:
        raise ValueError("token dengan transfer hook belum didukung")
    lower, upper = position_state.lower_bin_id, position_state.upper_bin_id
    if upper - lower + 1 > MAX_BINS_PER_TX:
        raise ValueError("posisi lebih dari 70 bins: tutup manual di Meteora")
    pos = P(position_state.address)
    mx, my = P(pool.token_x_mint), P(pool.token_y_mint)
    ixs = [ix_create_ata_idempotent(owner, owner, mx, pool.program_x),
           ix_create_ata_idempotent(owner, owner, my, pool.program_y)]
    if any(b.share for b in position_state.bins):
        ixs.append(ix_remove_liquidity_by_range(pool, pos, owner, lower, upper))
    ixs.append(ix_claim_fee(pool, pos, owner, lower, upper))
    ixs.append(ix_close_position_if_empty(pos, owner))
    if WSOL in (mx, my):
        ixs.append(ix_close_token_account(ata(owner, WSOL), owner, owner))
    return ixs


# ---- simulate / send ---------------------------------------------------------------

def _tx(ixs, payer, signers, blockhash, cu_limit=None, cu_price=None):
    pre = []
    if cu_limit:
        pre.append(set_compute_unit_limit(cu_limit))
    if cu_price:
        pre.append(set_compute_unit_price(cu_price))
    msg = MessageV0.try_compile(payer, pre + ixs, [], blockhash)
    if signers is None:  # simulation without keys
        n = msg.header.num_required_signatures
        return VersionedTransaction.populate(msg, [Signature.default()] * n)
    return VersionedTransaction(msg, signers)


def simulate(rpc, ixs, payer):
    """Simulate with sigVerify off (no keys needed). Returns (ok, units, logs, err)."""
    tx = _tx(ixs, payer, None, Hash.default(), cu_limit=1_400_000)
    res = rpc.call("simulateTransaction", [base64.b64encode(bytes(tx)).decode(), {
        "encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "confirmed"}])
    v = res["value"]
    return v.get("err") is None, v.get("unitsConsumed") or 0, v.get("logs") or [], v.get("err")


def send(rpc, ixs, payer_kp, extra_signers=(), cu_price=50_000, timeout=60):
    """Simulate for the CU budget, sign, send, wait for confirmation. Returns signature."""
    ok, units, logs, err = simulate(rpc, ixs, payer_kp.pubkey())
    if not ok:
        raise RuntimeError(f"simulasi gagal: {err} | {' / '.join(logs[-3:])}")
    cu_limit = min(1_400_000, int(units * 1.2) + 20_000)
    blockhash = Hash.from_string(rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"])
    tx = _tx(ixs, payer_kp.pubkey(), [payer_kp, *extra_signers], blockhash, cu_limit, cu_price)
    sig = rpc.call("sendTransaction", [base64.b64encode(bytes(tx)).decode(), {
        "encoding": "base64", "skipPreflight": True, "maxRetries": 5}])
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = rpc.call("getSignatureStatuses", [[sig]])["value"][0]
        if st:
            if st.get("err"):
                raise RuntimeError(f"transaksi gagal on-chain: {st['err']} ({sig})")
            if st.get("confirmationStatus") in ("confirmed", "finalized"):
                return sig
        time.sleep(2)
    raise RuntimeError(f"transaksi belum terkonfirmasi setelah {timeout} dtk: {sig}")


def new_position_keypair():
    return Keypair()
