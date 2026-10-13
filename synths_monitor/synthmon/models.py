"""Normalized records every data source produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SIDES = ("buy", "sell", "transfer_in", "transfer_out")


@dataclass
class Trade:
    id: str
    ts: int
    coin: str  # token contract address, lower-case
    side: str  # one of SIDES
    eth_amount: float | None = None  # ETH paid (buy) or received (sell), excluding gas
    token_amount: float | None = None
    symbol: str | None = None
    tx_hash: str | None = None
    block: int | None = None
    gas_fee_eth: float | None = None
    reason: str | None = None  # exit reason text, when the source provides one
    source: str = ""
    raw: Any = None


@dataclass
class Post:
    id: str
    ts: int
    text: str
    coin: str | None = None
    symbol: str | None = None
    tx_hash: str | None = None
    source: str = ""
    raw: Any = None


@dataclass
class Bag:
    coin: str
    token_amount: float | None = None
    value_eth: float | None = None
    symbol: str | None = None
    source: str = ""
    raw: Any = None


@dataclass
class CoinInfo:
    coin: str
    symbol: str | None = None
    name: str | None = None
    created_ts: int | None = None
    raw: Any = None


@dataclass
class CoinStats:
    coin: str
    holders: int | None = None
    buys: int | None = None
    sells: int | None = None
    buy_eth: float | None = None
    sell_eth: float | None = None
    raised_eth: float | None = None
    progress_pct: float | None = None
    symbol: str | None = None
    source: str = ""
    raw: Any = None


@dataclass
class TokenTransfer:
    uid: str
    coin: str
    block: int
    ts: int | None
    tx_hash: str
    from_addr: str
    to_addr: str
    amount: float


@dataclass
class PollStatus:
    ok: bool = True
    sources: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def note(self, kind: str, source: str) -> None:
        self.sources[kind] = source

    def error(self, msg: str) -> None:
        self.errors.append(msg)
