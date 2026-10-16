#!/usr/bin/env python3
"""Alert koin yang "surging abis jatoh" (dip -> rebound) di Robinhood Chain.

Data dari DexScreener public API (gratis, tanpa API key). Cuma pakai stdlib.

Pola yang dicari:
  1. Harga turun >= --drop % dari puncak ke dasar (dalam --lookback jam)
  2. Dasarnya masih baru (<= --low-age menit lalu)
  3. Harga sekarang udah mantul >= --rebound % dari dasar
  4. Dikonfirmasi: likuiditas cukup, volume 1 jam spike, buy > sell

Contoh:
  python surge_alert.py --check IGNITION AGEM          # cek status sekarang
  python surge_alert.py --watch IGNITION AGEM          # pantau terus, kirim alert
  python surge_alert.py --watch IGNITION AGEM --scan   # + scan token baru di chain
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


@dataclass
class Signal:
    peak: float
    low: float
    low_ts: float
    price: float
    drop: float
    rebound: float


def warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr, flush=True)


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
    return found


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
    return "\n".join([
        f"🚀 {pair['baseToken']['symbol']} bangkit abis jatoh — {cfg.chain}",
        f"Turun {sig.drop:.0f}% ({price_str(sig.peak)} → {price_str(sig.low)}), "
        f"sekarang mantul +{sig.rebound:.0f}% ke {price_str(sig.price)}",
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
    for k in ("history", "alerts", "scan", "resolved"):
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


def process(state: dict, addr: str, pair: dict, now: float, cfg: Config, notify) -> Signal | None:
    sig = detect(update_history(state, addr, pair, now, cfg), now, cfg)
    if not sig or confirm(pair, cfg) or not should_alert(state["alerts"].get(addr), sig, now, cfg):
        return None
    notify(format_alert(pair, sig, cfg))
    state["alerts"][addr] = {"ts": now, "price": sig.price, "low_ts": sig.low_ts}
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
        hist = update_history(state, addr, pair, now, cfg)
        sig, fails = detect(hist, now, cfg), confirm(pair, cfg)
        print(f"{pair['baseToken']['symbol']}  {addr}")
        print(f"  harga {price_str(float(pair.get('priceUsd') or 0))} · liq {usd(liq(pair))} · "
              f"{changes_str(pair)}")
        if sig:
            print(f"  pola: turun {sig.drop:.0f}% lalu mantul +{sig.rebound:.0f}% ✅")
        else:
            print("  pola: belum ada dip -> rebound yang cukup")
        print(f"  konfirmasi: {'lolos ✅' if not fails else '; '.join(fails)}")
        print(f"  status: {'🚀 ALERT' if sig and not fails else 'belum'}")
        print(f"  {pair.get('url', '')}\n")


def run(args, cfg: Config) -> None:
    state = load_state(args.state)
    notify = make_notifier()
    watch = resolve_all(args.watch, state, cfg)
    if not watch and not args.scan:
        sys.exit("Gak ada token yang bisa dipantau. Cek simbol/alamat atau tambah --scan.")
    print(f"Pantau {len(watch)} token di '{cfg.chain}'" + (" + scan" if args.scan else "")
          + f", tiap {args.interval}s. Ctrl+C buat stop.", flush=True)
    while True:
        now = time.time()
        if args.scan and now - state.get("last_discover", 0) >= DISCOVER_EVERY_S:
            for a in discover(cfg.chain):
                state["scan"][a] = now
            state["last_discover"] = now
            newest = sorted(state["scan"].items(), key=lambda kv: -kv[1])[:MAX_SCAN_TOKENS]
            state["scan"] = {a: t for a, t in newest if now - t <= cfg.lookback_h * 3600}
        tracked = sorted(set(watch) | set(state["scan"]))
        for addr, pair in fetch_best_pairs(cfg.chain, tracked).items():
            process(state, addr, pair, now, cfg, notify)
        for addr in list(state["history"]):
            if addr not in tracked:
                del state["history"][addr]
        save_state(args.state, state)
        if args.once:
            return
        time.sleep(args.interval)


def main(argv=None) -> None:
    d = Config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", nargs="+", metavar="TOKEN", help="cek status sekarang lalu keluar")
    mode.add_argument("--watch", nargs="*", default=[], metavar="TOKEN", help="simbol atau alamat 0x…")
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
    args = ap.parse_args(argv)
    cfg = Config(args.chain, args.drop, args.rebound, args.lookback, args.low_age, args.min_liq,
                 args.min_vol, args.vol_spike, args.min_buy, args.cooldown)
    try:
        if args.check:
            check(args.check, cfg)
        else:
            run(args, cfg)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
