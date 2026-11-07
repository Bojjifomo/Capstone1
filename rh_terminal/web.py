"""RH Terminal, web version: Uniswap V4 pools on Robinhood Chain with the entry check, PING,
watchlist, ladder zap, and your wallet's ladders with exits, close, claim, re-entry and cleanup.

  python3 -m rh_terminal web                  # http://localhost:8892 (RH_WEB_PORT in .env)
  python3 -m rh_terminal web --host 0.0.0.0   # reachable over Tailscale (phone, MacBook)

Trading: the key in .env (RH_PRIVATE_KEY) or in RH Secrets (iCloud Scriptable) stays in the Node
executor; the browser only sees the address. Who may trade:
  * without RH_WEB_PIN: this Mac, plus devices on the home network or Tailscale, no PIN
  * with RH_WEB_PIN: this Mac without it, every other device with the PIN
Trade requests also need the X-RH-Trade header, which another website open in your browser
cannot add without a CORS preflight this server never answers.
RH_PRACTICE_MODE=true: every button only simulates (eth_call), nothing is sent.
"""
from __future__ import annotations

import hmac
import ipaddress
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

from . import chain, filters, node, pools, portfolio, settings
from .cli import DATA, append, env_report, load_config, load_env, make_index, notify, practice_mode

STATIC = Path(__file__).resolve().parent / "web"
POOL_TTL = 30
PF_TTL = 30

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
               f"https://dexscreener.com/robinhood/{p}")


# -------------------------------------------------------------- portfolio ----
def portfolio_of(wallet: str, force: bool = False) -> dict:
    key = ("pf", wallet.lower())
    with _lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit["t"] < PF_TTL:
            return hit["data"]
    cfg = Handler.cfg
    pf = portfolio.portfolio(wallet, cfg, DATA, STATE["book"])
    pf["autoExit"] = auto_load()
    pf["tpChoices"] = TP_CHOICES
    pf["autoExitCheckMin"] = cfg["web"]["autoExitCheckMin"]
    with _lock:
        _cache[key] = {"t": time.time(), "data": pf}
    _seen_tokens([r["token"] for r in pf["rows"]], [r["pool"] for r in pf["rows"]])
    return pf


def _seen_tokens(tokens: list[str], pool_ids: list[str]) -> None:
    """Remember every token a ladder held (and its pool), so Cleanup can find leftovers later."""
    d = _jload("tokens_seen.json", {})
    for t, p in zip(tokens, pool_ids):
        d.setdefault(t, p)
    _jsave("tokens_seen.json", d)


# -------------------------------------------------------------- auto exit ----
# data/auto_exit.json: {pool: {"pct", "symbol", "armedAt", "status": armed|firing|done|failed, "msg", "pnlPct"}}
# While the web server runs, a thread checks armed ladders every web.autoExitCheckMin minutes (chain
# reads only). When the ladder's PnL % reaches the target it runs Close+swap (reenter.mjs, no reopen).
TP_CHOICES = (3, 5, 10, 20)
_auto_lock = threading.Lock()


def auto_load() -> dict:
    return _jload("auto_exit.json", {})


def auto_set(pool: str, symbol: str, pct: float | None) -> dict:
    with _auto_lock:
        d = auto_load()
        if not pct:
            d.pop(pool, None)
        else:
            d[pool] = {"pct": float(pct), "symbol": symbol, "armedAt": time.time(), "status": "armed"}
        _jsave("auto_exit.json", d)
        return d


def auto_exit_once(cfg: dict, wallet: str) -> list[dict]:
    with _auto_lock:
        armed = {k: v for k, v in auto_load().items() if v.get("status") == "armed"}
    if not armed:
        return []
    pf = portfolio_of(wallet, force=True)
    lad = {r["pool"]: r for r in pf["rows"]}
    fired = []
    for pool, a in armed.items():
        g = lad.get(pool)
        with _auto_lock:
            d = auto_load()
            if pool not in d:
                continue
            if not g:
                d[pool].update(status="done", msg="posisi sudah nggak ada")
                _jsave("auto_exit.json", d)
                continue
            d[pool].update(pnlPct=g["pnlPct"], checkedAt=time.time())
            hit = g["pnlPct"] is not None and g["pnlPct"] >= a["pct"]
            if hit:
                d[pool]["status"] = "firing"
            _jsave("auto_exit.json", d)
        if not hit:
            continue
        ids = ",".join(str(p["tokenId"]) for p in g["positions"])
        notify(f"🎯 AUTO EXIT {g['symbol']}: PnL {g['pnlPct']:+.2f}% ≥ +{a['pct']:g}% → close + swap {len(g['positions'])} slice")
        args = ["--ids", ids, "--maxloss", str(cfg["trade"]["maxLossPct"])]
        if practice_mode():
            args += ["--dry-run", "--owner", wallet]
        res = node.run("reenter.mjs", args, timeout=900)
        ok = bool(res.get("ok"))
        msg = ("simulasi (practice mode)" if res.get("dryRun") else "closed + swapped") if ok else f"{res.get('step') or ''} {res.get('error') or 'gagal'}"
        with _auto_lock:
            d = auto_load()
            if pool in d:
                d[pool].update(status="done" if ok else "failed", msg=msg[:200], firedAt=time.time())
                _jsave("auto_exit.json", d)
        append("closes.jsonl", {"ts": _now_iso(), "source": "auto-exit", "pool": pool, "symbol": g["symbol"], "ids": ids,
                                "rule": f"takeProfit {a['pct']:g}%", "pnlPct": g["pnlPct"], "ok": ok, "error": res.get("error")})
        notify(f"{'✅' if ok else '❌'} AUTO EXIT {g['symbol']}: {msg}")
        fired.append({"pool": pool, "ok": ok, "msg": msg})
    return fired


def _auto_loop(cfg: dict) -> None:
    while True:
        try:
            tw = node.trade_wallet()
            if tw.get("ok") and auto_load():
                auto_exit_once(cfg, tw["address"])
        except Exception as exc:
            print(f"[auto-exit] {exc}", file=sys.stderr, flush=True)
        time.sleep(max(1, float(cfg["web"].get("autoExitCheckMin", 2))) * 60)


# ---------------------------------------------------------------- cleanup ----
def cleanup_list(wallet: str) -> list[dict]:
    """Tokens this wallet's ladders have held, with a balance now, each with its deepest USDG/ETH pool."""
    seen = _jload("tokens_seen.json", {})
    toks = [t for t in seen if t not in (chain.USDG, chain.WETH, chain.ZERO)]
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
_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def is_home_address(addr: str) -> bool:
    """Loopback, private LAN (192.168/10/172.16, fe80/fc00) or a Tailscale address."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.version == 4 and ip in _TAILSCALE:
        return True
    return ip.is_loopback or (ip.is_private and not ip.is_global)


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

    def _is_home(self) -> bool:
        return is_home_address(self.client_address[0])

    def _trade_refused(self, body: dict) -> str | None:
        if self.headers.get("X-RH-Trade") != "1" or "application/json" not in (self.headers.get("Content-Type") or ""):
            return "missing trade header"
        # The Mac running the server never needs the PIN. Without RH_WEB_PIN, devices on the home
        # network or Tailscale trade without one too; anything else (a public address) never trades.
        if self._is_local():
            return None
        pin = os.environ.get("RH_WEB_PIN")
        if pin:
            return None if hmac.compare_digest(str(body.get("pin") or ""), pin) else "PIN salah"
        if self._is_home():
            return None
        return "trading cuma dari Mac ini, Wi-Fi rumah, atau Tailscale"

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
            if u.path == "/api/pools":
                return self._json(pool_rows(q.get("force") == "1"))
            if u.path == "/api/search":
                return self._json({"rows": STATE["book"].search(q.get("q", ""))})
            if u.path == "/api/portfolio":
                wallet = (q.get("wallet") or "").strip()
                if not (wallet.startswith("0x") and len(wallet) == 42):
                    return self._json({"error": "wallet address 0x… (42 karakter)"}, 400)
                return self._json(portfolio_of(wallet, q.get("force") == "1"))
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
                                             "pin": bool(os.environ.get("RH_WEB_PIN")), "local": self._is_local(),
                                             "trusted": self._is_local() or (not os.environ.get("RH_WEB_PIN") and self._is_home()),
                                             "practice": practice_mode(),
                                             "envCheck": None if tw.get("ok") or not self._is_local() else env_report()},
                                   "wallet": os.environ.get("RH_WALLET")})
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

    def _trade(self, script: str, args: list[str], wallet: str, timeout: int = 600) -> dict:
        if practice_mode():
            args = args + ["--dry-run", "--owner", wallet]
        res = node.run(script, args, timeout=timeout)
        res["practice"] = practice_mode()
        with _lock:
            for k in [k for k in _cache if isinstance(k, tuple)]:
                _cache.pop(k, None)
        return res

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._json({"ok": False, "error": "bad json"}, 400)
        cfg = self.cfg
        try:
            if self.path == "/api/prefs":
                if self.headers.get("X-RH-Trade") != "1":
                    return self._json({"ok": False, "error": "missing header"}, 403)
                return self._json(prefs_set(body))
            if self.path == "/api/ladder/plan":
                pos = cfg["position"]
                args = node.ladder_args(str(body["pool"]), float(body["usd"]), int(body.get("slices") or pos["ladderSlices"]),
                                        float(body.get("top") if body.get("top") is not None else pos["rangeTopPct"]),
                                        float(body.get("bottom") or pos["rangeBottomPct"]), STATE["ix"].eth_usd())
                return self._json(node.run("open_ladder.mjs", args + ["--plan"], timeout=90))
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
                return self._json({"ok": False, "error": "belum ada key: isi RH_PRIVATE_KEY di rh_terminal/.env (atau RH Secrets di iCloud)"}, 400)
            wallet = tw["address"]
            if self.path == "/api/autoexit":
                pct = body.get("pct")
                pct = float(pct) if pct not in (None, "", 0, "0", "off") else None
                if pct is not None and not 0.5 <= pct <= 500:
                    return self._json({"ok": False, "error": "target 0.5–500%"}, 400)
                return self._json({"ok": True, "autoExit": auto_set(str(body["pool"]), str(body.get("symbol") or ""), pct)})
            if self.path == "/api/trade/open":
                pos = cfg["position"]
                usd = float(body["usd"])
                args = node.ladder_args(str(body["pool"]), usd, int(body.get("slices") or pos["ladderSlices"]),
                                        float(body.get("top") if body.get("top") is not None else pos["rangeTopPct"]),
                                        float(body.get("bottom") or pos["rangeBottomPct"]), STATE["ix"].eth_usd())
                res = self._trade("open_ladder.mjs", args, wallet)
                append("decisions.jsonl", {"ts": _now_iso(), "source": "web", "pool": body.get("pool"), "symbol": body.get("symbol"),
                                           "decision": "enter", "sizeUsd": usd, "override": bool(body.get("override")),
                                           "practice": res.get("practice"), "positions": res.get("positions"), "ok": res.get("ok"),
                                           "hash": res.get("hash"), "error": res.get("error")})
                return self._json(res)
            if self.path in ("/api/trade/close", "/api/trade/claim"):
                ids = [str(int(x)) for x in body["ids"]]
                claim = self.path.endswith("claim")
                res = self._trade("close.mjs", ["--ids", ",".join(ids)] + (["--claim"] if claim else []), wallet, 300)
                append("closes.jsonl", {"ts": _now_iso(), "source": "web", "pool": body.get("pool"), "symbol": body.get("symbol"),
                                        "ids": ids, "rule": "claim" if claim else (body.get("rule") or "close"),
                                        **{k: res.get(k) for k in ("ok", "error", "hash", "practice")}})
                return self._json(res)
            if self.path == "/api/trade/reenter":
                ids = [str(int(x)) for x in body["ids"]]
                pos = cfg["position"]
                args = ["--ids", ",".join(ids), "--maxloss", str(float(body.get("maxloss") or cfg["trade"]["maxLossPct"]))]
                if body.get("reopen"):
                    args += ["--reopen", "--slices", str(int(body.get("slices") or len(ids))),
                             "--top", str(float(body.get("top") if body.get("top") is not None else pos["rangeTopPct"])),
                             "--bottom", str(abs(float(body.get("bottom") or pos["rangeBottomPct"])))]
                res = self._trade("reenter.mjs", args, wallet, 900)
                append("closes.jsonl", {"ts": _now_iso(), "source": "web", "pool": body.get("pool"), "symbol": body.get("symbol"),
                                        "ids": ids, "rule": "reenter" if body.get("reopen") else "close-swap",
                                        **{k: res.get(k) for k in ("ok", "error", "step", "practice")}})
                return self._json(res)
            if self.path == "/api/trade/cleanup":
                lst = cleanup_list(wallet)
                args = ["--tokens", json.dumps(lst), "--minusd", str(float(body.get("minusd") or 1)),
                        "--maxloss", str(float(body.get("maxloss") or cfg["trade"]["maxLossPct"]))]
                res = self._trade("cleanup.mjs", args, wallet, 900)
                append("closes.jsonl", {"ts": _now_iso(), "source": "web", "rule": "cleanup", **{k: res.get(k) for k in ("ok", "error", "tokens", "practice")}})
                return self._json(res)
        except (KeyError, ValueError) as exc:
            return self._json({"ok": False, "error": f"{exc}"}, 400)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"ok": False, "error": str(exc)[:300]}, 502)
        return self._send(404, b"not found", "text/plain")


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8892) -> None:
    Handler.cfg = cfg
    ix = make_index(cfg)
    STATE["ix"], STATE["book"] = ix, pools.Book(ix, cfg)
    print("Index swap: baca chain (pertama kali ±15–30 detik buat 1 jam terakhir)…", flush=True)
    ix.start()
    threading.Thread(target=_auto_loop, args=(cfg,), daemon=True, name="auto-exit").start()
    srv = ThreadingHTTPServer((host, port), Handler)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"RH Terminal web → http://{shown}:{port}  (Ctrl+C buat stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ix.save()
