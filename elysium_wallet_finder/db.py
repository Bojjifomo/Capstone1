"""SQLite wallet database (data/wallets.db).

A position's identity is pos_key = venue || ':' || token_id. Two Uniswap v4
PositionManagers (and the v3 NPMs) all mint token ids from 1, so the id alone
collides. venue is the position-manager address, 'v3-eoa' is not used; a
direct EOA liquidity position uses venue 'eoa:' || sender.
"""
import json
import sqlite3
import threading

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

-- LP events. kind: v4 (ModifyLiquidity) | inc | dec | col (v3 NPM).
-- r0/r1: raw token amounts from a v3 event (the v4 amounts are computed later).
-- pos_key is generated so inserts that only set venue + token_id stay valid.
CREATE TABLE IF NOT EXISTS events (
  block INTEGER, log_index INTEGER, tx TEXT, ts INTEGER,
  token_id INTEGER, pool TEXT, tl INTEGER, tu INTEGER, delta TEXT, salt TEXT,
  venue TEXT NOT NULL DEFAULT 'v4',
  kind TEXT NOT NULL DEFAULT 'v4',
  r0 TEXT, r1 TEXT,
  v_state INTEGER DEFAULT 0, a0 REAL, a1 REAL, f0 REAL, f1 REAL, u0 REAL, u1 REAL, lp_fee INTEGER,
  pos_key TEXT,
  PRIMARY KEY (block, log_index)
);
CREATE INDEX IF NOT EXISTS ev_pos ON events(pos_key);
CREATE INDEX IF NOT EXISTS ev_copy ON events(pool, tl, tu, ts);

-- position NFT transfers (mint = from 0x0, burn = to 0x0)
CREATE TABLE IF NOT EXISTS transfers (
  block INTEGER, log_index INTEGER, ts INTEGER, token_id INTEGER, from_addr TEXT, to_addr TEXT,
  venue TEXT NOT NULL DEFAULT 'v4',
  pos_key TEXT,
  PRIMARY KEY (block, log_index)
);
CREATE INDEX IF NOT EXISTS tr_pos ON transfers(pos_key);

CREATE TABLE IF NOT EXISTS pools (
  id TEXT PRIMARY KEY, c0 TEXT, c1 TEXT, fee INTEGER, spacing INTEGER, hooks TEXT,
  creator_fee INTEGER DEFAULT 0,  -- 1: launch pool, swap fees pay the locker not outside LPs
  venue TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  addr TEXT PRIMARY KEY, symbol TEXT, decimals INTEGER, supply REAL,
  price_usd REAL, mcap REAL, updated INTEGER
);

CREATE TABLE IF NOT EXISTS positions (
  token_id INTEGER, owner TEXT, pool TEXT, tl INTEGER, tu INTEGER,
  open_block INTEGER, open_ts INTEGER, close_block INTEGER, close_ts INTEGER,
  minted INTEGER,          -- 1 when the mint is in our data, so PnL is complete
  liq TEXT, n_events INTEGER, last_ts INTEGER,
  valued INTEGER DEFAULT 0, -- 1 = every event priced
  dep_usd REAL, wd_usd REAL, fee_usd REAL, now_usd REAL, tok_out_usd REAL,
  pnl_usd REAL, cost_usd REAL, eth_pair INTEGER, single INTEGER, sell_fee REAL,
  launch INTEGER DEFAULT 0,  -- seeded supply, or the ElysiumPad locker position
  venue TEXT NOT NULL DEFAULT 'v4',
  early INTEGER DEFAULT 0,   -- opened within EARLY_MINUTES of that pool's launch
  pos_key TEXT,
  PRIMARY KEY (venue, token_id)
);
CREATE INDEX IF NOT EXISTS pos_owner ON positions(owner);
CREATE INDEX IF NOT EXISTS pos_close ON positions(close_ts);
CREATE INDEX IF NOT EXISTS pos_pool ON positions(pool);
CREATE UNIQUE INDEX IF NOT EXISTS pos_key_uq ON positions(pos_key);

CREATE TRIGGER IF NOT EXISTS ev_poskey AFTER INSERT ON events
WHEN NEW.pos_key IS NULL
BEGIN
  UPDATE events SET pos_key = NEW.venue || ':' || NEW.token_id
  WHERE block = NEW.block AND log_index = NEW.log_index;
END;
CREATE TRIGGER IF NOT EXISTS tr_poskey AFTER INSERT ON transfers
WHEN NEW.pos_key IS NULL
BEGIN
  UPDATE transfers SET pos_key = NEW.venue || ':' || NEW.token_id
  WHERE block = NEW.block AND log_index = NEW.log_index;
END;
CREATE TRIGGER IF NOT EXISTS pos_poskey AFTER INSERT ON positions
WHEN NEW.pos_key IS NULL
BEGIN
  UPDATE positions SET pos_key = NEW.venue || ':' || NEW.token_id
  WHERE venue = NEW.venue AND token_id = NEW.token_id;
END;

CREATE TABLE IF NOT EXISTS wallets (
  addr TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER,
  n_pos INTEGER, n_open INTEGER, n_closed INTEGER,
  eth_bal REAL, usd_bal REAL, bal_ts INTEGER,
  data TEXT, score REAL, scored_at INTEGER
);

CREATE TABLE IF NOT EXISTS labels (
  addr TEXT PRIMARY KEY, label TEXT, notes TEXT, starred INTEGER DEFAULT 0,
  source TEXT, added INTEGER,
  ref TEXT  -- 'multiday' | 'degen': a reference wallet of that player class (also forces its class)
);

CREATE TABLE IF NOT EXISTS lpagent_cache (addr TEXT PRIMARY KEY, fetched INTEGER, data TEXT);

-- kept so a database copied from the Robinhood tool still opens; this app doesn't write it
CREATE TABLE IF NOT EXISTS slf_ranks (
  addr TEXT, pool TEXT, from_block INTEGER, to_block INTEGER, rank INTEGER, total INTEGER,
  pnl_usd REAL, pnl_pct REAL, deposited_usd REAL, positions INTEGER, computed_at INTEGER,
  PRIMARY KEY (addr, pool, from_block, to_block)
);

-- v4 contracts that turned out to be PositionManagers (nextTokenId + poolManager)
CREATE TABLE IF NOT EXISTS venues (
  addr TEXT PRIMARY KEY, kind TEXT, label TEXT, seen INTEGER
);
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
    """Columns added after a database was first created, and the reference
    wallets a new database starts with (once: deleting them sticks)."""
    have = {r[1] for r in con.execute("PRAGMA table_info(positions)")}
    if "launch" not in have:
        con.execute("ALTER TABLE positions ADD COLUMN launch INTEGER DEFAULT 0")
    if "early" not in have and "pos_key" in have:
        con.execute("ALTER TABLE positions ADD COLUMN early INTEGER DEFAULT 0")
    pools = {r[1] for r in con.execute("PRAGMA table_info(pools)")}
    if "creator_fee" not in pools:
        con.execute("ALTER TABLE pools ADD COLUMN creator_fee INTEGER DEFAULT 0")
    if "venue" not in pools:
        con.execute("ALTER TABLE pools ADD COLUMN venue TEXT")
    if "ref" not in {r[1] for r in con.execute("PRAGMA table_info(labels)")}:
        con.execute("ALTER TABLE labels ADD COLUMN ref TEXT")
    if get_meta(con, "refs_seeded") is None:
        for addr, (kind, name) in config.REFERENCE_WALLETS.items():
            con.execute("""INSERT INTO labels(addr, label, starred, source, added, ref) VALUES(?,?,1,'example',
                           strftime('%s','now'), ?) ON CONFLICT(addr) DO UPDATE SET ref=COALESCE(labels.ref, excluded.ref)""",
                        (addr, name, kind))
        set_meta(con, "refs_seeded", 1)
    con.commit()


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def set_meta(con, k, v):
    con.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v)))
