"""SQLite storage. All timestamps are unix seconds (UTC); all ETH values are floats in ETH."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Iterable

from .models import Bag, CoinInfo, CoinStats, Post, TokenTransfer, Trade
from .util import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_snapshots (
    ts INTEGER NOT NULL,
    address TEXT NOT NULL,
    eth_balance REAL NOT NULL,
    bags_value_eth REAL,
    total_value_eth REAL,
    source TEXT NOT NULL,
    PRIMARY KEY (ts, address)
);
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    coin TEXT NOT NULL,
    symbol TEXT,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell', 'transfer_in', 'transfer_out')),
    eth_amount REAL,
    token_amount REAL,
    tx_hash TEXT,
    block INTEGER,
    gas_fee_eth REAL,
    reason TEXT,
    source TEXT NOT NULL,
    raw TEXT,
    inserted_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS trades_ts ON trades (ts);
CREATE INDEX IF NOT EXISTS trades_coin_ts ON trades (coin, ts);
CREATE INDEX IF NOT EXISTS trades_tx ON trades (tx_hash);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    text TEXT NOT NULL,
    coin TEXT,
    symbol TEXT,
    tx_hash TEXT,
    exit_type TEXT,
    source TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS posts_ts ON posts (ts);
CREATE TABLE IF NOT EXISTS bags (
    ts INTEGER NOT NULL,
    coin TEXT NOT NULL,
    symbol TEXT,
    token_amount REAL,
    value_eth REAL,
    source TEXT,
    raw TEXT,
    PRIMARY KEY (ts, coin)
);
CREATE TABLE IF NOT EXISTS launched (
    coin TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    created_ts INTEGER,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS own_coin_snapshots (
    ts INTEGER NOT NULL,
    coin TEXT NOT NULL,
    holders INTEGER,
    buys INTEGER,
    sells INTEGER,
    buy_eth REAL,
    sell_eth REAL,
    raised_eth REAL,
    progress_pct REAL,
    source TEXT,
    raw TEXT,
    PRIMARY KEY (ts, coin)
);
CREATE TABLE IF NOT EXISTS coin_transfers (
    uid TEXT PRIMARY KEY,
    coin TEXT NOT NULL,
    block INTEGER NOT NULL,
    ts INTEGER,
    tx_hash TEXT,
    from_addr TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    amount REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS coin_transfers_coin ON coin_transfers (coin, block);
CREATE TABLE IF NOT EXISTS address_kind (
    address TEXT PRIMARY KEY,
    is_contract INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS leaderboard (
    ts INTEGER NOT NULL,
    handle TEXT NOT NULL,
    rank INTEGER,
    raw TEXT,
    PRIMARY KEY (ts, handle)
);
CREATE TABLE IF NOT EXISTS round_trips (
    coin TEXT NOT NULL,
    symbol TEXT,
    open_ts INTEGER NOT NULL,
    close_ts INTEGER,
    status TEXT NOT NULL,
    n_buys INTEGER,
    n_sells INTEGER,
    buy_eth REAL,
    sell_eth REAL,
    tokens_bought REAL,
    tokens_sold REAL,
    pnl_eth REAL,
    pnl_pct REAL,
    hold_seconds INTEGER,
    gas_eth REAL,
    exit_type TEXT,
    exit_post_id TEXT,
    cost_basis_complete INTEGER NOT NULL,
    trade_ids TEXT,
    PRIMARY KEY (coin, open_ts)
);
CREATE TABLE IF NOT EXISTS raw_responses (
    hash TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    url TEXT,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS polls (
    ts INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    sources TEXT,
    errors TEXT
);
"""


def _dump(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, separators=(",", ":"), default=str)


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def commit(self) -> None:
        self.conn.commit()

    # --- state -----------------------------------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def set_state(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # --- raw responses ---------------------------------------------------
    def save_raw(self, kind: str, url: str, body: Any) -> None:
        text = json.dumps(body, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{kind}|{text}".encode()).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO raw_responses (hash, ts, kind, url, body) VALUES (?, ?, ?, ?, ?)",
            (digest, now(), kind, url, text),
        )

    # --- writers -----------------------------------------------------------
    def add_wallet_snapshot(self, ts: int, address: str, eth: float, bags_value: float | None, source: str) -> None:
        total = eth + bags_value if bags_value is not None else None
        self.conn.execute(
            "INSERT OR REPLACE INTO wallet_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (ts, address, eth, bags_value, total, source),
        )

    def find_duplicate_trade(self, t: Trade, window: int = 180) -> bool:
        """True when the same trade is already stored from another source.

        Matches on tx hash when both sides have one; otherwise on coin, side,
        time within `window` seconds and ETH amount within 2%.
        """
        if t.tx_hash:
            row = self.conn.execute(
                "SELECT 1 FROM trades WHERE tx_hash = ? AND coin = ? AND side = ? AND id != ?",
                (t.tx_hash, t.coin, t.side, t.id),
            ).fetchone()
            if row:
                return True
        rows = self.conn.execute(
            "SELECT eth_amount, tx_hash FROM trades WHERE coin = ? AND side = ? AND source != ? AND id != ? "
            "AND ABS(ts - ?) <= ?",
            (t.coin, t.side, t.source, t.id, t.ts, window),
        ).fetchall()
        for row in rows:
            if t.tx_hash and row["tx_hash"] and row["tx_hash"] != t.tx_hash:
                continue
            a, b = row["eth_amount"], t.eth_amount
            if a is None or b is None or abs(a - b) <= 0.02 * max(abs(a), abs(b), 1e-12):
                return True
        return False

    def upsert_trade(self, t: Trade) -> bool:
        """Insert a trade. Returns True when it is new."""
        exists = self.conn.execute("SELECT 1 FROM trades WHERE id = ?", (t.id,)).fetchone()
        if exists:
            # Fill in fields another poll may have lacked (e.g. gas or the exit reason).
            self.conn.execute(
                "UPDATE trades SET gas_fee_eth = COALESCE(gas_fee_eth, ?), reason = COALESCE(reason, ?), "
                "symbol = COALESCE(symbol, ?), tx_hash = COALESCE(tx_hash, ?) WHERE id = ?",
                (t.gas_fee_eth, t.reason, t.symbol, t.tx_hash, t.id),
            )
            return False
        if self.find_duplicate_trade(t):
            return False
        self.conn.execute(
            "INSERT INTO trades (id, ts, coin, symbol, side, eth_amount, token_amount, tx_hash, block, gas_fee_eth, "
            "reason, source, raw, inserted_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t.id, t.ts, t.coin, t.symbol, t.side, t.eth_amount, t.token_amount, t.tx_hash, t.block,
                t.gas_fee_eth, t.reason, t.source, _dump(t.raw), now(),
            ),
        )
        return True

    def upsert_post(self, p: Post, exit_type: str | None) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO posts (id, ts, text, coin, symbol, tx_hash, exit_type, source, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (p.id, p.ts, p.text, p.coin, p.symbol, p.tx_hash, exit_type, p.source, _dump(p.raw)),
        )
        if cur.rowcount == 0:
            # Posts can be edited; keep the latest full text.
            self.conn.execute(
                "UPDATE posts SET text = ?, exit_type = ? WHERE id = ? AND text != ?", (p.text, exit_type, p.id, p.text)
            )
        return cur.rowcount > 0

    def add_bags(self, ts: int, bags: list[Bag]) -> None:
        for b in bags:
            self.conn.execute(
                "INSERT OR REPLACE INTO bags VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, b.coin, b.symbol, b.token_amount, b.value_eth, b.source, _dump(b.raw)),
            )

    def upsert_launched(self, coins: list[CoinInfo]) -> None:
        for c in coins:
            self.conn.execute(
                "INSERT INTO launched (coin, symbol, name, created_ts, raw) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(coin) DO UPDATE SET symbol = COALESCE(excluded.symbol, symbol), "
                "name = COALESCE(excluded.name, name), created_ts = COALESCE(excluded.created_ts, created_ts), "
                "raw = excluded.raw",
                (c.coin, c.symbol, c.name, c.created_ts, _dump(c.raw)),
            )

    def add_coin_snapshot(self, ts: int, s: CoinStats) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO own_coin_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, s.coin, s.holders, s.buys, s.sells, s.buy_eth, s.sell_eth, s.raised_eth, s.progress_pct, s.source,
             _dump(s.raw)),
        )

    def add_coin_transfers(self, transfers: list[TokenTransfer]) -> int:
        n = 0
        for t in transfers:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO coin_transfers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (t.uid, t.coin, t.block, t.ts, t.tx_hash, t.from_addr, t.to_addr, t.amount),
            )
            n += cur.rowcount
        return n

    def add_leaderboard(self, ts: int, handle: str, rank: int | None, raw: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO leaderboard VALUES (?, ?, ?, ?)", (ts, handle, rank, _dump(raw)))

    def add_poll(self, ts: int, ok: bool, sources: dict, errors: list[str]) -> None:
        self.conn.execute(
            "INSERT INTO polls VALUES (?, ?, ?, ?)", (ts, int(ok), json.dumps(sources), json.dumps(errors) if errors else None)
        )

    # --- readers -----------------------------------------------------------
    def trades(self, since: int | None = None, coin: str | None = None) -> list[Trade]:
        sql = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []
        if since is not None:
            sql += " AND ts >= ?"
            params.append(since)
        if coin:
            sql += " AND coin = ?"
            params.append(coin)
        sql += " ORDER BY ts, id"
        return [
            Trade(
                id=r["id"], ts=r["ts"], coin=r["coin"], side=r["side"], eth_amount=r["eth_amount"],
                token_amount=r["token_amount"], symbol=r["symbol"], tx_hash=r["tx_hash"], block=r["block"],
                gas_fee_eth=r["gas_fee_eth"], reason=r["reason"], source=r["source"],
            )
            for r in self.query(sql, params)
        ]

    def posts(self, since: int | None = None) -> list[Post]:
        rows = self.query("SELECT * FROM posts WHERE ts >= ? ORDER BY ts", (since or 0,))
        return [
            Post(id=r["id"], ts=r["ts"], text=r["text"], coin=r["coin"], symbol=r["symbol"], tx_hash=r["tx_hash"],
                 source=r["source"])
            for r in rows
        ]

    def latest_wallet_snapshot(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM wallet_snapshots ORDER BY ts DESC LIMIT 1").fetchone()

    def latest_coin_snapshot(self, coin: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM own_coin_snapshots WHERE coin = ? ORDER BY ts DESC LIMIT 1", (coin,)
        ).fetchone()

    def latest_bags(self) -> list[sqlite3.Row]:
        row = self.conn.execute("SELECT MAX(ts) AS ts FROM bags").fetchone()
        if row is None or row["ts"] is None:
            return []
        return self.query("SELECT * FROM bags WHERE ts = ?", (row["ts"],))

    def last_price(self, coin: str) -> float | None:
        """ETH per token from the most recent priced buy or sell of `coin`."""
        row = self.conn.execute(
            "SELECT eth_amount, token_amount FROM trades WHERE coin = ? AND side IN ('buy', 'sell') "
            "AND eth_amount > 0 AND token_amount > 0 ORDER BY ts DESC LIMIT 1",
            (coin,),
        ).fetchone()
        return None if row is None else row["eth_amount"] / row["token_amount"]
