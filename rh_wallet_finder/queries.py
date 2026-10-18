"""Read side of the wallet database: what the Streamlit app and the CLI show.

Everything here takes an open connection (db.connect()) and returns plain
dicts / lists.
"""
import json
import re
import threading
import time

import config
import lpagent
import metrics as M
import smartlp
from db import get_meta, set_meta

ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")
VIEWS = ("raw", "real", "d1raw", "d1real")
SETTING_KEYS = ("RPC_URL", "CALL_RPC_URL", "LPAGENT_API_KEY", "BACKFILL_DAYS", "METRIC_DAYS", "SELL_SLIPPAGE_PCT",
                "MIN_CLOSED", "SCORE_POSITIONS_PER_RUN", "RPC_WORKERS", "SYNC_MINUTES")
SECRET_KEYS = ("RPC_URL", "CALL_RPC_URL", "LPAGENT_API_KEY")
_cache = {}
_cache_lock = threading.Lock()


def wallet_rows(con, view):
    """Every scored wallet for one view, plus labels and nearest-label similarity."""
    scored_at = get_meta(con, "scored_at")
    labels_v = con.execute("SELECT COUNT(*), MAX(added) FROM labels").fetchone()
    slf_v = con.execute("SELECT COUNT(*), MAX(computed_at) FROM slf_ranks").fetchone()
    key = (view, scored_at, tuple(labels_v), tuple(slf_v))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    eth_usd = get_meta(con, "eth_usd") or 0
    labels = {r["addr"]: dict(r) for r in con.execute("SELECT * FROM labels")}
    slf = smartlp.best_ranks(con)
    names = pool_names(con, {v["pool"] for v in slf.values()})
    rows, raw_metrics = [], []
    for r in con.execute("SELECT addr, data, eth_bal, usd_bal, first_seen, last_seen FROM wallets "
                         "WHERE data IS NOT NULL"):
        d = json.loads(r["data"])
        m = d.get(view)
        if not m:
            continue
        lab = labels.get(r["addr"], {})
        worth = (r["eth_bal"] or 0) * eth_usd + (r["usd_bal"] or 0) + (m.get("open_now") or 0)
        best = slf.get(r["addr"])
        rows.append({**m, "addr": r["addr"], "worth": worth, "first_seen": r["first_seen"],
                     "last_seen": r["last_seen"], "label": lab.get("label"), "notes": lab.get("notes"),
                     "starred": bool(lab.get("starred")),
                     "slf": {**best, "pair": names.get(best["pool"])} if best else None})
        raw_metrics.append(d.get("raw") or m)
    # PRED-SIM: closest starred/labelled wallet
    feats = M.feature_matrix(raw_metrics)
    anchors = [(i, rows[i]["label"] or rows[i]["addr"][:8]) for i in range(len(rows))
               if rows[i]["starred"] or rows[i]["label"]]
    for i, row in enumerate(rows):
        best = None
        for j, name in anchors:
            if j == i:
                continue
            s = M.similarity(feats[i], feats[j])
            if best is None or s > best[1]:
                best = (name, s)
        row["sim_label"], row["sim_pct"] = best if best else (None, None)
    rows.sort(key=lambda x: -x["score"])
    for n, row in enumerate(rows, 1):
        row["rank"] = n
    with _cache_lock:
        _cache.clear()
        _cache[key] = (rows, feats)
    return rows, feats


def status(con):
    cfg = config.load_env()
    n = lambda q: con.execute(q).fetchone()[0]  # noqa: E731
    data_from = get_meta(con, "data_from_ts")
    styles = set()
    for r in con.execute("SELECT data FROM wallets WHERE data IS NOT NULL"):
        st = (json.loads(r[0]).get("raw") or {}).get("style")
        if st:
            styles.add(st)
    return {
        "discovered": n("SELECT COUNT(*) FROM wallets"),
        "scored": n("SELECT COUNT(*) FROM wallets WHERE score IS NOT NULL"),
        "categories": len(styles),
        "positions": n("SELECT COUNT(*) FROM positions"),
        "pools": n("SELECT COUNT(*) FROM pools"),
        "synced_at": get_meta(con, "synced_at"), "scored_at": get_meta(con, "scored_at"),
        "data_from": data_from, "metric_days": float(cfg["METRIC_DAYS"]),
        "backfill_days": float(cfg["BACKFILL_DAYS"]),
        "eth_usd": get_meta(con, "eth_usd"), "sell_fee": get_meta(con, "avg_sell_fee"),
        "slippage": float(cfg["SELL_SLIPPAGE_PCT"]),
        "rpc": config.mask(cfg["RPC_URL"]), "has_rpc": bool(cfg["RPC_URL"]),
        "call_rpc": config.mask(cfg["CALL_RPC_URL"]), "rpc_source": cfg["RPC_SOURCE"],
        "has_lpagent_key": bool(cfg["LPAGENT_API_KEY"]),
        "smart_lp": {"url": smartlp.server_url(), "db": bool(smartlp.db_path()),
                     "saved": n("SELECT COUNT(*) FROM labels WHERE source='smart-lp-finder'"),
                     "ranked": n("SELECT COUNT(DISTINCT addr) FROM slf_ranks")},
        "settings": {k: (cfg[k] if k not in ("RPC_URL", "CALL_RPC_URL", "LPAGENT_API_KEY") else "")
                     for k in SETTING_KEYS},
        "now": int(time.time()),
    }


def pool_names(con, pool_ids):
    """{pool id: 'TOKEN/USDG'} for pools we have indexed."""
    ids = list(pool_ids)
    if not ids:
        return {}
    tok = {r[0]: r[1] for r in con.execute("SELECT addr, symbol FROM tokens")}
    out = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in con.execute(f"SELECT id, c0, c1 FROM pools WHERE id IN ({','.join('?' * len(part))})", part):
            out[r[0]] = f"{tok.get(r[1]) or r[1][:6]}/{tok.get(r[2]) or r[2][:6]}"
    return out


def wallet_detail(con, addr):
    pos = []
    tok = {r["addr"]: dict(r) for r in con.execute("SELECT addr, symbol FROM tokens")}
    pools = {r["id"]: dict(r) for r in con.execute("SELECT id, c0, c1, fee FROM pools")}
    for r in con.execute("""SELECT token_id, pool, tl, tu, open_ts, close_ts, minted, valued, dep_usd, wd_usd,
                            fee_usd, now_usd, pnl_usd, cost_usd, single, eth_pair, launch FROM positions
                            WHERE owner=? ORDER BY open_ts DESC LIMIT 300""", (addr,)):
        p = dict(r)
        pl = pools.get(p["pool"]) or {}
        p["pair"] = "/".join((tok.get(pl.get(c)) or {}).get("symbol") or "?" for c in ("c0", "c1"))
        p["fee_tier"] = pl.get("fee")
        p["range"] = M.range_pct(p)
        pos.append(p)
    w = con.execute("SELECT * FROM wallets WHERE addr=?", (addr,)).fetchone()
    lab = con.execute("SELECT * FROM labels WHERE addr=?", (addr,)).fetchone()
    slf = [dict(r) for r in con.execute("SELECT * FROM slf_ranks WHERE addr=? ORDER BY rank, pnl_usd DESC",
                                        (addr,))]
    names = pool_names(con, {r["pool"] for r in slf})
    for r in slf:
        r["pair"] = names.get(r["pool"])
    return {"addr": addr, "positions": pos, "wallet": dict(w) if w else None, "smart_lp": slf,
            "metrics": json.loads(w["data"]) if w and w["data"] else None,
            "label": dict(lab) if lab else None, "lpagent_link": lpagent.link(addr),
            "explorer": f"{config.EXPLORER}/address/{addr}"}


def import_text(con, text, source):
    """Lines like '0xabc…', '0xabc…,label' or a pasted LP Agent table: every
    address found is saved; text after it on the same line becomes the label."""
    added = 0
    now = int(time.time())
    for line in text.splitlines():
        m = ADDR_RE.search(line)
        if not m:
            continue
        rest = line[m.end():]
        rest = re.sub(r"https?://\S+", "", rest)
        rest = re.sub(r"0x[0-9a-fA-F.…]+", "", rest)
        label = re.sub(r"\b(ROBINHOOD|SOL)\b", "", rest).strip(" ,;\t|-") or None
        con.execute("""INSERT INTO labels(addr, label, starred, source, added) VALUES(?,?,1,?,?)
                       ON CONFLICT(addr) DO UPDATE SET label=COALESCE(excluded.label, labels.label),
                       starred=1""", (m.group(0).lower(), label[:60] if label else None, source, now))
        added += 1
    con.commit()
    return {"ok": True, "added": added}


def similar(con, addr, view):
    addr = addr.lower()
    if not ADDR_RE.fullmatch(addr):
        return {"error": "Paste a wallet address (0x + 40 hex)."}
    rows, feats = wallet_rows(con, view if view in VIEWS else "raw")
    idx = next((i for i, r in enumerate(rows) if r["addr"] == addr), None)
    if idx is None:
        return {"error": "That wallet isn't scored yet (it needs a few closed, priced positions). "
                         "Star it so it gets priced first on the next run."}
    out = []
    for i, r in enumerate(rows):
        if i != idx:
            out.append({**r, "sim": M.similarity(feats[idx], feats[i])})
    out.sort(key=lambda r: -r["sim"])
    return {"target": rows[idx], "rows": out[:100]}


def presets(con):
    """Filter presets saved from the app: [{name, filters, sort}]."""
    return get_meta(con, "presets") or []


def save_presets(con, items):
    set_meta(con, "presets", items)
    con.commit()


def set_label(con, addr, label=None, notes=None, starred=None, source="manual"):
    """Upsert one watchlist row; None leaves a field as it is."""
    addr = addr.lower()
    if not ADDR_RE.fullmatch(addr):
        raise ValueError("bad address")
    con.execute("""INSERT INTO labels(addr, label, notes, starred, source, added) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(addr) DO UPDATE SET label=COALESCE(excluded.label, labels.label),
                   notes=COALESCE(excluded.notes, labels.notes),
                   starred=COALESCE(?, labels.starred)""",
                (addr, label, notes, 1 if starred else 0, source, int(time.time()),
                 None if starred is None else (1 if starred else 0)))
    con.commit()


def delete_label(con, addr):
    con.execute("DELETE FROM labels WHERE addr=?", (addr.lower(),))
    con.commit()
