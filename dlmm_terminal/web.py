"""DLMM terminal, web version: Meteora DLMM SOL pools with the spec v1 entry check,
a detail panel with the chart, and your wallet's positions with the exit rules.

  python3 -m dlmm_terminal web                 # http://localhost:8790
  python3 -m dlmm_terminal web --port 8790 --host 0.0.0.0   # reachable over Tailscale

Trading (like LP WEB): put the wallet's private key in .env as DLMM_PRIVATE_KEY (base58,
as Phantom exports it). The key stays in the Node executor on this Mac; the browser only
ever sees the address. Without a key the page is read-only.

Who may trade:
  * without DLMM_WEB_PIN: only a browser on this Mac (127.0.0.1)
  * with DLMM_WEB_PIN set: any device that can reach the page (phone over Tailscale), with the PIN
Trade requests must also carry the X-DLMM-Trade header, which another website open in your
browser cannot add without a CORS preflight this server never answers.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import api, exits, filters, node, settings

HERE = Path(__file__).resolve().parent
STATIC = HERE / "web"
DEX = "https://api.dexscreener.com/latest/dex/pairs/solana/"
POOL_TTL = 60          # seconds a pool scan is reused
LOG_EVERY = 15 * 60    # candidates.jsonl gets a full snapshot at most this often

_lock = threading.Lock()
_cache: dict = {}
_last_log = 0.0


def _get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "dlmm-terminal/1", "Accept": "application/json"})
    with api.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def dex_market(ids: list[str]) -> dict:
    """DexScreener per pool: price changes, buys/sells, volume pace. A failed batch only costs those columns."""
    out = {}
    for i in range(0, len(ids), 30):
        try:
            d = _get_json(DEX + ",".join(ids[i:i + 30]), timeout=10)
        except Exception:
            continue
        for p in d.get("pairs") or []:
            pc, tx, vol = p.get("priceChange") or {}, p.get("txns") or {}, p.get("volume") or {}
            out[p.get("pairAddress")] = {
                "chg": {"5m": pc.get("m5"), "1h": pc.get("h1"), "6h": pc.get("h6"), "24h": pc.get("h24")},
                "buys24": (tx.get("h24") or {}).get("buys"), "sells24": (tx.get("h24") or {}).get("sells"),
                "vol1h": vol.get("h1"), "vol24": vol.get("h24"),
                "icon": (p.get("info") or {}).get("imageUrl"),
            }
    return out


def verdict(c: dict, m: dict | None) -> dict:
    """OPEN / CAREFUL / WAIT / SKIP from the spec gates and flags, plus reasons shown as ✓ / ✗.

    SKIP  a gate that won't change (SOL not in pair, bin step outside 20-250)
    WAIT  a gate that can change (pool too young, fee APR too low; size vs TVL only if configured)
    CAREFUL  gates pass, some flag shows
    OPEN  gates pass, no flags
    Market signals (volume pace, buyers vs sellers) are context only: the spec found
    no entry signal that held up on unseen tokens, so they never change the level.
    """
    hard = [g for g in c["gatesFailed"] if g.startswith(("solQuoted", "binStep"))]
    level = "SKIP" if hard else "WAIT" if c["gatesFailed"] else "CAREFUL" if c["flags"] else "OPEN"
    bad = [{"ok": False, "text": g} for g in c["gatesFailed"]] + [{"ok": False, "text": f, "flag": True} for f in c["flags"]]
    good = []
    if not c["gatesFailed"]:
        good.append({"ok": True, "text": f"Gates pass: fee APR {c['feeApr24h']:,.0f}% · {c['sizeVsTvlPct']}% of TVL · {c['poolAgeHours']:.1f}h old"})
    info = []
    if m:
        v1, v24 = m.get("vol1h") or 0, m.get("vol24") or 0
        if v24 and v1 * 24 >= 1.5 * v24:
            info.append({"ok": True, "text": f"Volume picking up: last hour runs {v1 * 24 / v24:.1f}x the 24h pace"})
        elif v24 and v1 * 24 <= 0.4 * v24:
            info.append({"ok": False, "text": f"Quiet: last hour runs {v1 * 24 / v24:.1f}x the 24h pace"})
        b, s = m.get("buys24") or 0, m.get("sells24") or 0
        if b + s >= 50:
            share = b / (b + s) * 100
            if share >= 55:
                info.append({"ok": True, "text": f"Buyers lead: {share:.0f}% of 24h trades are buys"})
            elif share <= 45:
                info.append({"ok": False, "text": f"Sellers lead: {100 - share:.0f}% of 24h trades are sells"})
    score = len(good) + sum(1 for x in info if x["ok"]) - len(bad) - sum(1 for x in info if not x["ok"])
    return {"level": level, "score": score, "reasons": bad + good, "info": info}


def pool_rows(cfg: dict, min_tvl: float, force: bool = False) -> dict:
    global _last_log
    key = ("pools", min_tvl)
    with _lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit["t"] < POOL_TTL:
            return hit["data"]
    s = cfg["scan"]
    min_ratio = cfg["gates"]["minFeeApr24hPct"] / 365 / 4   # wide prefilter: WAIT rows are worth seeing too
    base = f"is_blacklisted=false && tvl>={max(min_tvl, s['prefilterMinTvlUsd'])} && fee_tvl_ratio_24h>={min_ratio:.4f}"
    snaps, seen = [], set()
    for side in ("token_y", "token_x"):
        for page in range(1, s["pages"] + 1):
            rows = api.list_pools(f"{base} && {side}={api.SOL_MINT}", page=page, page_size=s["pageSize"])
            for r in rows:
                if r["address"] not in seen:
                    seen.add(r["address"])
                    snaps.append(r)
            if len(rows) < s["pageSize"]:
                break
    market = dex_market([p["address"] for p in snaps])
    now = datetime.now(timezone.utc)
    out = []
    for snap in snaps:
        m = market.get(snap["address"])
        c = filters.evaluate(snap, [], cfg, now, changes=(m or {}).get("chg"))
        v = verdict(c, m)
        tvl = snap.get("tvl") or 0
        fees, vol = snap.get("fees") or {}, snap.get("volume") or {}
        out.append({
            **{k: c[k] for k in ("pool", "name", "symbol", "tokenMint", "solSide", "poolAgeHours", "tvlUsd", "feeApr1h",
                                 "feeApr24h", "volTvl24h", "mcapUsd", "priceChg1h", "priceChg6h", "priceChg24h", "binStep",
                                 "baseFeePct", "sizeVsTvlPct", "holders", "priceSol", "gatesFailed", "flags", "plan", "solUsd")},
            "vol24": vol.get("24h") or 0, "vol1h": vol.get("1h") or 0,
            # fees per window, and the same as % of TVL per day at that window's pace
            "fees": {w: fees.get(w) or 0 for w in ("1h", "4h", "24h")},
            "feePctDay": {w: (fees.get(w) or 0) / tvl * 100 * 24 / h if tvl else 0 for w, h in (("1h", 1), ("4h", 4), ("24h", 24))},
            "chg5m": ((m or {}).get("chg") or {}).get("5m"), "icon": (m or {}).get("icon"),
            "priceUsd": filters.token_of(snap).get("price") if filters.sol_side(snap) else None,
            "verdict": v,
        })
    data = {"t": time.time(), "rows": out, "solUsd": next((r["solUsd"] for r in out if r["solUsd"]), None),
            "maxPositionSol": filters.max_position_sol(cfg), "sizeSol": filters.planned_size_sol(cfg)}
    with _lock:
        _cache[key] = {"t": time.time(), "data": data}
    if time.time() - _last_log >= LOG_EVERY:   # spec section 5: log every candidate, skipped ones too
        _last_log = time.time()
        from .cli import append
        for snap in snaps:
            m = market.get(snap["address"])
            c = filters.evaluate(snap, [], cfg, now, changes=(m or {}).get("chg"))
            c["source"] = "web"
            append("candidates.jsonl", c)
    return data


# ---------- PING: the signal filter (v1 gates + GMGN bands - dead→alive), top N ----------
_ping = {"t": 0.0, "running": False, "data": None, "error": None, "cfgKey": None}
PING_TTL = 5 * 60


def _ping_key(cfg: dict) -> str:
    keep = {k: cfg.get(k) for k in ("universe", "gates", "signal", "scan")}
    return json.dumps(keep, sort_keys=True, default=str)


def _benchmark_profile() -> dict:
    """data/benchmark.json if the signal command built one; the filter itself only needs the owner's bands."""
    from .cli import DATA
    try:
        return json.loads((DATA / "benchmark.json").read_text())
    except (OSError, ValueError):
        return {"wallet": "", "days": 0, "bands": {}, "binSteps": {}}


def _ping_run(cfg: dict) -> None:
    from . import blast
    from .cli import scan_hits
    try:
        now = time.time()
        prof = _benchmark_profile()
        state = {"lastLog": now}                      # the signal command logs candidates; the web doesn't
        hits = scan_hits(cfg, prof, state, now)
        dx = blast.dex_5m([c["pool"] for c, _, _ in hits])
        rows = [blast.row(c, snap, m, dx.get(c["pool"]), now) for c, m, snap in hits]
        sig = cfg["signal"]
        best = blast.top(rows, int(sig.get("blastTop", 10)), sig.get("blastSortBy", "volTvl1h"))
        for i, r in enumerate(best, 1):
            r["rank"] = i
            r.pop("hours", None)
        revived = state.get("revivedExcluded") or []
        prev = {r["pool"] for r in ((_ping.get("data") or {}).get("rows") or [])}
        data = {"t": now, "rows": best, "total": len(rows), "revived": [{"pool": x["pool"], "symbol": x["symbol"]} for x in revived],
                "filter": blast.filter_text(cfg, prof, len(best), len(rows), len(revived)),
                "new": [r["pool"] for r in best if prev and r["pool"] not in prev]}
        with _lock:
            _ping.update(t=now, data=data, error=None, cfgKey=_ping_key(cfg))
    except Exception as exc:
        with _lock:
            _ping.update(t=time.time(), error=str(exc)[:300])
    finally:
        with _lock:
            _ping["running"] = False


def ping_rows(cfg: dict, force: bool = False) -> dict:
    """Cached PING list; a stale or forced one is rebuilt in the background (it takes 20-60 s:
    one DexScreener request per token that passes the gates, one history request per hit)."""
    with _lock:
        stale = force or time.time() - _ping["t"] > PING_TTL or _ping["cfgKey"] not in (None, _ping_key(cfg))
        if stale and not _ping["running"]:
            _ping["running"] = True
            threading.Thread(target=_ping_run, args=(cfg,), daemon=True).start()
        return {"running": _ping["running"], "error": _ping["error"], **(_ping["data"] or {"rows": [], "t": 0})}


# ---------- watchlist: the pools you picked to play ----------
def _watch_file():
    from .cli import DATA
    return DATA / "watchlist.json"


def watchlist() -> list[dict]:
    try:
        return json.loads(_watch_file().read_text())
    except (OSError, ValueError):
        return []


def watch_set(pool: str, symbol: str, on: bool, note: str | None = None) -> list[dict]:
    cur = [w for w in watchlist() if w.get("pool") != pool]
    if on:
        cur.append({"pool": pool, "symbol": symbol, "addedAt": time.time(), **({"note": note} if note else {})})
    f = _watch_file()
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(cur, indent=1))
    return cur


def wallet_sol(wallet: str) -> float | None:
    """SOL balance on the public node (reads never spend the Helius quota)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet, {"commitment": "confirmed"}]}).encode()
    req = urllib.request.Request(node.PUBLIC_RPC, data=body, headers={"Content-Type": "application/json", "User-Agent": "dlmm-terminal/1"})
    try:
        with api.urlopen(req, timeout=10) as r:
            return json.load(r)["result"]["value"] / 1e9
    except Exception:
        return None


# ---------- auto exit (take profit per ladder, you switch it on per position) ----------
# data/auto_exit.json: {pool: {"pct": 5, "symbol", "armedAt", "status": armed|firing|done|failed, "msg", "pnlPct"}}
# While the web server runs, a thread checks the armed ladders every web.autoExitCheckMin minutes
# (Meteora's data API only, no RPC). When the ladder's PnL % (PnL SOL / deposit, the number on the
# row) reaches the target, it closes every slice of that pool and sells the token to SOL (reenter.mjs
# without --reopen, the Close+swap button). Spec v1 found fixed take-profits lost SOL vs. manual
# exits; this is off until you arm it.
_auto_lock = threading.Lock()
TP_CHOICES = (3, 5, 10)


def _auto_file():
    from .cli import DATA
    return DATA / "auto_exit.json"


def auto_load() -> dict:
    try:
        return json.loads(_auto_file().read_text())
    except (OSError, ValueError):
        return {}


def auto_save(d: dict) -> None:
    f = _auto_file()
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(d, indent=1))


def auto_set(pool: str, symbol: str, pct: float | None) -> dict:
    with _auto_lock:
        d = auto_load()
        if not pct:
            d.pop(pool, None)
        else:
            d[pool] = {"pct": float(pct), "symbol": symbol, "armedAt": time.time(), "status": "armed"}
        auto_save(d)
        return d


def _ladders(wallet: str) -> dict:
    """{pool: {"symbol", "positions": [...], "pnlSol", "depositSol", "pnlPct"}} from Meteora's data API."""
    from .cli import ticks_for_wallet
    meta = {p["poolAddress"]: p for p in api.open_portfolio(wallet)}
    out: dict = {}
    for t in ticks_for_wallet(wallet, time.time()):
        g = out.setdefault(t.pool, {"symbol": t.symbol, "positions": []})
        g["positions"].append(t.position)
    for pool, g in out.items():
        m = meta.get(pool, {})
        pnl, dep = float(m.get("pnlSol") or 0), float(m.get("totalDepositSol") or 0)
        g.update(pnlSol=pnl, depositSol=dep, pnlPct=pnl / dep * 100 if dep else None)
    return out


def auto_exit_once(cfg: dict, wallet: str) -> list[dict]:
    """One pass: fire every armed ladder at or over its target. Returns what fired."""
    from .cli import append, notify
    with _auto_lock:
        armed = {k: v for k, v in auto_load().items() if v.get("status") == "armed"}
    if not armed:
        return []
    ladders = _ladders(wallet)
    fired = []
    for pool, a in armed.items():
        g = ladders.get(pool)
        with _auto_lock:
            d = auto_load()
            if pool not in d:
                continue
            if not g:                                        # closed some other way
                d[pool].update(status="done", msg="posisi sudah nggak ada")
                auto_save(d)
                continue
            d[pool]["pnlPct"] = g["pnlPct"]
            d[pool]["checkedAt"] = time.time()
            hit = g["pnlPct"] is not None and g["pnlPct"] >= a["pct"]
            if hit:
                d[pool]["status"] = "firing"
            auto_save(d)
        if not hit:
            continue
        notify(f"🎯 AUTO EXIT {g['symbol']}: PnL {g['pnlPct']:+.2f}% ≥ +{a['pct']:g}% → close + swap {len(g['positions'])} slice")
        res = node.run("reenter.mjs", ["--pool", pool, "--positions", ",".join(g["positions"]),
                                       "--maxloss", str(cfg["trade"]["maxLossPct"])], timeout=900)
        ok = bool(res.get("ok"))
        msg = (f"closed, ◎{res.get('solBack', 0):.4f} balik" if ok else f"{res.get('step') or ''} {res.get('error') or 'gagal'}")[:200]
        with _auto_lock:
            d = auto_load()
            if pool in d:
                d[pool].update(status="done" if ok else "failed", msg=msg, firedAt=time.time(), pnlPct=g["pnlPct"])
                auto_save(d)
        append("closes.jsonl", {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "auto-exit", "pool": pool,
                                "symbol": g["symbol"], "positions": g["positions"], "rule": f"takeProfit {a['pct']:g}%",
                                "pnlPct": g["pnlPct"], **{k: res.get(k) for k in ("ok", "error", "step", "solBack")}})
        notify(f"{'✅' if ok else '❌'} AUTO EXIT {g['symbol']}: {msg}")
        fired.append({"pool": pool, "ok": ok, "msg": msg})
    return fired


def _auto_loop(cfg: dict) -> None:
    import sys
    while True:
        try:
            tw = node.trade_wallet()
            if tw.get("ok") and auto_load():
                auto_exit_once(cfg, tw["pubkey"])
        except Exception as exc:
            print(f"[auto-exit] {exc}", file=sys.stderr, flush=True)
        time.sleep(max(1, float((cfg.get("web") or {}).get("autoExitCheckMin", 2))) * 60)


def portfolio(wallet: str, cfg: dict) -> dict:
    from .cli import DATA, ticks_for_wallet
    now = time.time()
    state_file = DATA / "web_monitor_state.json"
    try:
        states = json.loads(state_file.read_text())
    except Exception:
        states = {}
    meta = {p["poolAddress"]: p for p in api.open_portfolio(wallet)}
    rows, seen = [], set()
    for t in ticks_for_wallet(wallet, now):
        seen.add(t.position)
        st = exits.State.from_dict(states.get(t.position))
        ev = exits.evaluate(t, st, cfg["exits"])
        states[t.position] = st.to_dict()
        p = meta.get(t.pool, {})
        rows.append({
            "position": t.position, "pool": t.pool, "symbol": t.symbol, "pnlPct": t.pnl_pct, "pnlSol": t.pnl_sol,
            "peakPnlPct": st.peak_pnl, "solOnly": t.sol_only,
            "priceVsTopPct": (t.price / t.range_max - 1) * 100 if t.range_max else None,
            "rangeBottomPct": (t.range_min / t.range_max - 1) * 100 if t.range_max else None,
            "inRange": t.range_min <= t.price <= t.range_max, "below": t.price < t.range_min,
            "heldMin": (now - t.created_at) / 60 if t.created_at else None,
            "events": ev, "binStep": p.get("binStep"),
            "poolBalancesSol": p.get("balancesSol"), "poolUnclaimedSol": p.get("unclaimedFeesSol"),
            "poolPnlSol": p.get("pnlSol"), "poolDepositSol": p.get("totalDepositSol"),
            "poolPnlPct": p.get("pnlSolPctChange") if p.get("pnlSolPctChange") is not None else p.get("pnlPctChange"),
            "rangeMax": t.range_max, "rangeMin": t.range_min, "price": t.price,
        })
    for gone in set(states) - seen:
        states.pop(gone)
    DATA.mkdir(exist_ok=True)
    state_file.write_text(json.dumps(states))
    pools = list(meta.values())
    f = lambda k: sum(float(p.get(k) or 0) for p in pools)
    return {"wallet": wallet, "t": now, "rows": rows, "valueSol": f("balancesSol"), "unclaimedSol": f("unclaimedFeesSol"),
            "pnlSol": f("pnlSol"), "depositSol": f("totalDepositSol"), "exitsCfg": cfg["exits"],
            "walletSol": wallet_sol(wallet), "autoExit": auto_load(), "tpChoices": TP_CHOICES,
            "autoExitCheckMin": (cfg.get("web") or {}).get("autoExitCheckMin", 2)}


class Handler(BaseHTTPRequestHandler):
    cfg: dict = {}

    def log_message(self, fmt, *a):  # quiet
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

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == "/api/pools":
                return self._json(pool_rows(self.cfg, float(q.get("tvl", 2000)), q.get("force") == "1"))
            if u.path == "/api/pool":
                return self._json(api.pool(q["address"]))
            if u.path == "/api/portfolio":
                wallet = q.get("wallet") or os.environ.get("DLMM_WALLET")
                if not wallet:
                    return self._json({"error": "no wallet: pass ?wallet= or set DLMM_WALLET"}, 400)
                return self._json(portfolio(wallet, self.cfg))
            if u.path == "/api/ping":
                return self._json(ping_rows(self.cfg, q.get("force") == "1"))
            if u.path == "/api/watchlist":
                return self._json({"rows": watchlist()})
            if u.path == "/api/settings":
                return self._json({"form": settings.form(self.cfg, self.base_cfg()), "overlay": settings.load()})
            if u.path == "/api/config":
                tw = node.trade_wallet() if not node.ready() else {"ok": False}
                return self._json({"cfg": self.cfg, "wallet": tw.get("pubkey") or os.environ.get("DLMM_WALLET"),
                                   "maxPositionSol": filters.max_position_sol(self.cfg), "executor": node.ready(),
                                   "trade": {"enabled": bool(tw.get("ok")), "wallet": tw.get("pubkey"),
                                             "error": None if tw.get("ok") or not node.key_configured() else tw.get("error"),
                                             "pin": bool(os.environ.get("DLMM_WEB_PIN")), "local": self._is_local()}})
        except Exception as exc:
            import sys
            import traceback
            print(f"[{datetime.now():%H:%M:%S}] {u.path} failed: {exc}", file=sys.stderr, flush=True)
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                print("  → SSL: jalanin `python3 -m pip install certifi` (atau 'Install Certificates.command' di folder Python), lalu start ulang.", file=sys.stderr)
            else:
                traceback.print_exc()
            return self._json({"error": str(exc)[:300]}, 502)
        name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(f.suffix, "application/octet-stream")
        self._send(200, f.read_bytes(), ctype)

    config_path = None

    def base_cfg(self) -> dict:
        from .cli import load_config
        return load_config(self.config_path, overlay=False)

    def reload_cfg(self) -> None:
        """Settings changed: the same cfg object (every request holds it) gets the new values; caches drop."""
        from .cli import load_config
        new = load_config(self.config_path)
        self.cfg.clear()
        self.cfg.update(new)
        with _lock:
            _cache.clear()

    def _is_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _trade_refused(self, body: dict) -> str | None:
        """None when this request may move money, else why not."""
        import hmac
        if self.headers.get("X-DLMM-Trade") != "1" or "application/json" not in (self.headers.get("Content-Type") or ""):
            return "missing trade header"
        pin = os.environ.get("DLMM_WEB_PIN")
        if pin:
            return None if hmac.compare_digest(str(body.get("pin") or ""), pin) else "PIN salah"
        return None if self._is_local() else "trading cuma dari Mac ini (set DLMM_WEB_PIN di .env buat dari HP)"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            if self.path in ("/api/trade/open", "/api/trade/close", "/api/trade/reenter", "/api/trade/cleanup"):
                why = self._trade_refused(body)
                if why:
                    return self._json({"ok": False, "error": why}, 403)
                if not node.trade_wallet().get("ok"):
                    return self._json({"ok": False, "error": "belum ada key: isi DLMM_PRIVATE_KEY di .env lalu start ulang"}, 400)
                from .cli import append
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                if self.path.endswith("open"):
                    pos = self.cfg["position"]
                    sol = float(body["sol"])
                    args = node.ladder_args(str(body["pool"]), sol, int(body.get("slices") or pos.get("ladderSlices", 5)),
                                            abs(float(body.get("bottom") or pos["rangeBottomPct"])), float(body.get("top") or 0),
                                            str(body.get("strategy") or "spot"))
                    res = node.run("open_ladder.mjs", args, timeout=600)
                    append("decisions.jsonl", {"ts": ts, "source": "web", "pool": body.get("pool"), "symbol": body.get("symbol"),
                                               "decision": "enter", "sizeSol": sol, "override": bool(body.get("override")),
                                               "positions": [r.get("position") for r in res.get("results", [])], "ok": res.get("ok"),
                                               "error": res.get("error")})
                    return self._json(res)
                if self.path.endswith("cleanup"):
                    res = node.run("cleanup.mjs", ["--maxloss", str(float(body.get("maxloss") or self.cfg["trade"]["maxLossPct"]))], timeout=600)
                    append("closes.jsonl", {"ts": ts, "source": "web", "rule": "cleanup", **{k: res.get(k) for k in ("ok", "error", "emptyAccounts", "closed")}})
                    return self._json(res)
                if self.path.endswith("reenter"):
                    pos = self.cfg["position"]
                    positions = [str(x) for x in body["positions"]]
                    args = ["--pool", str(body["pool"]), "--positions", ",".join(positions), "--maxloss", str(float(body.get("maxloss") or self.cfg["trade"]["maxLossPct"]))]
                    if body.get("reopen"):
                        args += ["--reopen", "--slices", str(int(body.get("slices") or len(positions) or pos.get("ladderSlices", 5))),
                                 "--bottom", str(abs(float(body.get("bottom") or pos["rangeBottomPct"]))), "--top", str(float(body.get("top") or 0)),
                                 "--strategy", str(body.get("strategy") or "spot")]
                    res = node.run("reenter.mjs", args, timeout=900)
                    append("closes.jsonl", {"ts": ts, "source": "web", "pool": body.get("pool"), "symbol": body.get("symbol"),
                                            "positions": positions, "rule": "reenter" if body.get("reopen") else "close-swap", **{k: res.get(k) for k in ("ok", "error", "step", "solBack", "reopenSol")}})
                    return self._json(res)
                res = node.run("close_position.mjs", ["--pool", str(body["pool"]), "--position", str(body["position"])], timeout=300)
                append("closes.jsonl", {"ts": ts, "source": "web", "pool": body.get("pool"), "symbol": body.get("symbol"),
                                        "position": body.get("position"), "rule": body.get("rule"), **res})
                return self._json(res)
            if self.path == "/api/autoexit":
                why = self._trade_refused(body)
                if why:
                    return self._json({"ok": False, "error": why}, 403)
                if not node.trade_wallet().get("ok"):
                    return self._json({"ok": False, "error": "auto exit butuh key di .env (DLMM_PRIVATE_KEY)"}, 400)
                pct = body.get("pct")
                pct = float(pct) if pct not in (None, "", 0, "0", "off") else None
                if pct is not None and not 0.5 <= pct <= 100:
                    return self._json({"ok": False, "error": "target 0.5–100%"}, 400)
                return self._json({"ok": True, "autoExit": auto_set(str(body["pool"]), str(body.get("symbol") or ""), pct)})
            if self.path in ("/api/settings", "/api/watchlist"):
                why = self._trade_refused(body)
                if why:
                    return self._json({"ok": False, "error": why}, 403)
                if self.path == "/api/watchlist":
                    return self._json({"ok": True, "rows": watch_set(str(body["pool"]), str(body.get("symbol") or ""), bool(body.get("on")), body.get("note"))})
                if body.get("reset"):
                    settings.reset()
                else:
                    settings.save(self.base_cfg(), body.get("changes") or {})
                self.reload_cfg()
                return self._json({"ok": True, "form": settings.form(self.cfg, self.base_cfg()), "overlay": settings.load()})
            if self.path == "/api/ladder/plan":
                pos = self.cfg["position"]
                args = node.ladder_args(str(body["pool"]), float(body["sol"]), int(body.get("slices") or pos.get("ladderSlices", 5)),
                                        abs(float(body.get("bottom") or pos["rangeBottomPct"])), float(body.get("top") or 0),
                                        str(body.get("strategy") or "spot"))
                return self._json(node.run("open_ladder.mjs", args + ["--plan"]))
        except (KeyError, ValueError) as exc:
            return self._json({"ok": False, "error": f"{exc}"}, 400)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)[:300]}, 502)
        if self.path != "/api/decide":
            return self._send(404, b"not found", "text/plain")
        from .cli import append
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "web", **{k: body.get(k) for k in
               ("pool", "symbol", "decision", "decisionReason", "sizeSol", "level", "gatesFailed", "flags")}}
        append("decisions.jsonl", row)
        self._json({"ok": True, "logged": row})


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8790, config_path: str | None = None) -> None:
    Handler.cfg = cfg
    Handler.config_path = config_path
    threading.Thread(target=_auto_loop, args=(cfg,), daemon=True).start()   # auto exit (TP) for armed ladders
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"DLMM terminal web → http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
