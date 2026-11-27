"""Settings you change from the web (⚙): a flat overlay on config.json in data/settings.json.

Every command (web, screen, portfolio) loads config.json, then .env sizing, then this overlay,
so a change made in the browser is what the terminal uses too. Only the keys in SCHEMA can be set.
Delete data/settings.json (or press Reset in the web) to go back to config.json.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "settings.json"

# (dotted path, group, label, kind, extra). kind: num | int | bool | select | numnull (empty = off/no limit).
SCHEMA = [
    ("position.defaultSizeUsd", "Ladder", "Size default ($)", "num", {"min": 1}),
    ("position.ladderSlices", "Ladder", "Jumlah slice", "int", {"min": 1, "max": 100}),
    ("position.rangeTopPct", "Ladder", "Mulai dari (% di bawah harga, 0 = nempel)", "num", {"min": 0, "max": 98}),
    ("position.rangeBottomPct", "Ladder", "Range bottom (%, negatif)", "num", {"min": -99, "max": -1}),
    ("position.bankrollUsd", "Risk", "Bankroll LP ($)", "num", {"min": 1}),
    ("position.lossBudgetPct", "Risk", "Loss budget per posisi (% bankroll)", "num", {"min": 0.1, "max": 100}),
    ("trade.maxLossPct", "Risk", "Swap: batal kalau di bawah harga pool > (%)", "num", {"min": 0.5, "max": 50}),
    ("filterPreset", "Gate", "Preset filter: elysium = angka Elysium (default) · dlmm = angka DLMM Solana (ketat)", "select", {"options": ["elysium", "dlmm"]}),
    ("universe.minLpFeePct", "Gate", "Fee LP min (%)", "num", {"min": 0}),
    ("universe.maxLpFeePct", "Gate", "Fee LP maks (%) (di atas = honeypot)", "num", {"min": 0.01, "max": 100}),
    ("gates.minPoolAgeMinutes", "Gate", "Umur pool min (menit)", "num", {"min": 0}),
    ("gates.minTvlUsd", "Gate", "TVL min ($)", "num", {"min": 0}),
    ("gates.minMcapUsd", "Gate", "Mcap min ($) (kosong = off)", "numnull", {"min": 0}),
    ("gates.minFeePctDay", "Gate", "Fee 24h / TVL min (%/hari; 2.74 = APR 1000%) (kosong = off)", "numnull", {"min": 0}),
    ("gates.maxPriceChg1hPct", "Gate", "Harga naik 1 jam maks (%) → WAIT, tunggu dip (kosong = off)", "numnull", {}),
    ("flags.pump1hPct", "Flag", "Pump 1h ≥ (%)", "numnull", {}),
    ("flags.drop6hPct", "Flag", "Drop 6h ≤ (%)", "numnull", {}),
    ("flags.minTvlUsd", "Flag", "TVL tipis < ($)", "numnull", {"min": 0}),
    ("flags.sellsShare1hPct", "Flag", "Seller ≥ (% trade 1 jam)", "numnull", {"min": 0, "max": 100}),
    ("flags.youngPoolHours", "Flag", "Pool muda < (jam) (kosong = off)", "numnull", {"min": 0}),
    ("flags.volMcapBand.0", "Flag", "Vol 24h / mcap normal dari (x)", "num", {"min": 0}),
    ("flags.volMcapBand.1", "Flag", "Vol 24h / mcap normal sampai (x)", "num", {"min": 0}),
    ("flags.hookPool", "Flag", "Tandai pool ber-hook", "bool", {}),
    ("flags.cloneSymbol", "Flag", "Tandai token kloningan (simbol sama, kontrak beda)", "bool", {}),
    ("ping.top", "PING", "Top N", "int", {"min": 1, "max": 50}),
    ("ping.sortBy", "PING", "Urut berdasarkan", "select", {"options": ["volMcapFit", "fee1hPct", "fees1hUsd", "volTvl1h", "fee24hPct"]}),
    ("ping.mode", "PING", "Telegram: new = per pool baru, blast = top N tiap X menit", "select", {"options": ["new", "blast"]}),
    ("ping.blastEveryMin", "PING", "Blast tiap (menit)", "int", {"min": 5, "max": 720}),
    ("ping.minVol24hUsd", "PING", "Volume 24h min ($)", "numnull", {"min": 0}),
    ("ping.minTvlUsd", "PING", "TVL min ($)", "numnull", {"min": 0}),
    ("ping.minMcapUsd", "PING", "Mcap min ($)", "numnull", {"min": 0}),
    ("ping.maxMcapUsd", "PING", "Mcap maks ($)", "numnull", {"min": 0}),
    ("ping.minPoolAgeHours", "PING", "Umur pool min (jam)", "numnull", {"min": 0}),
    ("ping.maxPoolAgeHours", "PING", "Umur pool maks (jam)", "numnull", {"min": 0}),
    ("ping.minFees1hUsd", "PING", "Fee 1 jam min ($)", "numnull", {"min": 0}),
    ("ping.requireGates", "PING", "Wajib lolos gate", "bool", {}),
    ("ping.skipClones", "PING", "Buang token kloningan dari PING", "bool", {}),
    ("exits.alertAtPnlPct", "Exit", "Alert kalau PnL ≤ (%)", "numnull", {}),
    ("exits.alertBottomThirdOfRange", "Exit", "Alert di 1/3 bawah range", "bool", {}),
    ("exits.alertFullyConverted", "Exit", "Alert kalau harga lewat semua slice", "bool", {}),
    ("exits.takeProfitPct", "Exit", "Take profit (%) (kosong = off)", "numnull", {}),
    ("exits.stopLossPct", "Exit", "Stop loss (%, negatif) (kosong = off)", "numnull", {}),
    ("scan.maxPools", "Web", "Pool yang dicek DexScreener (top by fee 1h)", "int", {"min": 30, "max": 900}),
    ("web.refreshMin", "Web", "Refresh pool + portfolio tiap (menit)", "int", {"min": 1, "max": 120}),
    ("web.autoExitCheckMin", "Web", "Cek rule auto (TP, SL, keluar range) tiap (menit)", "int", {"min": 1, "max": 60}),
]
KEYS = {p: (kind, extra) for p, _, _, kind, extra in SCHEMA}

# DLMM Terminal's filter numbers (Solana, owner reviews Nov 2026). "elysium" in config.json is the
# default; "dlmm" applies these on top of it. A value you set in ⚙ wins over either.
PRESETS = {
    "dlmm": {
        "gates.minFeePctDay": 2.74, "gates.maxPriceChg1hPct": 5, "gates.minPoolAgeMinutes": 60,
        "flags.youngPoolHours": 6, "flags.volMcapBand.0": 0.3, "flags.volMcapBand.1": 3,
        "flags.pump1hPct": 25, "flags.drop6hPct": -17, "flags.minTvlUsd": 25000,
        "ping.sortBy": "volMcapFit", "ping.minMcapUsd": 400000, "ping.maxMcapUsd": 10000000,
        "ping.maxPoolAgeHours": 250, "ping.minVol24hUsd": 50000, "ping.top": 10,
    },
}


def get_path(cfg: dict, path: str):
    cur = cfg
    for k in path.split("."):
        try:
            cur = cur[int(k) if k.isdigit() else k]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def set_path(cfg: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = cfg
    for k, nxt in zip(parts[:-1], parts[1:]):
        if isinstance(cur, list):                     # e.g. flags.volMcapBand.0
            cur = cur[int(k)]
            continue
        if not isinstance(cur.get(k), (dict, list)):
            cur[k] = [None, None] if nxt.isdigit() else {}
        cur = cur[k]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def load() -> dict:
    try:
        d = json.loads(PATH.read_text())
        return {k: v for k, v in d.items() if k in KEYS}
    except (OSError, ValueError):
        return {}


def apply(cfg: dict, overlay: dict | None = None) -> dict:
    ov = load() if overlay is None else overlay
    preset = ov.get("filterPreset", cfg.get("filterPreset"))
    for path, v in PRESETS.get(preset, {}).items():      # preset first, then what you set yourself
        set_path(cfg, path, v)
    for path, v in ov.items():
        if path in KEYS:
            set_path(cfg, path, v)
    return cfg


def clean(path: str, raw):
    """Value from the form -> typed value, or ValueError with a readable reason."""
    kind, ex = KEYS[path]
    if kind == "bool":
        return bool(raw)
    if kind == "select":
        if raw not in ex["options"]:
            raise ValueError(f"{path}: pilih salah satu {ex['options']}")
        return raw
    if raw in (None, ""):
        if kind == "numnull":
            return None
        raise ValueError(f"{path}: wajib diisi")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{path}: {raw!r} bukan angka") from None
    if kind == "int":
        v = int(round(v))
    if "min" in ex and v < ex["min"] or "max" in ex and v > ex["max"]:
        raise ValueError(f"{path}: {v} di luar {ex.get('min', '-∞')}..{ex.get('max', '∞')}")
    return v


def save(base_cfg: dict, changes: dict) -> dict:
    """Validate `changes` ({path: raw}), keep only values that differ from config.json (+ .env), write."""
    overlay = load()
    for path in changes:
        if path not in KEYS:
            raise ValueError(f"{path}: nggak bisa diubah dari sini")
    if "filterPreset" in changes:
        v = clean("filterPreset", changes["filterPreset"])
        if v == base_cfg.get("filterPreset"):
            overlay.pop("filterPreset", None)
        else:
            overlay["filterPreset"] = v
    # a value equal to what the chosen preset gives is not an override
    ref = apply(json.loads(json.dumps(base_cfg)), {"filterPreset": overlay.get("filterPreset", base_cfg.get("filterPreset"))})
    for path, raw in changes.items():
        if path == "filterPreset":
            continue
        v = clean(path, raw)
        if v == get_path(ref, path):
            overlay.pop(path, None)
        else:
            overlay[path] = v
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(overlay, indent=1))
    return overlay


def reset() -> None:
    try:
        PATH.unlink()
    except OSError:
        pass


def form(cfg: dict, base_cfg: dict) -> list[dict]:
    return [{"path": p, "group": g, "label": lab, "kind": kind, **extra,
             "value": get_path(cfg, p), "base": get_path(base_cfg, p)} for p, g, lab, kind, extra in SCHEMA]
