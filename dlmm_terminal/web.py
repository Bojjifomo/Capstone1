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

from . import api, exits, filters, node

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
        })
    for gone in set(states) - seen:
        states.pop(gone)
    DATA.mkdir(exist_ok=True)
    state_file.write_text(json.dumps(states))
    pools = list(meta.values())
    f = lambda k: sum(float(p.get(k) or 0) for p in pools)
    return {"wallet": wallet, "t": now, "rows": rows, "valueSol": f("balancesSol"), "unclaimedSol": f("unclaimedFeesSol"),
            "pnlSol": f("pnlSol"), "depositSol": f("totalDepositSol"), "exitsCfg": cfg["exits"]}


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
            if self.path in ("/api/trade/open", "/api/trade/close", "/api/trade/reenter"):
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
                if self.path.endswith("reenter"):
                    pos = self.cfg["position"]
                    positions = [str(x) for x in body["positions"]]
                    args = ["--pool", str(body["pool"]), "--positions", ",".join(positions), "--maxloss", str(float(body.get("maxloss") or 5))]
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
            if self.path == "/api/ladder/plan":
                pos = self.cfg["position"]
                args = node.ladder_args(str(body["pool"]), float(body["sol"]), int(body.get("slices") or pos.get("ladderSlices", 5)),
                                        abs(float(body.get("bottom") or pos["rangeBottomPct"])), float(body.get("top") or 0),
                                        str(body.get("strategy") or "spot"))
                return self._json(node.run("open_ladder.mjs", args + ["--plan"]))
        except (KeyError, ValueError) as exc:
            return self._json({"ok": False, "error": f"bad request: {exc}"}, 400)
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)[:300]}, 502)
        if self.path != "/api/decide":
            return self._send(404, b"not found", "text/plain")
        from .cli import append
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "web", **{k: body.get(k) for k in
               ("pool", "symbol", "decision", "decisionReason", "sizeSol", "level", "gatesFailed", "flags")}}
        append("decisions.jsonl", row)
        self._json({"ok": True, "logged": row})


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8790) -> None:
    Handler.cfg = cfg
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"DLMM terminal web → http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
