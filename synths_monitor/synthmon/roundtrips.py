"""Pair buys and sells per coin into round trips.

A round trip opens on the first buy while the bot is flat in a coin and
closes when the position is back to (near) zero. Partial sells stay inside
the same round trip; its exit type is the exit type of the sell that closed
it. When a source gives no token amounts for a coin, each sell closes the
round trip instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .db import DB
from .models import Trade


@dataclass
class RoundTrip:
    coin: str
    symbol: str | None
    open_ts: int
    close_ts: int | None = None
    n_buys: int = 0
    n_sells: int = 0
    buy_eth: float = 0.0
    sell_eth: float = 0.0
    tokens_bought: float = 0.0
    tokens_sold: float = 0.0
    gas_eth: float = 0.0
    exit_type: str | None = None
    exit_post_id: str | None = None
    # False when tokens arrived without a priced buy (transfer, launch allocation) or ETH legs are missing.
    cost_basis_complete: bool = True
    trade_ids: list[str] = field(default_factory=list)
    peak_tokens: float = 0.0

    @property
    def status(self) -> str:
        return "closed" if self.close_ts is not None else "open"

    @property
    def pnl_eth(self) -> float | None:
        if self.close_ts is None or not self.cost_basis_complete or self.buy_eth <= 0:
            return None
        return self.sell_eth - self.buy_eth

    @property
    def pnl_pct(self) -> float | None:
        pnl = self.pnl_eth
        return None if pnl is None else pnl / self.buy_eth * 100

    @property
    def hold_seconds(self) -> int | None:
        return None if self.close_ts is None else self.close_ts - self.open_ts


def build_round_trips(
    trades: list[Trade], exits: dict[str, tuple[str, str | None]] | None = None, dust_fraction: float = 0.005
) -> list[RoundTrip]:
    exits = exits or {}
    by_coin: dict[str, list[Trade]] = {}
    for t in sorted(trades, key=lambda t: (t.ts, t.block or 0, t.id)):
        by_coin.setdefault(t.coin, []).append(t)

    result: list[RoundTrip] = []
    for coin, coin_trades in by_coin.items():
        track_tokens = all(t.token_amount is not None for t in coin_trades)
        rt: RoundTrip | None = None
        position = 0.0
        for t in coin_trades:
            if rt is None:
                rt = RoundTrip(coin=coin, symbol=t.symbol, open_ts=t.ts)
                position = 0.0
            rt.symbol = rt.symbol or t.symbol
            rt.trade_ids.append(t.id)
            rt.gas_eth += t.gas_fee_eth or 0.0
            tokens = t.token_amount or 0.0
            if t.side in ("buy", "transfer_in"):
                if t.side == "buy":
                    rt.n_buys += 1
                    if t.eth_amount is None:
                        rt.cost_basis_complete = False
                    rt.buy_eth += t.eth_amount or 0.0
                else:
                    rt.cost_basis_complete = False
                rt.tokens_bought += tokens
                position += tokens
                rt.peak_tokens = max(rt.peak_tokens, position)
                continue

            if t.side == "sell":
                rt.n_sells += 1
                if t.eth_amount is None:
                    rt.cost_basis_complete = False
                rt.sell_eth += t.eth_amount or 0.0
            if rt.tokens_bought == 0 and rt.n_buys == 0:
                rt.cost_basis_complete = False  # sold tokens this data never saw being bought
            rt.tokens_sold += tokens
            position -= tokens
            closed = position <= max(rt.peak_tokens * dust_fraction, 1e-12) if track_tokens else True
            if closed:
                rt.close_ts = t.ts
                if t.side == "sell":
                    rt.exit_type, rt.exit_post_id = exits.get(t.id, ("unknown", None))
                else:
                    rt.exit_type = "transfer"
                result.append(rt)
                rt = None
        if rt is not None:
            result.append(rt)
    return sorted(result, key=lambda r: (r.open_ts, r.coin))


def store_round_trips(db: DB, trips: list[RoundTrip]) -> None:
    db.execute("DELETE FROM round_trips")
    for r in trips:
        db.execute(
            "INSERT OR REPLACE INTO round_trips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r.coin, r.symbol, r.open_ts, r.close_ts, r.status, r.n_buys, r.n_sells, r.buy_eth, r.sell_eth,
                r.tokens_bought, r.tokens_sold, r.pnl_eth, r.pnl_pct, r.hold_seconds, r.gas_eth, r.exit_type,
                r.exit_post_id, int(r.cost_basis_complete), json.dumps(r.trade_ids),
            ),
        )
