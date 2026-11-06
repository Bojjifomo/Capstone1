"""Top-N pool blast: one Telegram message every N minutes, laid out like the
"DLMM Top 10 MEME Fees Opportunities" card (legend, then one block per pool).

Pools come from the `signal` scan in cli.py (v1 gates + benchmark habitat); this module only
adds the short-window market data (DexScreener) and formats the message.
"""
from __future__ import annotations

import html
import json
import time
import urllib.request

from . import api

DEX = "https://api.dexscreener.com/latest/dex/pairs/solana/"

LEGEND = [
    ("🧹", "Avg 1d Fees / TVL"),
    ("📌", "FDV"),
    ("📊", "TVL"),
    ("📐", "5m Vol"),
    ("⏳", "5m Price Change"),
    ("🟥", "Bin Step"),
    ("🥩", "1hr Vol / TVL"),
    ("🥤", "Prorated 5m Vol / 1hr Vol"),
    ("⚡", "DEX Boost"),
    ("🟢", "5m Buys"),
    ("🔴", "5m Sells"),
    ("♻️", "5m Txn"),
    ("🫀", "Active hours (24h)"),
    ("⏰", "Pool Created At"),
]

SORT_KEYS = {
    "volTvl1h": "1h Vol/TVL",
    "feeTvl24h": "1d Fees/TVL",
    "fees1hUsd": "1h fees",
    "prorated5m": "prorated 5m Vol / 1h Vol",
}


def dex_5m(ids: list[str]) -> dict:
    """DexScreener per pool: 5m volume / price change / buys / sells, 1h volume, boosts.
    A failed batch only costs those columns."""
    out = {}
    for i in range(0, len(ids), 30):
        req = urllib.request.Request(DEX + ",".join(ids[i:i + 30]),
                                     headers={"User-Agent": "dlmm-terminal/1", "Accept": "application/json"})
        try:
            with api.urlopen(req, timeout=10) as r:
                d = json.load(r)
        except Exception:
            continue
        for p in d.get("pairs") or []:
            tx5 = (p.get("txns") or {}).get("m5") or {}
            vol, pc = p.get("volume") or {}, p.get("priceChange") or {}
            out[p.get("pairAddress")] = {
                "vol5m": vol.get("m5"), "vol1h": vol.get("h1"), "chg5m": pc.get("m5"),
                "buys5m": tx5.get("buys"), "sells5m": tx5.get("sells"),
                "boost": (p.get("boosts") or {}).get("active") or 0,
                "fdv": p.get("fdv"), "dsUrl": p.get("url"),
            }
    return out


TOKEN_PAIRS = "https://api.dexscreener.com/token-pairs/v1/solana/"


def token_stats(mints: list[str], now: float) -> dict:
    """DexScreener, per token over all its pairs: 24h volume (deduped by pair) and age since its
    first pair. One request per token (the multi-token endpoint caps the pairs it returns)."""
    out = {}
    for mint in dict.fromkeys(m for m in mints if m):
        req = urllib.request.Request(TOKEN_PAIRS + mint, headers={"User-Agent": "dlmm-terminal/1", "Accept": "application/json"})
        try:
            with api.urlopen(req, timeout=10) as r:
                pairs = json.load(r) or []
        except Exception:
            continue
        seen, vol, first = set(), 0.0, None
        for p in pairs:
            if p.get("pairAddress") in seen:
                continue
            seen.add(p.get("pairAddress"))
            vol += (p.get("volume") or {}).get("h24") or 0
            t = p.get("pairCreatedAt")
            first = t if t and (first is None or t < first) else first
        if seen:
            out[mint] = {"volume24hUsd": round(vol, 2), "ageHours": round((now - first / 1000) / 3600, 2) if first else None,
                         "pairs": len(seen)}
    return out


def row(c: dict, snap: dict, m: dict, dx: dict | None, now: float) -> dict:
    """One pool's numbers for the card. c = filters.evaluate row, snap = Meteora pool snapshot."""
    dx = dx or {}
    tvl = snap.get("tvl") or 0
    vol1h_met = (snap.get("volume") or {}).get("1h") or 0
    vol1h = dx.get("vol1h") if dx.get("vol1h") is not None else vol1h_met
    vol5m = dx.get("vol5m")
    buys, sells = dx.get("buys5m"), dx.get("sells5m")
    return {
        "pool": c["pool"], "name": c["name"] or c["symbol"], "symbol": c["symbol"],
        "feeTvl24h": c["fees24hUsd"] / tvl * 100 if tvl else 0,
        "fdv": dx.get("fdv") or c.get("mcapUsd"),
        "tvl": tvl,
        "vol5m": vol5m,
        "chg5m": dx.get("chg5m"),
        "binStep": c["binStep"],
        "volTvl1h": vol1h_met / tvl * 100 if tvl else 0,
        "prorated5m": vol5m * 12 / vol1h * 100 if vol5m is not None and vol1h else None,
        "score": m["score"], "dims": m["dims"], "hits": len(m["hits"]),
        "boost": dx.get("boost", 0),
        "buys5m": buys, "sells5m": sells,
        "txn5m": (buys or 0) + (sells or 0) if buys is not None or sells is not None else None,
        "ageH": (now - (snap.get("created_at") or 0) / 1000) / 3600,
        "fees1hUsd": c.get("fees1hUsd") or 0,
        "activeHours": (c.get("activity") or {}).get("activeHours"),
        "volume24hUsd": c.get("volume24hUsd"), "totalFeesSol": c.get("totalFeesSol"),
        "hours": (c.get("activity") or {}).get("hours"),
        "flags": c["flags"],
        "dsUrl": dx.get("dsUrl") or f"https://dexscreener.com/solana/{c['pool']}",
    }


def top(rows: list[dict], n: int, sort_by: str) -> list[dict]:
    return sorted(rows, key=lambda r: -(r.get(sort_by) or 0))[:n]


def _usd(v) -> str:
    return "n/a" if v is None else f"${v:,.0f}"


def _short(v) -> str:
    if v is None:
        return "n/a"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.1f}{suf}"
    return f"{v:.0f}"


def _pct(v, sign=False) -> str:
    return "n/a" if v is None else (f"{v:+.2f}%" if sign else f"{v:,.2f}%")


def ago(hours: float) -> str:
    for size, unit in ((24 * 30, "month"), (24 * 7, "week"), (24, "day"), (1, "hour")):
        if hours >= size:
            n = int(hours // size)
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return f"{int(hours * 60)} minutes ago"


def _line(r: dict, link: bool) -> str:
    e = html.escape
    name = (f'<b><a href="https://app.meteora.ag/dlmm/{r["pool"]}">{e(r["name"])}</a></b>' if link
            else r["name"])
    ds = f'<a href="{e(r["dsUrl"])}">DS</a>' if link else "DS"
    out = (f"{name} 🧹 {_pct(r['feeTvl24h'])} 📌 {_short(r['fdv'])} 📊 {_usd(r['tvl'])} "
           f"📐 {_usd(r['vol5m'])} ⏳ {_pct(r['chg5m'], True)} 🟥 {r['binStep']} "
           f"🥩 {_pct(r['volTvl1h'])} 🥤 {_pct(r['prorated5m'])} "
           f"⚡ {r['boost']} 🟢 {r['buys5m'] if r['buys5m'] is not None else 'n/a'} "
           f"🔴 {r['sells5m'] if r['sells5m'] is not None else 'n/a'} "
           f"♻️ {r['txn5m'] if r['txn5m'] is not None else 'n/a'} "
           + (f"🫀 {r['activeHours']}/{r['hours']}h " if r.get("activeHours") is not None else "")
           + f"⏰ {ago(r['ageH'])} · {ds}")
    if r["flags"]:
        flags = " · ".join(r["flags"])
        out += f"\n⚠️ {e(flags) if link else flags}"
    return out


LABELS = {"tokenAgeHours": "token age", "poolAgeHours": "pool age", "mcapUsd": "mcap", "volume24hUsd": "vol 24h",
          "totalFeesSol": "total fees", "fees24hUsd": "fees 24h", "fees1hUsd": "fees 1h"}


def layer2(sig: dict) -> str:
    """'age 1h–250h, mcap $400.0k–$10.00M, ...' from signal.matchDims + bandOverrides."""
    from .benchmark import _band_txt
    ov = sig.get("bandOverrides") or {}
    parts = [f"{LABELS.get(d, d)} {_band_txt(d, ov[d])}" for d in sig.get("matchDims") or [] if isinstance(ov.get(d), dict)]
    return ", ".join(parts) if parts else "no GMGN bands"


def filter_text(cfg: dict, prof: dict, n: int, total: int, revived: int = 0) -> str:
    """'Filtered by: SOL pair, bin 20-250, ... Top 10 of 12, sorted by 1h Vol/TVL.'"""
    sig, g, u = cfg["signal"], cfg["gates"], cfg["universe"]
    wallet = prof.get("wallet") or ""
    return (f"Filtered by: SOL pair, bin {u['binStepMin']}-{u['binStepMax']}, pool age ≥ {g['minPoolAgeMinutes']:g}m, "
            f"fee APR 24h ≥ {g['minFeeApr24hPct']:,.0f}%, {layer2(sig)}"
            + (f", bin steps of {wallet[:6]}…{wallet[-4:]} {prof['days']:g}d" if sig.get("requireBenchmarkBinSteps") and wallet else "")
            + ". "
            + (f"Excluded {revived} dead→alive pool(s). " if revived else "")
            + f"Top {n} of {total}, sorted by {SORT_KEYS.get(sig.get('blastSortBy'), sig.get('blastSortBy'))}.")


def message(rows: list[dict], total: int, cfg: dict, prof: dict, html_mode: bool, revived: int = 0) -> str:
    """The card. html_mode=True for Telegram (parse_mode=HTML), False for the terminal."""
    sig, g, u = cfg["signal"], cfg["gates"], cfg["universe"]
    b = lambda s: f"<b>{s}</b>" if html_mode else s
    if not rows:
        return b("DLMM Top Fees Opportunities") + "\nNo pool passes the v1 gates + benchmark right now."
    lead = rows[0]
    head = f"MEME - {lead['name']} {_pct(lead['feeTvl24h'])}"
    legend = "\n".join(f"{icon} {label}" for icon, label in LEGEND)
    body = "\n\n".join(_line(r, html_mode) for r in rows)
    foot = filter_text(cfg, prof, len(rows), total, revived)
    return "\n\n".join([head, b(f"DLMM Top {len(rows)} MEME Fees Opportunities"), legend, body, b(foot) if html_mode else foot])
