"""The tools in use, wrapped so one Claude agent can drive them:

  - RH Wallet Finder   (LP wallet data + analysis, rh_wallet_finder/)
  - RH Smart LP Finder (per-pool rankings, read through the Wallet Finder's import)
  - Web showcase       (the Vercel page fed by snapshots, rh_wallet_finder/web/)

Each Tool says how risky it is:
  read   runs straight away (reads the database or a README)
  write  asks first: it spends RPC quota, changes the database or uploads to the web

Tools run the existing CLI (rh_wallet_finder/finder.py) as a subprocess, so the agent
sees what you would see in the terminal and nothing here duplicates its logic.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
RH_DIR = ROOT / "rh_wallet_finder"
MAX_OUTPUT = 30_000
EVM_RE = re.compile(r"0x[0-9a-fA-F]{40}")
SELECT_RE = re.compile(r"^\s*(select|with)\b", re.I)
DOCS = {"overview": ROOT / "TOOLS.md", "rh_wallet_finder": RH_DIR / "README.md"}


class ToolError(Exception):
    """A bad input: goes back to Claude as an is_error tool result."""


@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    run: Callable[[dict], str]
    risk: str = "read"
    required: list = field(default_factory=list)

    def schema(self):
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object", "properties": self.properties,
                                 "required": self.required, "additionalProperties": False}}


def _clip(text):
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n… [cut: {len(text) - MAX_OUTPUT:,} more characters]"


def _rh(*argv, stdin=None, timeout=300):
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "PYTHONUNBUFFERED": "1"}
    try:
        p = subprocess.run([sys.executable, "finder.py", *argv], cwd=RH_DIR, input=stdin, capture_output=True,
                           text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        return _clip(f"[stopped after {timeout}s]\n{e.stdout if isinstance(e.stdout, str) else ''}")
    out = p.stdout + (f"\n[stderr]\n{p.stderr}" if p.stderr.strip() else "")
    return _clip((out.strip() or "(no output)") + (f"\n[exit code {p.returncode}]" if p.returncode else ""))


def _evm(addr):
    addr = (addr or "").strip()
    if not EVM_RE.fullmatch(addr):
        raise ToolError(f"not an EVM wallet address (0x + 40 hex): {addr!r}")
    return addr.lower()


def _int(v, lo, hi, name):
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be a whole number")
    if not lo <= v <= hi:
        raise ToolError(f"{name} must be between {lo} and {hi}")
    return str(v)


def _select_only(sql):
    sql = (sql or "").strip().rstrip(";").strip()
    if not SELECT_RE.match(sql) or ";" in sql:
        raise ToolError("only one SELECT (or WITH … SELECT) statement is allowed")
    return sql


# ── wallet data + analysis ──────────────────────────────────────────────────────────

def rh_status(_):
    return _rh("status")


def rh_top(i):
    argv = ["top", "--json", "-n", _int(i.get("n", 20), 1, 200, "n")]
    if i.get("kind"):
        argv += ["--kind", i["kind"]]
    if i.get("real"):
        argv.append("--real")
    if i.get("last_24h"):
        argv.append("--1d")
    return _rh(*argv)


def rh_wallet(i):
    argv = ["check", _evm(i.get("wallet")), "--json", "--positions", _int(i.get("positions", 30), 0, 300, "positions")]
    if i.get("real"):
        argv.append("--real")
    return _rh(*argv)


def rh_lpagent(i):
    return _rh("check", _evm(i.get("wallet")), timeout=120)


def rh_twin(i):
    return _rh("twin", _evm(i.get("wallet")), "-n", _int(i.get("n", 15), 1, 100, "n"))


def rh_pools(i):
    argv = ["pools", "--json", "-n", _int(i.get("n", 15), 1, 100, "n"),
            "--days", _int(i.get("days", 7), 1, 90, "days"),
            "--min-positions", _int(i.get("min_positions", 10), 1, 1000, "min_positions"),
            "--min-wallets", _int(i.get("min_wallets", 3), 1, 100, "min_wallets")]
    if i.get("sort"):
        argv += ["--sort", i["sort"]]
    return _rh(*argv)


def rh_compare(i):
    rows = (i.get("rows") or "").strip()
    if not rows:
        raise ToolError("paste the RH Discovery rows in `rows`")
    return _rh("compare", "-", stdin=rows)


def rh_sql(i):
    sql = _select_only(i.get("sql"))
    limit = int(_int(i.get("limit", 200), 1, 1000, "limit"))
    path = Path(os.environ.get("RHWF_DB") or RH_DIR / "data" / "wallets.db")  # same override as its config.py
    if not path.exists():
        return "No database yet: start the Wallet Finder (finder.py) and let it sync first."
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        con.execute("PRAGMA query_only=1")
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(limit + 1)
    except sqlite3.Error as e:
        raise ToolError(f"SQLite: {e}")
    finally:
        con.close()
    out = json.dumps([dict(zip(cols, r)) for r in rows[:limit]], default=str)
    return _clip(out + (f"\n[more rows: raise limit or narrow the query]" if len(rows) > limit else ""))


def rh_sync(i):
    return _rh("sync", "--segments", _int(i.get("segments", 3), 1, 50, "segments"), timeout=1800)


def rh_score(_):
    return _rh("score", timeout=1800)


def rh_import(i):
    text = (i.get("text") or "").strip()
    if not EVM_RE.search(text):
        raise ToolError("no 0x… wallet address in `text`")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
    try:
        return _rh("import", f.name)
    finally:
        os.unlink(f.name)


# ── RH Smart LP Finder ──────────────────────────────────────────────────────────────

def slf_summary(i):
    return _rh("smart-lp", "-n", _int(i.get("n", 30), 1, 200, "n"))


def slf_import(_):
    return _rh("import-smart-lp", timeout=300)


# ── web showcase ────────────────────────────────────────────────────────────────────

def web_check(_):
    return _rh("web-check", timeout=60)


def web_publish(_):
    return _rh("publish", timeout=180)


# ── docs ────────────────────────────────────────────────────────────────────────────

def read_docs(i):
    path = DOCS.get(i.get("topic"))
    if not path:
        raise ToolError(f"topic must be one of {', '.join(DOCS)}")
    return _clip(path.read_text()) if path.exists() else f"{path.name} not found"


# ── registry ────────────────────────────────────────────────────────────────────────

B = {"type": "boolean"}
N = {"type": "integer"}
WALLET = {"type": "string", "description": "EVM wallet, 0x + 40 hex"}


def all_tools():
    return [
        Tool("read_docs", "Read a README: overview (TOOLS.md, what each tool is and how they connect) or "
             "rh_wallet_finder (every column definition, setting, the RPC budget and the known limits).",
             {"topic": {"type": "string", "enum": list(DOCS)}}, read_docs, required=["topic"]),

        Tool("rh_status", "Wallet Finder state as JSON: wallets discovered/scored, multiday vs degen counts, "
             "synced_at / scored_at (unix), price queue, rpc_today vs rpc_cap, whether RPC, LP Agent key and "
             "the web publish link are set, Smart LP Finder link, last publish.", {}, rh_status),
        Tool("rh_top", "Top Uniswap V4 LP wallets on Robinhood Chain as JSON rows, ranked by score (0-100) within "
             "their class. Key fields: addr, score, verdict, kind, net (realized PnL 30d USD), roi, win, dd (max "
             "drawdown %), trades, duration (median hold, seconds), copy, style, worth (net worth), roi_nw, "
             "label, starred, slf (best Smart LP Finder placing).",
             {"kind": {"type": "string", "enum": ["multiday", "degen"],
                       "description": "one player class; leave out for both mixed"},
              "n": N, "real": {**B, "description": "Real PnL: minus the cost of selling the returned tokens"},
              "last_24h": {**B, "description": "only positions closed in the last 24 hours"}}, rh_top),
        Tool("rh_wallet", "One wallet from the database: metrics, label/notes, Smart LP Finder placings, latest "
             "positions (pair, range, deposit/withdraw/fee/now USD, PnL). No external calls.",
             {"wallet": WALLET, "positions": N, "real": B}, rh_wallet, required=["wallet"]),
        Tool("rh_lpagent", "The same wallet checked against LP Agent's own API (needs LPAGENT_API_KEY) to "
             "cross-check our figures.", {"wallet": WALLET}, rh_lpagent, required=["wallet"]),
        Tool("rh_twin", "Wallets whose LP style (hold time, range width, size, ladders, pace) is closest to this "
             "one, with similarity 0-1.", {"wallet": WALLET, "n": N}, rh_twin, required=["wallet"]),
        Tool("rh_pools", "Best fee pools: from priced positions closed in the last `days`, per pool: fee_apr (fees "
             "÷ capital-days × 365, %), net_apr (same with PnL incl. price moves/IL), fee_per_1k ($ fee per $1,000 per "
             "day), fees, pnl, positions, wallets, win %, earning (% of positions that earned any fee), hold_h "
             "(median), range_pct (median range width), top_share (% of fees one wallet took), mcap, fee_tier, and "
             "the top 3 wallets there with our score. High fee_apr with low net_apr = fees eaten by IL.",
             {"days": N, "sort": {"type": "string", "enum": ["fee_apr", "net_apr", "fees", "pnl", "wallets"]},
              "n": N, "min_positions": N, "min_wallets": N}, rh_pools),
        Tool("rh_compare", "Compare rows the user copied from RH Discovery with our figures (✓ within tolerance, "
             "✗ off, and why a wallet isn't ranked).", {"rows": {"type": "string", "description": "pasted table"}},
             rh_compare, required=["rows"]),
        Tool("rh_sql", "Read-only SELECT on the Wallet Finder SQLite DB, for questions the other tools don't "
             "answer. Tables: wallets(addr, score, data JSON, eth_bal, usd_bal, first_seen, last_seen), "
             "positions(token_id, owner, pool, tl, tu, open_ts, close_ts, dep_usd, wd_usd, fee_usd, now_usd, "
             "pnl_usd, …), events, transfers, pools(id, c0, c1, fee), tokens(addr, symbol), labels(addr, label, "
             "notes, starred, source, ref), slf_ranks(addr, pool, rank, total, pnl_usd, pnl_pct, deposited_usd, "
             "positions, computed_at), meta(k, v). Times are unix seconds.",
             {"sql": {"type": "string"}, "limit": N}, rh_sql, required=["sql"]),
        Tool("rh_sync", "Load more chain history (eth_getLogs) for a few segments. Spends RPC quota; the app's "
             "background worker normally does this on its own.", {"segments": N}, rh_sync, risk="write"),
        Tool("rh_score", "Price queued positions (archive eth_call) and re-rank. Spends RPC quota, takes minutes.",
             {}, rh_score, risk="write"),
        Tool("rh_import", "Star wallets into the Watchlist: one per line, text after the address becomes the label.",
             {"text": {"type": "string"}}, rh_import, risk="write", required=["text"]),

        Tool("slf_summary", "RH Smart LP Finder as seen from the Wallet Finder: whether its folder/DB is found, its "
             "server link, pools it scanned (pair, wallets ranked, computed_at), saved wallets, and the best "
             "placings (rank/total in a pool, pnl_usd) next to our own score (our_score, null = not ranked by us).",
             {"n": N}, slf_summary),
        Tool("slf_import", "Pull Smart LP Finder's saved wallets and pool rankings in now (normally every sync).",
             {}, slf_import, risk="write"),

        Tool("web_check", "Web showcase health: the snapshot the Vercel site serves now (generated, age_min, "
             "wallets, status) next to the last publish from this machine (or its error).", {}, web_check),
        Tool("web_publish", "Upload the board snapshot to the web showcase now (one Blob write).", {}, web_publish,
             risk="write"),
    ]
