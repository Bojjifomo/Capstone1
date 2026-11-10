"""Elysium Terminal, web version: Uniswap v4 and v3 pools on Elysium with the entry check, PING,
watchlist, ladder zap, tight ranges, and your wallet's positions (one row per open) with close,
claim, re-entry, cleanup and per-position auto rules (TP, SL, rebalance or close out of range).

  python3 -m elysium_terminal web                  # http://localhost:8893 (ELYSIUM_WEB_PORT in .env)
  python3 -m elysium_terminal web --host 0.0.0.0   # reachable over Tailscale (phone, MacBook)

Trading: the key in elysium_terminal/.env (ELYSIUM_PRIVATE_KEY) stays in the Node executor;
the browser only sees the address. ELYSIUM_PRIVATE_KEY is never read. Who may trade:
  * without ELYSIUM_WEB_PIN: only a browser on this Mac (127.0.0.1)
  * with ELYSIUM_WEB_PIN: any device that reaches the page, with the PIN
Trade requests also need the X-ELY-Trade header, which another website open in your browser
cannot add without a CORS preflight this server never answers.
ELYSIUM_PRACTICE_MODE defaults to true: every button only simulates (eth_call), nothing is sent.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import auto, chain, depth, filters, groups, node, pools, portfolio, settings, tight
from .cli import DATA, append, env_report, load_config, load_env, make_index, notify, practice_mode

STATIC = Path(__file__).resolve().parent / "web"
POOL_TTL = 30
PF_TTL = 25
LAST_CLIENT = 0.0

_lock = threading.Lock()
_cache: dict = {}
STATE: dict = {"ix": None, "book": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- json files ----
def _jload(name: str, default):
    try:
        return json.loads((DATA / name).read_text())
    except (OSError, ValueError):
        return default


def _jsave(name: str, obj) -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, indent=1))


PREF_KEYS = {"unit", "mode", "win", "sort", "tvl", "filters", "view", "wallet"}


def prefs_set(changes: dict) -> dict:
    p = _jload("ui_prefs.json", {})
    p.update({k: v for k, v in changes.items() if k in PREF_KEYS and len(json.dumps(v)) < 200})
    _jsave("ui_prefs.json", p)
    return p


def watchlist() -> list[dict]:
    return _jload("watchlist.json", [])


def watch_set(pool: str, symbol: str, on: bool) -> list[dict]:
    cur = [w for w in watchlist() if w.get("pool") != pool]
    if on:
        cur.append({"pool": pool, "symbol": symbol, "addedAt": time.time()})
    _jsave("watchlist.json", cur)
    with _lock:
        _cache.pop("pools", None)
    return cur


# ------------------------------------------------------------------ pools ----
_build = threading.Lock()


def pool_rows(force: bool = False) -> dict:
    with _lock:
        hit = _cache.get("pools")
        if hit and not force and time.time() - hit["t"] < POOL_TTL:
            return hit["data"]
    with _build:                     # one build at a time; whoever waited gets the fresh one
        with _lock:
            hit = _cache.get("pools")
            if hit and time.time() - hit["t"] < 3:
                return hit["data"]
        data = STATE["book"].rows(watch=[w["pool"] for w in watchlist()])
        with _lock:
            _cache["pools"] = {"t": time.time(), "data": data}
    _ping_notify(data)
    return data


_ping_sent: dict = {}


def _ping_notify(data: dict) -> None:
    """Telegram (if set) when a pool newly enters PING; once per pool per 6 hours."""
    rows = {r["pool"]: r for r in data["rows"]}
    for p in data["ping"].get("new") or []:
        if time.time() - _ping_sent.get(p, 0) < 6 * 3600 or p not in rows:
            continue
        _ping_sent[p] = time.time()
        r = rows[p]
        notify(f"📡 PING baru: {r['symbol']}/{r['quoteSymbol']} fee {r['lpFeePct']}% · TVL ${r['tvlUsd'] or 0:,.0f} · "
               f"fee 1 jam ${r['fees'].get('1h') or 0:,.0f} ({(r['feePctDay'].get('1h') or 0):.1f}%/hari)\n"
               f"https://dexscreener.com/elysium/{p}")


# -------------------------------------------------------------- portfolio ----
_pf_build = threading.Lock()


def portfolio_of(wallet: str, force: bool = False) -> dict:
    key = ("pf", wallet.lower())
    with _lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit["t"] < PF_TTL:
            return hit["data"]
    with _pf_build:                  # two tabs (or a tab and the auto loop) share one chain read
        with _lock:
            hit = _cache.get(key)
            if hit and time.time() - hit["t"] < 3:
                return hit["data"]
        cfg = Handler.cfg
        pf = portfolio.portfolio(wallet, cfg, DATA, STATE["book"])
        with _lock:
            _cache[key] = {"t": time.time(), "data": pf}
    _seen_tokens([r["token"] for r in pf["rows"]], [r["pool"] for r in pf["rows"]])
    return pf


def _pf_view(pf: dict) -> dict:
    """The portfolio plus the auto rules, which change without a chain read."""
    store = _auto_store()
    tw = node.trade_wallet() if node.key_configured() else {}
    if tw.get("ok") and tw.get("address", "").lower() == pf.get("wallet") and not pf.get("partial"):
        store.adopt_legacy(pf["rows"], time.time())
    return {**pf, "auto": store.view(), "autoCheckSec": _auto_check_sec(), "practice": practice_mode()}


def _drop_pf_cache() -> None:
    with _lock:
        for k in [k for k in _cache if isinstance(k, tuple)]:
            _cache.pop(k, None)


def _seen_tokens(tokens: list[str], pool_ids: list[str]) -> None:
    """Remember every token a position held (and its pool), so Cleanup can find leftovers later."""
    d = _jload("tokens_seen.json", {})
    n = len(d)
    for t, p in zip(tokens, pool_ids):
        d.setdefault(t, p)
    if len(d) != n:
        _jsave("tokens_seen.json", d)


# ------------------------------------------------------------------ depth ----
DEPTH_TTL = 20
_spacing_v3: dict[str, int] = {}


def depth_of(wallet: str, gid: str) -> dict:
    """Liquidity depth of the pool around one position, plus that position's share of it."""
    row = next((r for r in portfolio_of(wallet)["rows"] if r["id"] == gid), None)
    if row is None:
        raise ValueError("posisi tidak ketemu (refresh portfolio)")
    pool, lo, hi = row["pool"], row["tickLower"], row["tickUpper"]
    if hi - lo > portfolio.LADDER_MAX_TICKS:
        raise ValueError("range posisi ini terlalu lebar buat digambar (full range)")
    key = ("depth", pool, lo, hi)
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit["t"] < DEPTH_TTL:
        d = hit["data"]
    else:
        if row["dex"] == "v4":
            spacing = int(row["key"].get("tickSpacing") or 0)
            tick = (chain.slot0s_v4([pool]).get(pool) or {}).get("tick")
        else:
            if pool not in _spacing_v3:
                _spacing_v3[pool] = chain.to_int(chain.words(chain.eth_call(pool, "0x" + chain.SEL["tickSpacing"]))[0], 24, True)
            spacing = _spacing_v3[pool]
            tick = (chain.slot0s_v3([pool]).get(pool) or {}).get("tick")
        if tick is None:
            raise chain.RpcError("harga pool gagal dibaca")
        d = depth.read(pool, row["dex"], spacing, tick, lo, hi)
        d["t"] = time.time()
        with _lock:
            _cache[key] = {"t": d["t"], "data": d}
    mine = sum(int(p["liquidity"]) for p in row["positions"] if p["tickLower"] <= d["tick"] < p["tickUpper"])
    return {**d, "id": gid, "mine": mine, "sharePct": mine / d["active"] * 100 if d["active"] else None,
            "ranges": [[p["tickLower"], p["tickUpper"]] for p in row["positions"]]}


# ------------------------------------------------------------- executor ----
# One wallet, one nonce sequence: a manual click and the auto loop never sign at the same time.
_trade_lock = threading.Lock()


def _key_args(pool: str) -> list[str]:
    """Hand the executor a v4 pool key we already have, so it does not look it up again."""
    pid = str(pool or "").lower()
    if len(pid) != 66:
        return []
    book, ix = STATE.get("book"), STATE.get("ix")
    key = (ix.keys.get(pid) if ix is not None else None) or (book.extra.get(pid) if book is not None else None)
    if not key or key.get("dex") == "v3":
        return []
    try:
        return ["--key", json.dumps({k: key[k] for k in ("currency0", "currency1", "fee", "tickSpacing", "hooks")})]
    except KeyError:
        return []


def run_trade(script: str, args: list[str], wallet: str, timeout: int = 600, pool: str | None = None) -> dict:
    args = list(args) + (_key_args(pool) if pool else [])
    if practice_mode():
        args += ["--dry-run", "--owner", wallet]
    with _trade_lock:
        res = node.run(script, args, timeout=timeout)
    res["practice"] = practice_mode()
    _drop_pf_cache()
    return res


def _register(res: dict, kind: str, ids=None, **meta) -> str | None:
    """Remember what a successful, real open minted, so it shows as its own row."""
    ids = ids if ids is not None else res.get("positions")
    if not res.get("ok") or res.get("dryRun") or not ids:
        return None
    venue = res.get("venue")
    manager = res.get("manager")
    if not venue or not manager:
        return None
    try:
        return groups.register(DATA, venue, manager, ids, kind, **meta)
    except (OSError, ValueError) as exc:
        print(f"[groups] {exc}", file=sys.stderr, flush=True)
        return None


# ------------------------------------------------------------------ auto ----
# data/auto.json: one rule per position (see auto.py). While this process runs, a thread
# reads the portfolio every web.autoExitCheckMin minutes (default 1) and acts on armed rules. Nothing runs in the browser.
AUTO_CHECK_SEC = 60


def _auto_check_sec() -> int:
    try:
        return int(max(1, min(60, float(Handler.cfg["web"].get("autoExitCheckMin", 1)))) * 60)
    except (KeyError, TypeError, ValueError):
        return AUTO_CHECK_SEC


def _auto_store() -> auto.Store:
    return auto.Store(DATA)


def _find_row(wallet: str, gid: str, force: bool = False) -> dict | None:
    pf = portfolio_of(wallet, force=force)
    return next((r for r in pf["rows"] if r["id"] == gid), None)


def _ids(row: dict) -> str:
    return ",".join(str(p["tokenId"]) for p in row["positions"])


def do_exit(row: dict, wallet: str, cfg: dict) -> dict:
    """Close every NFT of the position and sell the token back to the quote."""
    args = ["--pool", row["pool"], "--ids", _ids(row), "--maxloss", str(cfg["trade"]["maxLossPct"])]
    return run_trade("reenter.mjs", args, wallet, 900, pool=row["pool"])


def do_rebalance(row: dict, wallet: str, width_pct: float, slippage_bps: int) -> dict:
    """Close, swap to the right mix, open a new range around the current price. Keeps the PnL basis."""
    if row.get("posCount", 1) != 1:
        return {"ok": False, "error": "rebalance cuma buat posisi satu range. Ladder: pakai Re-entry."}
    args = ["--mode", "rebalance", "--pool", row["pool"], "--ids", _ids(row),
            "--width", f"{float(width_pct):g}", "--slippage-bps", str(int(slippage_bps))]
    res = run_trade("tight.mjs", args, wallet, 900, pool=row["pool"])
    if res.get("ok") and not res.get("dryRun"):
        basis = None
        if row.get("depositUsd") and row.get("quoteUsd"):
            basis = row["depositUsd"] / row["quoteUsd"]          # carried, so PnL spans the rebalances
        res["newId"] = _register(res, "tight", depositQuote=basis if basis is not None else res.get("depositQuote"),
                                 widthPct=float(width_pct), rebalances=int(row.get("rebalances") or 0) + 1,
                                 originAt=row.get("openedAt"))
    return res


def auto_once(cfg: dict, wallet: str) -> list[dict]:
    store = _auto_store()
    view = store.view()
    if view["global"]["paused"] or not any(r.get("status") == "armed" for r in view["rules"].values()):
        return []
    pf = portfolio_of(wallet, force=True)
    slip = view["global"]["slippageBps"]

    def execute(action, rule, row, decision):
        if action == "exit":
            res = do_exit(row, wallet, cfg)
        else:
            res = do_rebalance(row, wallet, decision["widthPct"], slip)
        append("closes.jsonl", {"ts": _now_iso(), "source": "auto", "id": row["id"], "pool": row["pool"],
                                "symbol": row["symbol"], "ids": _ids(row), "rule": f"{action}: {decision['reason']}",
                                "pnlPct": row.get("pnlPct"), "ok": res.get("ok"), "error": res.get("error"),
                                "practice": res.get("practice"), "newId": res.get("newId")})
        return res

    return auto.run_once(store, pf["rows"], bool(pf.get("partial")), time.time(), execute, notify)


def _auto_loop(cfg: dict) -> None:
    try:
        _auto_store().recover(time.time())
    except Exception as exc:
        print(f"[auto] {exc}", file=sys.stderr, flush=True)
    while True:
        time.sleep(_auto_check_sec())
        try:
            tw = node.trade_wallet() if node.key_configured() else {}
            if tw.get("ok"):
                auto_once(cfg, tw["address"])
        except Exception as exc:
            print(f"[auto] {exc}", file=sys.stderr, flush=True)


# ----------------------------------------------------------------- tight ----
def _tight_store() -> tight.Store:
    return tight.Store(DATA)


def _tight_view(width: str | None, target: str | None) -> dict:
    """Signals from the pool snapshot already in memory. Does not start a new chain poll."""
    store = _tight_store()
    cfg = store.config()
    screen = dict(cfg)
    if width not in (None, ""):
        screen["widthPct"] = tight.sanitize_config({"widthPct": width}, screen)["widthPct"]
    if target not in (None, ""):
        screen["targetPctPerHour"] = tight.sanitize_config({"targetPctPerHour": target}, screen)["targetPctPerHour"]
    with _lock:
        hit = _cache.get("pools")
    data = hit["data"] if hit else pool_rows(False)
    ix = STATE.get("ix")
    now = time.time()
    coverage = (data.get("index") or {}).get("coverageHours") or 0
    fee_source = "index" if coverage >= 1 else "perkiraan volume × fee, jendela index belum sejam"
    cards = []
    for r in data.get("rows") or []:
        if r.get("tick") is None:
            continue
        key = r.get("key") or {}
        points = ix.series(r["pool"]) if ix is not None else []
        cards.append(tight.screen_row({
            "pool": r["pool"], "symbol": r.get("symbol"), "quoteSymbol": r.get("quoteSymbol"),
            "dex": r.get("dex"), "tick": r.get("tick"), "tickSpacing": r.get("tickSpacing") or 0,
            "fees1h": (r.get("fees") or {}).get("1h"), "tvlUsd": r.get("tvlUsd"),
            "vol1h": (r.get("vol") or {}).get("1h"), "swaps1h": (r.get("swaps") or {}).get("1h"),
            "feePpm": r.get("lpFee"), "feeSource": fee_source, "feeWindowSec": 3600,
            "hooks": r.get("hooks"), "quoteIsToken0": key.get("currency0") == r.get("quote"),
            "notionalUsd": data.get("sizeUsd"), "dynamicFee": r.get("dynamicFee"),
        }, points, now, screen))
    prev = store.signals()
    nxt = {}
    for c in cards:
        st = tight.apply_hysteresis((prev.get(c["pool"]) if c.get("pool") else None),
                                    bool(c["verdict"]["rawAbove"] and c["supported"]),
                                    now, int(screen["enterPolls"]), int(screen["exitPolls"]))
        if c.get("pool"):
            nxt[c["pool"]] = st
        c["listed"] = bool(st.get("on"))
        c["passes"] = int(st.get("above") or 0)
    store.save_signals(nxt)
    # Everything a tight range can be opened on, best first. A pool that fails the filter is
    # still shown with its reasons; hiding it left the tab empty on a volatile day.
    rank = {"lolos filter": 0, "di bawah target": 1}
    tvl = {r["pool"]: r.get("tvlUsd") or 0 for r in data.get("rows") or []}
    for c in cards:
        c["tvlUsd"] = tvl.get(c["pool"])
    keep = [c for c in cards if c["supported"] and c["verdict"]["grossPctPerHour"] is not None
            and (c["listed"] or (c["tvlUsd"] or 0) >= 1000)]
    keep.sort(key=lambda c: (not c["listed"], rank.get(c["verdict"]["status"], 2), -(c["verdict"]["grossPctPerHour"] or -1)))
    age = None if not hit else max(0, now - hit["t"])
    astore = _auto_store()
    return {
        "t": now, "screen": {"widthPct": screen["widthPct"], "targetPctPerHour": screen["targetPctPerHour"]},
        "rows": keep[:40], "scanned": len(cards), "listed": sum(1 for c in cards if c["listed"]),
        "rejected": sum(1 for c in cards if c["verdict"]["status"] == "tidak cocok"),
        "feeSource": fee_source, "snapshotAgeSec": age, "auto": astore.view(), "log": astore.log_tail(12),
        "autoCheckSec": _auto_check_sec(), "practice": practice_mode(),
        "note": "Daftar ini tidak membuka posisi sendiri. %/jam adalah fee seluruh pool dibagi TVL, bukan fee posisi lo.",
    }


def _snapshot_row(pool: str) -> dict | None:
    with _lock:
        hit = _cache.get("pools")
    rows = (hit["data"].get("rows") if hit else None) or []
    return next((r for r in rows if r["pool"] == pool), None)


# ---------------------------------------------------------------- cleanup ----
def cleanup_list(wallet: str) -> list[dict]:
    """Tokens this wallet's ladders have held, with a balance now, each with its deepest USDC/HYPE pool."""
    seen = _jload("tokens_seen.json", {})
    toks = [t for t in seen if t not in (chain.USDC, chain.WHYPE, chain.ZERO)]
    if not toks:
        return []
    bals = chain.multicall([(t, chain.SEL["balanceOf"] + chain.addr_word(wallet)) for t in toks])
    book = STATE["book"]
    meta = book.tokens(toks)
    rows = (pool_rows().get("rows") or [])
    out = []
    for t, b in zip(toks, bals):
        raw = chain.to_int(chain.words(b)[0]) if b else 0
        if not raw:
            continue
        cands = [r for r in rows if r["token"] == t and r["tvlUsd"]]
        best = max(cands, key=lambda r: r["tvlUsd"]) if cands else None
        pool = best["pool"] if best else seen[t]
        px = best["priceUsd"] if best else None
        dec = (meta.get(t) or {}).get("decimals", 18)
        out.append({"token": t, "symbol": (meta.get(t) or {}).get("symbol"), "pool": pool,
                    "valueUsd": raw / 10 ** dec * px if px else 0})
    return out


# ---------------------------------------------------------------- handler ----
class Handler(BaseHTTPRequestHandler):
    cfg: dict = {}

    def log_message(self, fmt, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str).encode(), "application/json")

    def _is_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _trade_refused(self, body: dict) -> str | None:
        if self.headers.get("X-ELY-Trade") != "1" or "application/json" not in (self.headers.get("Content-Type") or ""):
            return "missing trade header"
        pin = os.environ.get("ELYSIUM_WEB_PIN")
        if pin:
            return None if hmac.compare_digest(str(body.get("pin") or ""), pin) else "PIN salah"
        return None if self._is_local() else "trading cuma dari Mac ini (set ELYSIUM_WEB_PIN di .env buat dari HP)"

    def reload_cfg(self) -> None:
        new = load_config()
        self.cfg.clear()
        self.cfg.update(new)
        with _lock:
            _cache.clear()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == "/api/heartbeat":
                global LAST_CLIENT
                LAST_CLIENT = time.time()
                ix = STATE.get("ix")
                if ix is not None:
                    ix.poll_sec = 30 if time.time() - LAST_CLIENT < 90 else 180
                return self._json({"ok": True})
            if u.path == "/api/tight":
                return self._json(_tight_view(q.get("width"), q.get("target")))
            if u.path == "/api/series":
                pid = (q.get("pool") or "").lower()
                if not pid:
                    return self._json({"error": "pool?"}, 400)
                return self._json({"pool": pid, "points": STATE["ix"].series(pid)})
            if u.path == "/api/pools":
                return self._json(pool_rows(q.get("force") == "1"))
            if u.path == "/api/search":
                return self._json({"rows": STATE["book"].search(q.get("q", ""))})
            if u.path == "/api/portfolio":
                wallet = (q.get("wallet") or "").strip()
                if not (wallet.startswith("0x") and len(wallet) == 42):
                    return self._json({"error": "wallet address 0x… (42 karakter)"}, 400)
                return self._json(_pf_view(portfolio_of(wallet, q.get("force") == "1")))
            if u.path == "/api/depth":
                try:
                    return self._json(depth_of((q.get("wallet") or "").strip(), q.get("id") or ""))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
            if u.path == "/api/prefs":
                return self._json(_jload("ui_prefs.json", {}))
            if u.path == "/api/watchlist":
                return self._json({"rows": watchlist()})
            if u.path == "/api/settings":
                return self._json({"form": settings.form(self.cfg, load_config(overlay=False)), "overlay": settings.load()})
            if u.path == "/api/config":
                load_env(reload=True)                       # a key pasted into .env works without a restart
                tw = node.trade_wallet() if not node.ready() else {"ok": False}
                return self._json({"cfg": self.cfg, "executor": node.ready(), "index": STATE["book"].index_status(),
                                   "maxPositionUsd": filters.max_position_usd(self.cfg), "rpc": chain.STATS,
                                   "trade": {"enabled": bool(tw.get("ok")), "wallet": tw.get("address"),
                                             "error": None if tw.get("ok") or not node.key_configured() else tw.get("error"),
                                             "pin": bool(os.environ.get("ELYSIUM_WEB_PIN")), "local": self._is_local(),
                                             "practice": practice_mode(),
                                             "envCheck": None if tw.get("ok") or not self._is_local() else env_report()},
                                   "wallet": os.environ.get("ELYSIUM_WALLET")})
        except Exception as exc:
            print(f"[{datetime.now():%H:%M:%S}] {u.path} failed: {exc}", file=sys.stderr, flush=True)
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                print("  → SSL: jalanin `python3 -m pip install certifi`, lalu start ulang.", file=sys.stderr)
            else:
                traceback.print_exc()
            return self._json({"error": str(exc)[:300]}, 502)
        name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml", ".png": "image/png"}.get(f.suffix, "application/octet-stream")
        self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._json({"ok": False, "error": "bad json"}, 400)
        cfg = self.cfg
        try:
            if self.path == "/api/tight/plan":
                pool = str(body.get("pool") or "").lower()
                row = _snapshot_row(pool)
                if not row or row.get("tick") is None:
                    return self._json({"ok": False, "error": "pool ini belum ada di snapshot (buka tab Pool dulu), atau tick-nya kosong"}, 400)
                key = row.get("key") or {}
                fee, tvl = (row.get("fees") or {}).get("1h"), row.get("tvlUsd")
                gross = (fee / tvl * 100) if fee is not None and tvl else None
                rev = tight.review_open(
                    tick=int(row["tick"]), spacing=int(row.get("tickSpacing") or 0),
                    width_pct=float(body.get("widthPct") or 1), usd=float(body.get("usd") or 0),
                    quote_is_token0=key.get("currency0") == row.get("quote"),
                    fee_ppm=row.get("lpFee"), slippage_bps=int(body.get("slippageBps") or 100),
                    gross_pct_per_hour=gross)
                rev.update(pool=pool, symbol=row.get("symbol"), quoteSymbol=row.get("quoteSymbol"),
                           quoteUsd=row.get("quoteUsd"), tvlUsd=row.get("tvlUsd"))
                hooks = str(row.get("hooks") or chain.ZERO).lower()
                if rev.get("ok") and not ((row.get("dex") in ("v3", "v4")) and hooks == chain.ZERO and not row.get("dynamicFee")):
                    rev.update(ok=False, unsupported=True, error="pool ber-hook atau fee dinamis: tight range tidak didukung")
                return self._json(rev)
            if self.path == "/api/prefs":
                if self.headers.get("X-ELY-Trade") != "1":
                    return self._json({"ok": False, "error": "missing header"}, 403)
                return self._json(prefs_set(body))
            if self.path == "/api/ladder/plan":
                pos = cfg["position"]
                args = node.ladder_args(str(body["pool"]), float(body["usd"]), int(body.get("slices") or pos["ladderSlices"]),
                                        float(body.get("top") if body.get("top") is not None else pos["rangeTopPct"]),
                                        float(body.get("bottom") or pos["rangeBottomPct"]), STATE["ix"].eth_usd())
                return self._json(node.run("open_ladder.mjs", args + ["--plan"] + _key_args(str(body["pool"])), timeout=90))
            if self.path == "/api/decide":
                row = {"ts": _now_iso(), "source": "web", **{k: body.get(k) for k in
                       ("pool", "symbol", "decision", "decisionReason", "sizeUsd", "level", "gatesFailed", "flags")}}
                append("decisions.jsonl", row)
                return self._json({"ok": True})
            why = self._trade_refused(body)
            if why:
                return self._json({"ok": False, "error": why}, 403)
            if self.path == "/api/watchlist":
                return self._json({"ok": True, "rows": watch_set(str(body["pool"]), str(body.get("symbol") or ""), bool(body.get("on")))})
            if self.path == "/api/settings":
                if body.get("reset"):
                    settings.reset()
                else:
                    settings.save(load_config(overlay=False), body.get("changes") or {})
                self.reload_cfg()
                return self._json({"ok": True, "form": settings.form(self.cfg, load_config(overlay=False))})
            tw = node.trade_wallet()
            if not tw.get("ok"):
                return self._json({"ok": False, "error": "belum ada key: isi ELYSIUM_PRIVATE_KEY di elysium_terminal/.env"}, 400)
            wallet = tw["address"]
            store = _auto_store()

            # ---- auto rules -------------------------------------------------
            if self.path == "/api/auto/global":
                return self._json({"ok": True, "auto": {**store.view(), "global": store.set_global(body)}})
            if self.path == "/api/auto/rule":
                gid = str(body["id"])
                row = _find_row(wallet, gid)
                if row is None:
                    return self._json({"ok": False, "error": "posisi ini tidak ada di wallet trading (refresh portfolio)"}, 400)
                rule = auto.clean_rule(body)
                if "rebalance" in (rule["above"], rule["below"]):
                    if row["posCount"] != 1:
                        return self._json({"ok": False, "error": "rebalance cuma buat posisi satu range. Buat ladder pilih close atau diam."}, 400)
                    if row.get("hooked") or row.get("dynamicFee"):
                        return self._json({"ok": False, "error": "pool ber-hook / fee dinamis: rebalance tidak didukung"}, 400)
                store.set_rule(gid, body, row, time.time())
                return self._json({"ok": True, "auto": store.view()})
            if self.path == "/api/auto/resume":
                gid = str(body["id"])
                cur = store.view()["rules"].get(gid)
                if not cur:
                    return self._json({"ok": False, "error": "rule tidak ada"}, 400)
                if cur.get("status") == "running":
                    return self._json({"ok": False, "error": "masih jalan, tunggu selesai"}, 400)
                if body.get("remove"):
                    store.set_rule(gid, {}, None, time.time())
                else:
                    store.patch(gid, {"status": "armed", "msg": "dilanjutkan manual", "fails": 0, "outSince": None})
                return self._json({"ok": True, "auto": store.view()})

            # ---- trades -----------------------------------------------------
            if self.path == "/api/trade/tight-open":
                pool = str(body["pool"]).lower()
                usd = float(body["usd"])
                if not usd > 0:
                    return self._json({"ok": False, "error": "ukuran harus > 0"}, 400)
                row = _snapshot_row(pool) or {}
                quote_usd = row.get("quoteUsd") or (1.0 if row.get("quoteSymbol") == "USDC" else STATE["ix"].hype_usd())
                if not quote_usd:
                    return self._json({"ok": False, "error": "harga quote belum kebaca, coba lagi sebentar"}, 400)
                width = tight.sanitize_config({"widthPct": body.get("widthPct")}, {})["widthPct"]
                slip = auto.clean_global({"slippageBps": body.get("slippageBps")}, store.view()["global"])["slippageBps"]
                args = ["--mode", "open", "--pool", pool, "--usd", str(usd), "--quote-usd", str(quote_usd),
                        "--width", f"{width:g}", "--slippage-bps", str(slip)]
                res = run_trade("tight.mjs", args, wallet, 600, pool=pool)
                gid = _register(res, "tight", depositQuote=res.get("depositQuote"), widthPct=width)
                res["id"] = gid
                rule = auto.clean_rule(body.get("rule") or {})
                if gid and not auto.is_empty(rule):
                    store.set_rule(gid, {**rule, "widthPct": rule.get("widthPct") or width},
                                   {"pool": pool, "symbol": body.get("symbol") or row.get("symbol")}, time.time())
                    res["ruleArmed"] = True
                append("decisions.jsonl", {"ts": _now_iso(), "source": "tight", "pool": pool, "symbol": body.get("symbol"),
                                           "decision": "enter", "sizeUsd": usd, "widthPct": width, "id": gid,
                                           **{k: res.get(k) for k in ("ok", "error", "hash", "practice", "stage", "sent")}})
                return self._json(res)
            if self.path == "/api/trade/rebalance":
                row = _find_row(wallet, str(body["id"]), force=True)
                if row is None:
                    return self._json({"ok": False, "error": "posisi tidak ketemu (refresh portfolio)"}, 400)
                g = store.view()["global"]
                width = auto.clean_global({"widthPct": body.get("widthPct") or row.get("widthPct") or g["widthPct"]}, g)["widthPct"]
                res = do_rebalance(row, wallet, width, g["slippageBps"])
                if res.get("newId"):
                    store.move(row["id"], res["newId"], {"lastFire": time.time()})
                append("closes.jsonl", {"ts": _now_iso(), "source": "web", "id": row["id"], "pool": row["pool"], "symbol": row["symbol"],
                                        "ids": _ids(row), "rule": "rebalance", "newId": res.get("newId"),
                                        **{k: res.get(k) for k in ("ok", "error", "hash", "practice", "stage", "sent")}})
                return self._json(res)
            if self.path == "/api/trade/open":
                pos = cfg["position"]
                usd = float(body["usd"])
                pool = str(body["pool"])
                args = node.ladder_args(pool, usd, int(body.get("slices") or pos["ladderSlices"]),
                                        float(body.get("top") if body.get("top") is not None else pos["rangeTopPct"]),
                                        float(body.get("bottom") or pos["rangeBottomPct"]), STATE["ix"].eth_usd())
                res = run_trade("open_ladder.mjs", args, wallet, pool=pool)
                plan = res.get("plan") or {}
                res["id"] = _register(res, "ladder", depositQuote=plan.get("total"))
                append("decisions.jsonl", {"ts": _now_iso(), "source": "web", "pool": body.get("pool"), "symbol": body.get("symbol"),
                                           "decision": "enter", "sizeUsd": usd, "override": bool(body.get("override")),
                                           "practice": res.get("practice"), "positions": res.get("positions"), "ok": res.get("ok"),
                                           "hash": res.get("hash"), "error": res.get("error"), "id": res.get("id")})
                return self._json(res)
            if self.path in ("/api/trade/close", "/api/trade/claim"):
                ids = [str(int(x)) for x in body["ids"]]
                claim = self.path.endswith("claim")
                pool = str(body.get("pool") or "")
                res = run_trade("close.mjs", ["--pool", pool, "--ids", ",".join(ids)] + (["--claim"] if claim else []), wallet, 300, pool=pool)
                append("closes.jsonl", {"ts": _now_iso(), "source": "web", "id": body.get("id"), "pool": body.get("pool"), "symbol": body.get("symbol"),
                                        "ids": ids, "rule": "claim" if claim else (body.get("rule") or "close"),
                                        **{k: res.get(k) for k in ("ok", "error", "hash", "practice")}})
                return self._json(res)
            if self.path == "/api/trade/reenter":
                ids = [str(int(x)) for x in body["ids"]]
                pos = cfg["position"]
                pool = str(body.get("pool") or "")
                args = ["--pool", pool, "--ids", ",".join(ids), "--maxloss", str(float(body.get("maxloss") or cfg["trade"]["maxLossPct"]))]
                if body.get("reopen"):
                    args += ["--reopen", "--slices", str(int(body.get("slices") or len(ids))),
                             "--top", str(float(body.get("top") if body.get("top") is not None else pos["rangeTopPct"])),
                             "--bottom", str(abs(float(body.get("bottom") or pos["rangeBottomPct"])))]
                res = run_trade("reenter.mjs", args, wallet, 900, pool=pool)
                if body.get("reopen"):
                    res["id"] = _register(res, "ladder", ids=res.get("positions"), depositQuote=res.get("reopenedQuote"))
                append("closes.jsonl", {"ts": _now_iso(), "source": "web", "id": body.get("id"), "pool": body.get("pool"), "symbol": body.get("symbol"),
                                        "ids": ids, "rule": "reenter" if body.get("reopen") else "close-swap",
                                        **{k: res.get(k) for k in ("ok", "error", "step", "practice")}})
                return self._json(res)
            if self.path == "/api/trade/cleanup":
                lst = cleanup_list(wallet)
                args = ["--tokens", json.dumps(lst), "--minusd", str(float(body.get("minusd") or 1)),
                        "--maxloss", str(float(body.get("maxloss") or cfg["trade"]["maxLossPct"]))]
                res = run_trade("cleanup.mjs", args, wallet, 900)
                append("closes.jsonl", {"ts": _now_iso(), "source": "web", "rule": "cleanup", **{k: res.get(k) for k in ("ok", "error", "tokens", "practice")}})
                return self._json(res)
        except (KeyError, ValueError) as exc:
            return self._json({"ok": False, "error": f"{exc}"}, 400)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"ok": False, "error": str(exc)[:300]}, 502)
        return self._send(404, b"not found", "text/plain")


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8893) -> None:
    Handler.cfg = cfg
    ix = make_index(cfg)
    STATE["ix"], STATE["book"] = ix, pools.Book(ix, cfg)
    print("Index swap: baca chain (pertama kali ±15–30 detik buat 1 jam terakhir)…", flush=True)
    ix.start()
    threading.Thread(target=_auto_loop, args=(cfg,), daemon=True, name="auto").start()
    srv = ThreadingHTTPServer((host, port), Handler)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"Elysium Terminal web → http://{shown}:{port}  (Ctrl+C buat stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ix.save()
