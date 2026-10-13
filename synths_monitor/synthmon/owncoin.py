"""On-chain stats for the bot's own coin, rebuilt from its Transfer history."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from .db import DB
from .models import CoinStats
from .sources.chain import FallbackChain
from .util import ZERO_ADDRESS

DUST_TOKENS = 1e-9
MARKET_CANDIDATES = 25
log = logging.getLogger("synthmon")


def contract_flags(db: DB, chain: FallbackChain, addresses: set[str]) -> dict[str, bool]:
    """Whether each address has code, cached in address_kind and looked up in batches."""
    known = {r["address"]: bool(r["is_contract"]) for r in db.query("SELECT * FROM address_kind")}
    missing = sorted(a for a in addresses if a not in known)
    if missing:
        log.info("checking %d new addresses for contract code", len(missing))
        for addr, is_c in chain.call("is_contract_many", missing).items():
            db.execute("INSERT OR REPLACE INTO address_kind VALUES (?, ?)", (addr, int(is_c)))
            known[addr] = is_c
    return known


def sync_transfers(db: DB, chain: FallbackChain, coin: str, latest_block: int) -> bool:
    """Pull new Transfer events for `coin`; True once caught up to `latest_block`.

    A busy coin's history is fetched over several polls.
    """
    cursor_key = f"coin_transfers_block:{coin}"
    start = db.get_state(cursor_key, 0)
    if start > latest_block:
        return True
    transfers, next_block = chain.call("token_transfers", coin, start, latest_block)
    db.add_coin_transfers(transfers)
    db.set_state(cursor_key, next_block)
    db.commit()
    if next_block <= latest_block:
        log.info("own coin history: synced to block %d of %d; continuing next poll", next_block, latest_block)
        return False
    return True


def onchain_stats(db: DB, chain: FallbackChain, cfg: dict[str, Any], coin: str) -> CoinStats:
    """Holders and buy/sell counts from transfers.

    A transfer from a contract (curve, pool, router) to a wallet is a buy and
    the reverse is a sell; contract-to-contract hops are routing and are
    skipped. Mints and burns are not trades.
    """
    rows = db.query("SELECT from_addr, to_addr, amount FROM coin_transfers WHERE coin = ? ORDER BY block", (coin,))
    # The market side (curve, pool, router) is party to most transfers. Only the busiest counterparties
    # are checked for code; everyone else is taken to be a wallet. That keeps a coin with thousands of
    # holders to a few RPC calls instead of one per holder.
    activity = Counter(a for r in rows for a in (r["from_addr"], r["to_addr"]) if a != ZERO_ADDRESS)
    candidates = {a for a, _ in activity.most_common(MARKET_CANDIDATES)}
    flags = contract_flags(db, chain, candidates)
    if cfg["coin"]["curve_address"]:
        flags[cfg["coin"]["curve_address"]] = True
    if cfg["bot"]["wallet"]:
        flags[cfg["bot"]["wallet"]] = False  # the bot may use a smart account; it still trades as a wallet
    is_contract = defaultdict(bool, {a: flags.get(a, False) for a in candidates | set(flags)})
    balances: dict[str, float] = defaultdict(float)
    buys = sells = 0
    for row in rows:
        frm, to, amount = row["from_addr"], row["to_addr"], row["amount"]
        balances[frm] -= amount
        balances[to] += amount
        if ZERO_ADDRESS in (frm, to):
            continue
        if is_contract[frm] and not is_contract[to]:
            buys += 1
        elif is_contract[to] and not is_contract[frm]:
            sells += 1
    holders = sum(
        1 for addr, bal in balances.items() if addr != ZERO_ADDRESS and bal > DUST_TOKENS and not is_contract[addr]
    )
    stats = CoinStats(coin=coin, holders=holders, buys=buys, sells=sells, source=f"chain:{chain.last_source}")

    curve = cfg["coin"]["curve_address"]
    if curve:
        raised = chain.call("eth_balance", curve)
        for weth in cfg["chain"]["weth"]:
            raised += chain.call("erc20_balance", weth, curve)
        stats.raised_eth = raised
        stats.progress_pct = raised / cfg["coin"]["open_threshold_eth"] * 100
    return stats
