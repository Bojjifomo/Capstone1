#!/usr/bin/env python3
"""Alert koin yang "surging abis jatoh" / "bangkit dari mati" di Robinhood Chain.

Data dari DexScreener + candle GeckoTerminal (gratis, tanpa API key). Cuma pakai stdlib.

Sinyal "bangkit dari mati" (semua token, kayak chart ZERO):
  pump -> dump >= --dead-drop % -> flat & sepi >= --dead-hours jam -> sekarang breakout
  >= --revival % di atas range matinya dengan volume >= 5x volume pas mati
Sinyal "bangkit abis jatoh" (semua token):
  1. Harga turun >= --drop % dari puncak ke dasar (dalam --lookback jam)
  2. Dasarnya masih baru (<= --low-age menit lalu)
  3. Harga sekarang udah mantul >= --rebound % dari dasar
Sinyal "lagi pump" (cuma token di watchlist / --watch):
  naik >= --pump % dari harga terendah --pump-window menit terakhir
Dua-duanya harus lolos konfirmasi: likuiditas cukup, volume 1 jam spike, buy > sell.

Contoh:
  python surge_alert.py --check                        # cek semua token di watchlist.txt
  python surge_alert.py --check 0x316f…2894            # cek 1 token
  python surge_alert.py                                # pantau watchlist.txt, kirim alert
  python surge_alert.py --scan                         # + scan token baru di chain
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = "https://api.dexscreener.com"
# (key priceChange DexScreener, panjang jendela dalam detik)
WINDOWS = (("m5", 300), ("h1", 3600), ("h6", 21600), ("h24", 86400))
DISCOVER_EVERY_S = 300  # endpoint profile/boost limitnya 60 req/menit
MAX_SCAN_TOKENS = 300
WATCHLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.txt")
GT_API = "https://api.geckoterminal.com/api/v2"
GT_MIN_GAP_S = 2.5        # GeckoTerminal keyless ~30 req/menit
GT_OHLCV_PER_LOOP = 8     # maks ambil candle per putaran polling
CANDLE_TTL_S = 600


@dataclass
class Config:
    chain: str = "robinhood"
    drop_pct: float = 40.0         # min turun puncak -> dasar
    rebound_pct: float = 30.0      # min naik dasar -> sekarang
    lookback_h: float = 24.0       # jendela nyari puncak
    max_low_age_min: float = 180   # dasar harus terjadi dalam X menit terakhir
    min_liq_usd: float = 10_000
    min_vol_h1_usd: float = 5_000
    vol_spike: float = 2.0         # volume 1j vs rata-rata per jam 6j terakhir (maks 6x)
    min_buy_ratio: float = 0.55    # porsi tx buy dalam 1 jam
    cooldown_min: float = 60
    rerun_mult: float = 1.5        # alert lagi kalau harga udah 1.5x dari alert terakhir
    pump_pct: float = 25.0         # watchlist: min naik dari low pump_window terakhir
    pump_window_min: float = 60
    dead_h: float = 24             # revival: min lama "mati" sebelum surge
    dead_flat: float = 2.0         # revival: range harga pas mati (p90/p10) maks segini
    dead_drop_pct: float = 70.0    # revival: level mati min X% di bawah puncak lama
    revival_pct: float = 50.0      # revival: harga min X% di atas atap range mati
    revival_window_h: float = 6    # revival: surge-nya dalam X jam terakhir
    revival_vol_mult: float = 5.0  # revival: volume/jam pas surge vs pas mati
    history_days: float = 30       # revival: candle berapa hari ke belakang


@dataclass
class Signal:
    peak: float
    low: float
    low_ts: float
    price: float
    drop: float
    rebound: float
    kind: str = "dip"  # "dip" = bangkit abis jatoh, "pump" = lagi pump, "revival" = bangkit dari mati
    dead_h: float = 0.0


def warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr, flush=True)


_warned: set[str] = set()


def warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        warn(msg)


# ---------------------------------------------------------------- HTTP

def _request(req: urllib.request.Request, timeout: float = 20):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except urllib.error.URLError:
            if attempt == 3:
                raise
        time.sleep(2 ** (attempt + 1))


def get_json(path: str, params: dict | None = None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    return _request(urllib.request.Request(url, headers={
        "User-Agent": "surge-alert/1.0", "Accept": "application/json"}))


_gt_last_call = 0.0


def gt_get(path: str, params: dict | None = None):
    global _gt_last_call
    time.sleep(max(0.0, _gt_last_call + GT_MIN_GAP_S - time.time()))
    _gt_last_call = time.time()
    url = GT_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    return _request(urllib.request.Request(url, headers={
        "User-Agent": "surge-alert/1.0", "Accept": "application/json;version=20230302"}))


def post(url: str, data: bytes, headers: dict) -> None:
    _request(urllib.request.Request(url, data=data, method="POST",
                                    headers={"User-Agent": "surge-alert/1.0", **headers}))


def post_json(url: str, payload: dict) -> None:
    post(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})


# ---------------------------------------------------------------- DexScreener

def liq(pair: dict) -> float:
    return float((pair.get("liquidity") or {}).get("usd") or 0)


def is_address(s: str) -> bool:
    return s.lower().startswith("0x") and len(s) == 42


def resolve(item: str, chain: str) -> str | None:
    """Simbol atau alamat -> alamat token. Simbol dipilih yang pair-nya paling likuid."""
    if is_address(item):
        return item.lower()
    sym = item.lstrip("$").upper()
    pairs = (get_json("/latest/dex/search", {"q": sym}) or {}).get("pairs") or []
    same_sym = [p for p in pairs if p["baseToken"]["symbol"].upper() == sym]
    on_chain = [p for p in same_sym if p.get("chainId") == chain]
    if not on_chain:
        chains = sorted({p.get("chainId") for p in same_sym})
        warn(f"{sym}: gak ketemu di chain '{chain}'. Ketemu di: {chains or '-'}. "
             "Cek --chain atau pakai alamat kontrak.")
        return None
    best = max(on_chain, key=liq)
    addr = best["baseToken"]["address"].lower()
    clones = {p["baseToken"]["address"].lower() for p in on_chain} - {addr}
    if clones:
        warn(f"{sym}: ada {len(clones)} token lain dengan simbol sama (bisa clone/scam). "
             f"Dipilih yang paling likuid: {addr} — mending pakai alamat ini langsung.")
    return addr


def fetch_best_pairs(chain: str, addrs: list[str]) -> dict[str, dict]:
    """Alamat token -> pair paling likuid di mana token itu base token."""
    wanted, best = set(addrs), {}
    for i in range(0, len(addrs), 30):  # API maks 30 alamat per request
        try:
            pairs = get_json(f"/tokens/v1/{chain}/{','.join(addrs[i:i + 30])}") or []
        except Exception as e:
            warn(f"gagal ambil pair: {e}")
            continue
        for p in pairs:
            a = p["baseToken"]["address"].lower()
            if a in wanted and (a not in best or liq(p) > liq(best[a])):
                best[a] = p
    return best


def discover(chain: str) -> list[str]:
    """Token di chain ini yang baru masang profile / boost di DexScreener."""
    found = []
    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        try:
            items = get_json(path) or []
        except Exception as e:
            warn(f"discover {path} gagal: {e}")
            continue
        for it in items if isinstance(items, list) else [items]:
            if it.get("chainId") == chain and it.get("tokenAddress"):
                found.append(it["tokenAddress"].lower())
    # pool yang lagi trending 1 jam terakhir: koin yang baru bangun biasanya masuk sini
    try:
        res = gt_get(f"/networks/{chain}/trending_pools", {"duration": "1h"}) or {}
        for pool in res.get("data") or []:
            base = ((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}
            if base.get("id", "").startswith(chain + "_"):
                found.append(base["id"].split("_", 1)[1].lower())
    except Exception as e:
        warn(f"discover GeckoTerminal trending gagal: {e}")
    return found


def _ohlcv(chain: str, pool: str, params: dict) -> list[tuple[float, float, float]]:
    try:
        res = gt_get(f"/networks/{chain}/pools/{pool}/ohlcv/hour", params) or {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    rows = ((res.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    return sorted((float(r[0]), float(r[4]), float(r[5] or 0)) for r in rows if r[4])


def fetch_candles(chain: str, pair: dict, days: float, pool_cache: dict) -> list:
    """Candle 1 jam [(ts, close, volume USD)] urut lama -> baru dari GeckoTerminal. [] kalau gagal."""
    token = pair["baseToken"]["address"].lower()
    params = {"aggregate": 1, "limit": min(1000, int(days * 24)), "currency": "usd", "token": token}
    pool = pool_cache.get(token) or pair.get("pairAddress")
    try:
        candles = _ohlcv(chain, pool, params) if pool else []
        if not candles:  # alamat pool DexScreener gak dikenal GeckoTerminal: pakai pool teratas token
            res = gt_get(f"/networks/{chain}/tokens/{token}/pools") or {}
            top = ((res.get("data") or [{}])[0].get("attributes") or {}).get("address")
            if top and top != pool:
                pool_cache[token] = top
                candles = _ohlcv(chain, top, params)
        return candles
    except Exception as e:
        warn_once("gt", f"candle GeckoTerminal gagal ({e}); sinyal 'bangkit dari mati' dilewat")
        return []


# ---------------------------------------------------------------- deteksi

def implied_past_prices(pair: dict, now: float) -> list[tuple[float, float]]:
    """Harga 5m/1j/6j/24j lalu, direkonstruksi dari priceChange DexScreener."""
    price = float(pair.get("priceUsd") or 0)
    pc = pair.get("priceChange") or {}
    pts = []
    for key, secs in WINDOWS:
        c = pc.get(key)
        if price > 0 and c is not None and c > -100:
            pts.append((now - secs, price / (1 + c / 100)))
    return sorted(pts)


def detect(history: list, now: float, cfg: Config) -> Signal | None:
    """Cari dasar baru (<= max_low_age) yang didahului puncak jauh lebih tinggi
    dan sekarang udah mantul. history = [[ts, price], ...] urut waktu, terakhir = sekarang."""
    pts = [(t, p) for t, p in history if now - t <= cfg.lookback_h * 3600 and p > 0]
    if len(pts) < 3:
        return None
    price = pts[-1][1]
    recent = [i for i in range(len(pts) - 1) if now - pts[i][0] <= cfg.max_low_age_min * 60]
    if not recent:
        return None
    i_low = min(recent, key=lambda i: pts[i][1])
    if i_low == 0:
        return None
    low_ts, low = pts[i_low]
    peak = max(p for _, p in pts[:i_low])
    drop = (1 - low / peak) * 100
    rebound = (price / low - 1) * 100
    if drop >= cfg.drop_pct and rebound >= cfg.rebound_pct:
        return Signal(peak, low, low_ts, price, drop, rebound)
    return None


def detect_pump(history: list, now: float, cfg: Config) -> Signal | None:
    """Naik tajam dari harga terendah <= pump_window menit terakhir, gak perlu dump dulu.
    Buat koin yang udah dipantau dan "pump lagi" (puncak lamanya bisa udah lewat 24 jam)."""
    pts = [(t, p) for t, p in history if now - t <= cfg.lookback_h * 3600 and p > 0]
    recent = [pt for pt in pts[:-1] if now - pt[0] <= cfg.pump_window_min * 60]
    if not recent:
        return None
    price = pts[-1][1]
    low_ts, low = min(recent, key=lambda pt: pt[1])
    rise = (price / low - 1) * 100
    if rise < cfg.pump_pct:
        return None
    peak = max(p for _, p in pts)
    return Signal(peak, low, low_ts, price, (1 - low / peak) * 100, rise, kind="pump")


def hourly(candles: list, now: float) -> list[tuple[float, float, float]]:
    """Jam tanpa trade gak punya candle: isi pakai close sebelumnya, volume 0."""
    if not candles:
        return []
    by_hour = {int(t // 3600): (c, v) for t, c, v in candles}
    out, last = [], None
    for h in range(min(by_hour), int(now // 3600) + 1):
        c, v = by_hour.get(h, (last, 0.0))
        out.append((h * 3600.0, c, v))
        last = c
    return out


def quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def looks_like_surge(pair: dict) -> bool:
    """Saringan murah sebelum ambil candle (GeckoTerminal ada rate limit)."""
    pc = pair.get("priceChange") or {}
    return (pc.get("h1") or 0) >= 20 or (pc.get("h6") or 0) >= 40


def detect_revival(candles: list, price: float, now: float, cfg: Config,
                   why: list | None = None) -> Signal | None:
    """Pump -> dump -> mati (flat & sepi) -> bangun lagi. Alasan gagal ditaruh di `why`."""
    def no(reason: str) -> None:
        if why is not None:
            why.append(reason)

    series = hourly(candles, now)
    surge_from = now - cfg.revival_window_h * 3600
    base_from = surge_from - cfg.dead_h * 3600
    before = [c for c in series if c[0] < base_from]
    base = [c for c in series if base_from <= c[0] < surge_from]
    surge = [c for c in series if c[0] >= surge_from]
    if not before or len(base) < cfg.dead_h / 2 or price <= 0:
        return no("history candle belum cukup panjang")
    closes = [c[1] for c in base]
    lo, mid, hi = quantile(closes, 0.1), quantile(closes, 0.5), quantile(closes, 0.9)
    peak = max(c[1] for c in before)
    drop = (1 - mid / peak) * 100
    if hi / lo > cfg.dead_flat:
        return no(f"{cfg.dead_h:g}j sebelum surge gak flat (range {hi / lo:.1f}x, maks {cfg.dead_flat:g}x)")
    if drop < cfg.dead_drop_pct:
        return no(f"level mati cuma -{drop:.0f}% dari puncak (min -{cfg.dead_drop_pct:g}%)")
    breakout = (price / hi - 1) * 100
    if breakout < cfg.revival_pct:
        return no(f"baru {breakout:+.0f}% dari atap range mati (min +{cfg.revival_pct:g}%)")
    base_vol = sum(c[2] for c in base) / len(base)
    surge_vol = sum(c[2] for c in surge) / max(1, len(surge))
    if base_vol > 0 and surge_vol < cfg.revival_vol_mult * base_vol:
        return no(f"volume surge {surge_vol / base_vol:.1f}x volume pas mati (min {cfg.revival_vol_mult:g}x)")
    breakout_ts = next((c[0] for c in surge if c[1] > hi), surge_from)
    last_alive = max(c[0] for c in before + base if c[1] >= hi * 1.5) if peak >= hi * 1.5 else series[0][0]
    return Signal(peak, mid, breakout_ts, price, drop, (price / mid - 1) * 100,
                  kind="revival", dead_h=(breakout_ts - last_alive) / 3600)


def confirm(pair: dict, cfg: Config) -> list[str]:
    """Filter konfirmasi. Return alasan gagal (list kosong = lolos)."""
    fails = []
    vol = pair.get("volume") or {}
    v1, v6 = float(vol.get("h1") or 0), float(vol.get("h6") or 0)
    tx = (pair.get("txns") or {}).get("h1") or {}
    buys, sells = tx.get("buys") or 0, tx.get("sells") or 0
    if liq(pair) < cfg.min_liq_usd:
        fails.append(f"liq {usd(liq(pair))} < {usd(cfg.min_liq_usd)}")
    if v1 < cfg.min_vol_h1_usd:
        fails.append(f"vol 1j {usd(v1)} < {usd(cfg.min_vol_h1_usd)}")
    if v6 > 0 and v1 < cfg.vol_spike * v6 / 6:
        fails.append(f"vol spike {v1 / (v6 / 6):.1f}x < {cfg.vol_spike}x")
    if buys + sells == 0 or buys / (buys + sells) < cfg.min_buy_ratio:
        ratio = buys / (buys + sells) if buys + sells else 0
        fails.append(f"buy {ratio:.0%} < {cfg.min_buy_ratio:.0%}")
    return fails


def should_alert(last: dict | None, sig: Signal, now: float, cfg: Config) -> bool:
    if not last:
        return True
    if sig.price >= last["price"] * cfg.rerun_mult:  # masih lanjut terbang
        return True
    if sig.kind == "revival" == last.get("kind") and now - last["ts"] < cfg.dead_h * 3600:
        return False  # satu kebangkitan cukup sekali, titik breakout-nya bisa geser tiap jam
    new_episode = sig.low_ts != last.get("low_ts")
    return new_episode and now - last["ts"] >= cfg.cooldown_min * 60


# ---------------------------------------------------------------- format

def usd(x: float) -> str:
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(x) >= div:
            return f"${x / div:.1f}{suf}"
    return f"${x:.0f}"


def price_str(p: float) -> str:
    return f"${p:,.4f}" if p >= 1 else f"${p:.4g}"


def hours_str(h: float) -> str:
    return f"{h / 24:.1f} hari" if h >= 48 else f"{h:.0f} jam"


def changes_str(pair: dict) -> str:
    pc = pair.get("priceChange") or {}
    return " · ".join(f"{k} {pc[k]:+.0f}%" for k, _ in WINDOWS if pc.get(k) is not None)


def format_alert(pair: dict, sig: Signal, cfg: Config) -> str:
    vol = pair.get("volume") or {}
    v1, v6 = float(vol.get("h1") or 0), float(vol.get("h6") or 0)
    spike = f" ({v1 / (v6 / 6):.1f}x rata2)" if v6 else ""
    tx = (pair.get("txns") or {}).get("h1") or {}
    b, s = tx.get("buys") or 0, tx.get("sells") or 0
    fdv = pair.get("fdv") or pair.get("marketCap")
    sym = pair["baseToken"]["symbol"]
    if sig.kind == "pump":
        vs_peak = (f"puncak baru {cfg.lookback_h:g}j" if sig.price >= sig.peak else
                   f"masih {(sig.price / sig.peak - 1) * 100:.0f}% dari puncak {cfg.lookback_h:g}j "
                   f"{price_str(sig.peak)}")
        head = [f"🚀 {sym} lagi pump — {cfg.chain}",
                f"Naik +{sig.rebound:.0f}% dalam ≤{cfg.pump_window_min:g} menit "
                f"({price_str(sig.low)} → {price_str(sig.price)}), {vs_peak}"]
    elif sig.kind == "revival":
        mc = float(pair.get("marketCap") or fdv or 0)

        def at(p: float) -> str:
            return f" (mcap {usd(mc * p / sig.price)})" if mc else ""
        head = [f"💀→🚀 {sym} bangkit dari mati — {cfg.chain}",
                f"Puncak {price_str(sig.peak)}{at(sig.peak)} → mati ~{hours_str(sig.dead_h)} di "
                f"{price_str(sig.low)}{at(sig.low)}, -{sig.drop:.0f}%",
                f"Sekarang {price_str(sig.price)}{at(sig.price)}, +{sig.rebound:.0f}% dari level mati"]
    else:
        head = [f"🚀 {sym} bangkit abis jatoh — {cfg.chain}",
                f"Turun {sig.drop:.0f}% ({price_str(sig.peak)} → {price_str(sig.low)}), "
                f"sekarang mantul +{sig.rebound:.0f}% ke {price_str(sig.price)}"]
    return "\n".join(head + [
        f"Vol 1j {usd(v1)}{spike} · Buy {b}/{s} · Liq {usd(liq(pair))}"
        + (f" · FDV {usd(float(fdv))}" if fdv else ""),
        changes_str(pair),
        pair.get("url") or "",
        "⚠️ Bukan saran finansial. Cek kontrak, holder, & likuiditas dulu.",
    ])


# ---------------------------------------------------------------- state & loop

def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    for k in ("history", "alerts", "scan", "resolved", "gt_pool"):
        state.setdefault(k, {})
    return state


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def update_history(state: dict, addr: str, pair: dict, now: float, cfg: Config) -> list:
    price = float(pair.get("priceUsd") or 0)
    hist = state["history"].setdefault(addr, [])
    # baru pertama lihat / abis lama mati: isi dulu pakai harga hasil rekonstruksi
    if not hist or now - hist[-1][0] > 1800:
        last_ts = hist[-1][0] if hist else 0
        hist.extend([t, p] for t, p in implied_past_prices(pair, now) if t > last_ts)
    if price > 0:
        hist.append([now, price])
    hist[:] = [h for h in hist if now - h[0] <= cfg.lookback_h * 3600]
    return hist


def process(state: dict, addr: str, pair: dict, now: float, cfg: Config, notify,
            pump: bool = False, candles: list | None = None) -> Signal | None:
    hist = update_history(state, addr, pair, now, cfg)
    price = float(pair.get("priceUsd") or 0)
    sig = ((detect_revival(candles, price, now, cfg) if candles else None)
           or detect(hist, now, cfg) or (detect_pump(hist, now, cfg) if pump else None))
    if not sig or confirm(pair, cfg) or not should_alert(state["alerts"].get(addr), sig, now, cfg):
        return None
    notify(format_alert(pair, sig, cfg))
    state["alerts"][addr] = {"ts": now, "price": sig.price, "low_ts": sig.low_ts, "kind": sig.kind}
    return sig


def make_notifier():
    sinks = []
    tg_token, tg_chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        sinks.append(("telegram", lambda m: post_json(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            {"chat_id": tg_chat, "text": m, "disable_web_page_preview": True})))
    if discord := os.getenv("DISCORD_WEBHOOK_URL"):
        sinks.append(("discord", lambda m: post_json(discord, {"content": m})))
    if topic := os.getenv("NTFY_TOPIC"):
        sinks.append(("ntfy", lambda m: post(f"https://ntfy.sh/{topic}", m.encode(),
                                             {"Title": "Surge alert", "Tags": "rocket"})))

    def notify(msg: str) -> None:
        print(msg + "\n", flush=True)
        for name, send in sinks:
            try:
                send(msg)
            except Exception as e:
                warn(f"gagal kirim ke {name}: {e}")
    return notify


def read_watchlist(path: str) -> list[str]:
    """Satu token per baris (alamat 0x… atau simbol); teks setelah # diabaikan."""
    try:
        with open(path) as f:
            return [s for s in (line.split("#")[0].strip() for line in f) if s]
    except FileNotFoundError:
        return []


def resolve_all(items: list[str], state: dict, cfg: Config) -> list[str]:
    out = []
    for item in items:
        key = f"{cfg.chain}:{item.lower()}"
        if key not in state["resolved"]:
            addr = resolve(item, cfg.chain)
            if not addr:
                continue
            state["resolved"][key] = addr
        out.append(state["resolved"][key])
    return out


def check(items: list[str], cfg: Config) -> None:
    """Snapshot status sekarang (tanpa history, tanpa kirim alert)."""
    state = load_state(os.devnull)
    addrs = resolve_all(items, state, cfg)
    pairs = fetch_best_pairs(cfg.chain, addrs)
    now = time.time()
    for addr in addrs:
        pair = pairs.get(addr)
        if not pair:
            print(f"{addr}: gak ada pair di {cfg.chain}\n")
            continue
        price = float(pair.get("priceUsd") or 0)
        hist = update_history(state, addr, pair, now, cfg)
        candles, why = fetch_candles(cfg.chain, pair, cfg.history_days, state["gt_pool"]), []
        rev = detect_revival(candles, price, now, cfg, why) if candles else None
        sig = detect(hist, now, cfg) or detect_pump(hist, now, cfg)
        fails = confirm(pair, cfg)
        print(f"{pair['baseToken']['symbol']}  {addr}")
        print(f"  harga {price_str(price)} · liq {usd(liq(pair))} · {changes_str(pair)}")
        if rev:
            print(f"  bangkit dari mati: puncak {price_str(rev.peak)} → mati ~{hours_str(rev.dead_h)} "
                  f"(-{rev.drop:.0f}%) → +{rev.rebound:.0f}% ✅")
        else:
            print(f"  bangkit dari mati: {why[0] if why else 'gak ada data candle GeckoTerminal'}")
        if sig and sig.kind == "dip":
            print(f"  abis jatoh: turun {sig.drop:.0f}% lalu mantul +{sig.rebound:.0f}% ✅")
        elif sig:
            print(f"  lagi pump: +{sig.rebound:.0f}% dari low {cfg.pump_window_min:g} menit ✅")
        else:
            print("  abis jatoh / pump: belum")
        print(f"  konfirmasi: {'lolos ✅' if not fails else '; '.join(fails)}")
        print(f"  status: {'🚀 ALERT' if (rev or sig) and not fails else 'belum'}")
        print(f"  {pair.get('url', '')}\n")


def run(args, cfg: Config) -> None:
    state = load_state(args.state)
    notify = make_notifier()
    watch = resolve_all(args.watch, state, cfg)
    if not watch and not args.scan:
        sys.exit("Gak ada token yang bisa dipantau. Cek simbol/alamat atau tambah --scan.")
    print(f"Pantau {len(watch)} token di '{cfg.chain}'" + (" + scan" if args.scan else "")
          + f", tiap {args.interval}s. Ctrl+C buat stop.", flush=True)
    candle_cache: dict[str, tuple[float, list]] = {}
    while True:
        now = time.time()
        if args.scan and now - state.get("last_discover", 0) >= DISCOVER_EVERY_S:
            for a in discover(cfg.chain):
                state["scan"][a] = now
            state["last_discover"] = now
            newest = sorted(state["scan"].items(), key=lambda kv: -kv[1])[:MAX_SCAN_TOKENS]
            state["scan"] = {a: t for a, t in newest if now - t <= cfg.lookback_h * 3600}
        tracked = sorted(set(watch) | set(state["scan"]))
        pairs = fetch_best_pairs(cfg.chain, tracked)
        candle_cache = {a: v for a, v in candle_cache.items() if now - v[0] < CANDLE_TTL_S}
        budget = GT_OHLCV_PER_LOOP
        # yang naiknya paling kenceng duluan, biar kebagian jatah candle
        for addr, pair in sorted(pairs.items(), key=lambda kv: -((kv[1].get("priceChange") or {}).get("h1") or 0)):
            if looks_like_surge(pair) and not confirm(pair, cfg) and addr not in candle_cache and budget > 0:
                budget -= 1
                candle_cache[addr] = (now, fetch_candles(cfg.chain, pair, cfg.history_days, state["gt_pool"]))
            candles = candle_cache[addr][1] if addr in candle_cache and looks_like_surge(pair) else None
            process(state, addr, pair, now, cfg, notify, pump=addr in watch, candles=candles)
        for key in ("history", "gt_pool"):
            state[key] = {a: v for a, v in state[key].items() if a in tracked}
        save_state(args.state, state)
        if args.once:
            return
        time.sleep(args.interval)


def main(argv=None) -> None:
    d = Config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", nargs="*", metavar="TOKEN",
                      help="cek status sekarang lalu keluar (tanpa TOKEN = isi watchlist)")
    mode.add_argument("--watch", nargs="*", metavar="TOKEN",
                      help="simbol atau alamat 0x… (tanpa TOKEN = isi watchlist)")
    ap.add_argument("--watchlist", default=WATCHLIST, help="file daftar token (default watchlist.txt)")
    ap.add_argument("--scan", action="store_true", help="ikut pantau token baru (profile/boost) di chain")
    ap.add_argument("--once", action="store_true", help="jalan sekali aja (buat cron)")
    ap.add_argument("--interval", type=int, default=60, help="detik antar polling (default 60)")
    ap.add_argument("--state", default="surge_state.json")
    ap.add_argument("--chain", default=d.chain, help=f"chainId DexScreener (default {d.chain})")
    ap.add_argument("--drop", type=float, default=d.drop_pct, help=f"min %% turun (default {d.drop_pct:g})")
    ap.add_argument("--rebound", type=float, default=d.rebound_pct, help=f"min %% mantul (default {d.rebound_pct:g})")
    ap.add_argument("--lookback", type=float, default=d.lookback_h, help=f"jam (default {d.lookback_h:g})")
    ap.add_argument("--low-age", type=float, default=d.max_low_age_min, help=f"menit (default {d.max_low_age_min:g})")
    ap.add_argument("--min-liq", type=float, default=d.min_liq_usd)
    ap.add_argument("--min-vol", type=float, default=d.min_vol_h1_usd, help="min volume 1 jam USD")
    ap.add_argument("--vol-spike", type=float, default=d.vol_spike)
    ap.add_argument("--min-buy", type=float, default=d.min_buy_ratio, help="0-1")
    ap.add_argument("--cooldown", type=float, default=d.cooldown_min, help="menit")
    ap.add_argument("--pump", type=float, default=d.pump_pct,
                    help=f"watchlist: min %% naik buat alert pump (default {d.pump_pct:g})")
    ap.add_argument("--pump-window", type=float, default=d.pump_window_min,
                    help=f"menit (default {d.pump_window_min:g})")
    ap.add_argument("--dead-hours", type=float, default=d.dead_h,
                    help=f"bangkit dari mati: min jam flat sebelum surge (default {d.dead_h:g})")
    ap.add_argument("--dead-drop", type=float, default=d.dead_drop_pct,
                    help=f"bangkit dari mati: min %% level mati di bawah puncak (default {d.dead_drop_pct:g})")
    ap.add_argument("--revival", type=float, default=d.revival_pct,
                    help=f"bangkit dari mati: min %% di atas range mati (default {d.revival_pct:g})")
    args = ap.parse_args(argv)
    cfg = Config(chain=args.chain, drop_pct=args.drop, rebound_pct=args.rebound,
                 lookback_h=args.lookback, max_low_age_min=args.low_age, min_liq_usd=args.min_liq,
                 min_vol_h1_usd=args.min_vol, vol_spike=args.vol_spike, min_buy_ratio=args.min_buy,
                 cooldown_min=args.cooldown, pump_pct=args.pump, pump_window_min=args.pump_window,
                 dead_h=args.dead_hours, dead_drop_pct=args.dead_drop, revival_pct=args.revival)
    try:
        if args.check is not None:
            items = args.check or read_watchlist(args.watchlist)
            if not items:
                sys.exit(f"Gak ada token buat dicek. Isi {args.watchlist} atau kasih TOKEN.")
            check(items, cfg)
        else:
            args.watch = args.watch or read_watchlist(args.watchlist)
            run(args, cfg)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
