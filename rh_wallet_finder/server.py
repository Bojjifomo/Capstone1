"""Local dashboard server: http://127.0.0.1:4664

Listens on 127.0.0.1 only. Anything that changes state (settings, labels,
imports, sync) needs the random token the page gets when it loads, so another
website can't post to it.
"""
import json
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config
import db
import lpagent
import metrics as M
from db import get_meta

STATIC = Path(__file__).resolve().parent / "static"
TOKEN = secrets.token_hex(16)
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")
VIEWS = ("raw", "real", "d1raw", "d1real")
SETTING_KEYS = ("RPC_URL", "CALL_RPC_URL", "LPAGENT_API_KEY", "BACKFILL_DAYS", "METRIC_DAYS", "SELL_SLIPPAGE_PCT",
                "MIN_CLOSED", "SCORE_POSITIONS_PER_RUN", "RPC_WORKERS")
_cache = {}
_cache_lock = threading.Lock()
worker = None  # set by serve()


def wallet_rows(con, view):
    """Every scored wallet for one view, plus labels and nearest-label similarity."""
    scored_at = get_meta(con, "scored_at")
    labels_v = con.execute("SELECT COUNT(*), MAX(added) FROM labels").fetchone()
    key = (view, scored_at, tuple(labels_v))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    eth_usd = get_meta(con, "eth_usd") or 0
    labels = {r["addr"]: dict(r) for r in con.execute("SELECT * FROM labels")}
    rows, raw_metrics = [], []
    for r in con.execute("SELECT addr, data, eth_bal, usd_bal, first_seen, last_seen FROM wallets "
                         "WHERE data IS NOT NULL"):
        d = json.loads(r["data"])
        m = d.get(view)
        if not m:
            continue
        lab = labels.get(r["addr"], {})
        worth = (r["eth_bal"] or 0) * eth_usd + (r["usd_bal"] or 0) + (m.get("open_now") or 0)
        rows.append({**m, "addr": r["addr"], "worth": worth, "first_seen": r["first_seen"],
                     "last_seen": r["last_seen"], "label": lab.get("label"), "notes": lab.get("notes"),
                     "starred": bool(lab.get("starred"))})
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
        "settings": {k: (cfg[k] if k not in ("RPC_URL", "CALL_RPC_URL", "LPAGENT_API_KEY") else "")
                     for k in SETTING_KEYS},
        "worker": None if worker is None else {
            "state": worker.state, "error": worker.error, "progress": worker.progress,
            "log": list(worker.logs)[-60:]},
        "now": int(time.time()),
    }


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
    return {"addr": addr, "positions": pos, "wallet": dict(w) if w else None,
            "metrics": json.loads(w["data"]) if w and w["data"] else None,
            "label": dict(lab) if lab else None, "lpagent_link": lpagent.link(addr),
            "explorer": f"{config.EXPLORER}/address/{addr}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "RHWalletFinder/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, name):
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            return self._send(404, {"error": "not found"})
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}.get(f.suffix, "text/plain")
        body = f.read_bytes()
        if f.suffix == ".html":
            body = body.replace(b"{{TOKEN}}", TOKEN.encode())
        self._send(200, body, ctype)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ("/", "/index.html"):
            return self._static("index.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        con = db.connect()
        try:
            if u.path == "/api/status":
                return self._send(200, status(con))
            if u.path == "/api/wallets":
                view = q.get("view", "raw")
                if view not in VIEWS:
                    return self._send(400, {"error": "bad view"})
                rows, _ = wallet_rows(con, view)
                if view.startswith("d1"):
                    rows = rows[:100]
                return self._send(200, {"rows": rows})
            if u.path == "/api/similar":
                return self._send(200, similar(con, q.get("addr", ""), q.get("view", "raw")))
            m = re.fullmatch(r"/api/wallet/(0x[0-9a-fA-F]{40})", u.path)
            if m:
                return self._send(200, wallet_detail(con, m.group(1).lower()))
            m = re.fullmatch(r"/api/lpagent/(0x[0-9a-fA-F]{40})", u.path)
            if m:
                key = config.load_env()["LPAGENT_API_KEY"]
                try:
                    return self._send(200, lpagent.check(con, m.group(1), key, q.get("fresh") == "1"))
                except lpagent.LPAgentError as e:
                    return self._send(200, {"link": lpagent.link(m.group(1)), "has_key": True,
                                            "errors": [str(e)]})
            if u.path == "/api/labels":
                return self._send(200, {"rows": [dict(r) for r in con.execute(
                    "SELECT * FROM labels ORDER BY added DESC")]})
            return self._send(404, {"error": "not found"})
        finally:
            con.close()

    def do_POST(self):  # noqa: N802
        if self.headers.get("x-token") != TOKEN:
            return self._send(403, {"error": "bad token, reload the page"})
        n = int(self.headers.get("content-length") or 0)
        if n > 2_000_000:
            return self._send(413, {"error": "too big"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        path = urlparse(self.path).path
        con = db.connect()
        try:
            if path == "/api/label":
                addr = (body.get("addr") or "").lower()
                if not ADDR_RE.fullmatch(addr):
                    return self._send(400, {"error": "bad address"})
                if body.get("delete"):
                    con.execute("DELETE FROM labels WHERE addr=?", (addr,))
                else:
                    con.execute("""INSERT INTO labels(addr, label, notes, starred, source, added)
                                   VALUES(?,?,?,?,?,?) ON CONFLICT(addr) DO UPDATE SET
                                   label=COALESCE(excluded.label, labels.label),
                                   notes=COALESCE(excluded.notes, labels.notes),
                                   starred=excluded.starred, added=excluded.added""",
                                (addr, body.get("label"), body.get("notes"), 1 if body.get("starred") else 0,
                                 body.get("source") or "manual", int(time.time())))
                con.commit()
                return self._send(200, {"ok": True})
            if path == "/api/import":
                return self._send(200, import_text(con, body.get("text") or "", body.get("source") or "import"))
            if path == "/api/settings":
                upd = {k: str(v).strip() for k, v in body.items() if k in SETTING_KEYS and str(v).strip()}
                for k in ("RPC_URL", "CALL_RPC_URL"):
                    if k in upd and not upd[k].startswith(("https://", "http://")):
                        return self._send(400, {"error": "RPC URLs must start with https://"})
                config.save_env(upd)
                if worker:
                    worker.kick()
                return self._send(200, {"ok": True, "saved": sorted(upd)})
            if path == "/api/sync":
                if worker:
                    worker.kick()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        finally:
            con.close()


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


def serve(port=None, open_browser=True, run_worker=True):
    global worker
    cfg = config.load_env()
    port = int(port or cfg["PORT"])
    if run_worker:
        from worker import Worker
        worker = Worker()
        worker.start()
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"No free port between {port} and {port + 19}")
    url = f"http://127.0.0.1:{port}/"
    print(f"RH Wallet Finder → {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if worker:
            worker.stop()
