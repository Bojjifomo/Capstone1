"""SQLite wallet database (data/wallets.db)."""
import json
import sqlite3
import threading

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

-- every ModifyLiquidity done through the PositionManager (salt = NFT id)
CREATE TABLE IF NOT EXISTS events (
  block INTEGER, log_index INTEGER, tx TEXT, ts INTEGER,
  token_id INTEGER, pool TEXT, tl INTEGER, tu INTEGER, delta TEXT,
  -- filled by the scorer: amounts (human units, always >= 0), fees realised
  -- here, USD price of each token at that block, pool fee (1e6 = 100%)
  v_state INTEGER DEFAULT 0, a0 REAL, a1 REAL, f0 REAL, f1 REAL, u0 REAL, u1 REAL, lp_fee INTEGER,
  PRIMARY KEY (block, log_index)
);
CREATE INDEX IF NOT EXISTS ev_token ON events(token_id);
CREATE INDEX IF NOT EXISTS ev_copy ON events(pool, tl, tu, ts);

-- position NFT transfers (mint = from 0x0, burn = to 0x0)
CREATE TABLE IF NOT EXISTS transfers (
  block INTEGER, log_index INTEGER, ts INTEGER, token_id INTEGER, from_addr TEXT, to_addr TEXT,
  PRIMARY KEY (block, log_index)
);
CREATE INDEX IF NOT EXISTS tr_token ON transfers(token_id);

CREATE TABLE IF NOT EXISTS pools (
  id TEXT PRIMARY KEY, c0 TEXT, c1 TEXT, fee INTEGER, spacing INTEGER, hooks TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  addr TEXT PRIMARY KEY, symbol TEXT, decimals INTEGER, supply REAL,
  price_usd REAL, mcap REAL, updated INTEGER
);

CREATE TABLE IF NOT EXISTS positions (
  token_id INTEGER PRIMARY KEY, owner TEXT, pool TEXT, tl INTEGER, tu INTEGER,
  open_block INTEGER, open_ts INTEGER, close_block INTEGER, close_ts INTEGER,
  minted INTEGER,          -- 1 when the mint is in our data, so PnL is complete
  liq TEXT, n_events INTEGER, last_ts INTEGER,
  valued INTEGER DEFAULT 0, -- 1 = every event priced
  dep_usd REAL, wd_usd REAL, fee_usd REAL, now_usd REAL, tok_out_usd REAL,
  pnl_usd REAL, cost_usd REAL, eth_pair INTEGER, single INTEGER, sell_fee REAL,
  launch INTEGER DEFAULT 0  -- seeded a big share of the token's supply: a token launch, not trading
);
CREATE INDEX IF NOT EXISTS pos_owner ON positions(owner);
CREATE INDEX IF NOT EXISTS pos_close ON positions(close_ts);

CREATE TABLE IF NOT EXISTS wallets (
  addr TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER,
  n_pos INTEGER, n_open INTEGER, n_closed INTEGER,
  eth_bal REAL, usd_bal REAL, bal_ts INTEGER,
  data TEXT, score REAL, scored_at INTEGER
);

CREATE TABLE IF NOT EXISTS labels (
  addr TEXT PRIMARY KEY, label TEXT, notes TEXT, starred INTEGER DEFAULT 0,
  source TEXT, added INTEGER
);

CREATE TABLE IF NOT EXISTS lpagent_cache (addr TEXT PRIMARY KEY, fetched INTEGER, data TEXT);
"""

_local = threading.local()


def connect(path=None):
    path = path or config.DB_PATH
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _migrate(con):
    """Columns added after a database was first created."""
    have = {r[1] for r in con.execute("PRAGMA table_info(positions)")}
    if "launch" not in have:
        con.execute("ALTER TABLE positions ADD COLUMN launch INTEGER DEFAULT 0")


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def set_meta(con, k, v):
    con.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v)))
