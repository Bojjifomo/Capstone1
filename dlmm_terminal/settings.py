"""Settings you change from the web (⚙): a flat overlay on config.json in data/settings.json.

Every command (web, monitor, signal, open) loads config.json, then .env sizing, then this overlay,
so a change made in the browser is what the terminal uses too. Only the keys in SCHEMA can be set.
Delete data/settings.json (or press Reset in the web) to go back to config.json.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "settings.json"

# (dotted path, group, label, kind, extra). kind: num | int | bool | select | numnull (empty = off/no limit).
# A path segment that is a number indexes a list (flags.nightEntryLocalHours.0).
SCHEMA = [
    ("position.defaultSizeSol", "Ladder", "Size default (SOL)", "num", {"min": 0.01}),
    ("position.ladderSlices", "Ladder", "Jumlah slice", "int", {"min": 1, "max": 20}),
    ("position.rangeBottomPct", "Ladder", "Range bottom (%, negatif)", "num", {"min": -99, "max": -1}),
    ("position.strategy", "Ladder", "Bentuk", "select", {"options": ["Spot", "Curve", "BidAsk"]}),
    ("position.bankrollSol", "Risk", "Bankroll LP (SOL)", "num", {"min": 0.1}),
    ("position.lossBudgetPct", "Risk", "Loss budget per posisi (% bankroll)", "num", {"min": 0.1, "max": 100}),
    ("trade.maxLossPct", "Risk", "Swap: batal kalau di bawah harga pool > (%)", "num", {"min": 0.5, "max": 50}),
    ("universe.binStepMin", "Gate v1", "Bin step min", "int", {"min": 1}),
    ("universe.binStepMax", "Gate v1", "Bin step max", "int", {"min": 1}),
    ("gates.minPoolAgeMinutes", "Gate v1", "Umur pool min (menit)", "num", {"min": 0}),
    ("gates.minFeeApr24hPct", "Gate v1", "Fee APR 24h min (%)", "num", {"min": 0}),
    ("gates.maxPositionPctOfTvl", "Gate v1", "Size maks % TVL (kosong = off)", "numnull", {"min": 0}),
    ("flags.pump1hPct", "Flag", "Pump 1h ≥ (%)", "num", {}),
    ("flags.drop6hPct", "Flag", "Drop 6h ≤ (%)", "num", {}),
    ("flags.minTvlUsd", "Flag", "TVL tipis < ($)", "num", {"min": 0}),
    ("flags.nightEntryLocalHours.0", "Flag", "Jam malam dari (WIB)", "int", {"min": 0, "max": 23}),
    ("flags.nightEntryLocalHours.1", "Flag", "Jam malam sampai (WIB)", "int", {"min": 0, "max": 24}),
    ("signal.bandOverrides.tokenAgeHours.lo", "PING", "Umur token min (jam)", "numnull", {"min": 0}),
    ("signal.bandOverrides.tokenAgeHours.hi", "PING", "Umur token maks (jam)", "numnull", {"min": 0}),
    ("signal.bandOverrides.mcapUsd.lo", "PING", "Mcap min ($)", "numnull", {"min": 0}),
    ("signal.bandOverrides.mcapUsd.hi", "PING", "Mcap maks ($)", "numnull", {"min": 0}),
    ("signal.bandOverrides.volume24hUsd.lo", "PING", "Volume 24h min ($)", "numnull", {"min": 0}),
    ("signal.bandOverrides.totalFeesSol.lo", "PING", "Total fees pool min (SOL)", "numnull", {"min": 0}),
    ("signal.blastTop", "PING", "Top N", "int", {"min": 1, "max": 50}),
    ("signal.blastSortBy", "PING", "Urut berdasarkan", "select", {"options": ["volTvl1h", "feeTvl24h", "fees1hUsd", "prorated5m"]}),
    ("signal.revival.mode", "PING", "Pool mati→hidup", "select", {"options": ["gate", "flag", "off"]}),
    ("exits.alertAtPnlPct", "Exit", "Alert kalau PnL ≤ (%)", "numnull", {}),
    ("exits.alertBottomThirdOfRange", "Exit", "Alert di 1/3 bawah range", "bool", {}),
    ("exits.closeBelowRangeAfterMin", "Exit", "Close setelah N menit di bawah range (kosong = off)", "numnull", {"min": 0}),
    ("exits.takeProfitPct", "Exit", "Take profit (%) (kosong = off)", "numnull", {}),
    ("exits.stopLossPct", "Exit", "Stop loss (%, negatif) (kosong = off)", "numnull", {}),
    ("web.refreshMin", "Web", "Refresh pool + portfolio tiap (menit)", "int", {"min": 1, "max": 120}),
    ("web.autoExitCheckMin", "Web", "Cek auto exit (TP) tiap (menit, tanpa RPC)", "int", {"min": 1, "max": 60}),
]
KEYS = {p: (kind, extra) for p, _, _, kind, extra in SCHEMA}
DEFAULTS = {"trade": {"maxLossPct": 5}, "web": {"refreshMin": 15, "autoExitCheckMin": 2}}


def _seg(k: str):
    return int(k) if k.isdigit() else k


def get_path(cfg: dict, path: str):
    cur = cfg
    for k in path.split("."):
        k = _seg(k)
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def set_path(cfg: dict, path: str, value) -> None:
    parts = [_seg(k) for k in path.split(".")]
    cur = cfg
    for k, nxt in zip(parts, parts[1:]):
        if isinstance(cur, list):
            cur = cur[k]
            continue
        if not isinstance(cur.get(k), (dict, list)):
            cur[k] = [] if isinstance(nxt, int) else {}
        cur = cur[k]
    cur[parts[-1]] = value


def load() -> dict:
    try:
        d = json.loads(PATH.read_text())
        return {k: v for k, v in d.items() if k in KEYS}
    except (OSError, ValueError):
        return {}


def apply(cfg: dict, overlay: dict | None = None) -> dict:
    """cfg with the defaults for keys config.json may not have, then the overlay. Changes cfg in place."""
    for sec, vals in DEFAULTS.items():
        for k, v in vals.items():
            cfg.setdefault(sec, {}).setdefault(k, v)
    for path, v in (load() if overlay is None else overlay).items():
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
    """Validate `changes` ({path: raw}), keep only values that differ from config.json (+ .env),
    write the overlay. Returns the overlay written."""
    overlay = load()
    for path, raw in changes.items():
        if path not in KEYS:
            raise ValueError(f"{path}: nggak bisa diubah dari sini")
        v = clean(path, raw)
        if v == get_path(base_cfg, path):
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
    """What the ⚙ panel draws: one entry per setting with its current and config.json value."""
    return [{"path": p, "group": g, "label": lab, "kind": kind, **extra,
             "value": get_path(cfg, p), "base": get_path(base_cfg, p)} for p, g, lab, kind, extra in SCHEMA]


def base_copy(cfg_without_overlay: dict) -> dict:
    return copy.deepcopy(cfg_without_overlay)
