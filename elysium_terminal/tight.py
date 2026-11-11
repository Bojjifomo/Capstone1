"""Tight-range LP math and pair signals.

Everything here is pure plus a JSON store. It does not call RPC and it does not
send transactions. What happens to an open position (rebalance, close when the
price leaves the range, TP, SL) lives in auto.py.

Yield numbers are a pool-wide fee/TVL proxy. They are not position fees, not a
forecast, and not a promise. A user target such as 5%/hour is only a filter.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

MIN_TICK = -887272
MAX_TICK = 887272
_LN = math.log(1.0001)
ZERO = "0x" + "0" * 40

DEFAULT_CONFIG = {
    "widthPct": 1.0,
    "targetPctPerHour": 5.0,
    "minSwaps": 12,
    "minBuckets": 3,
    "maxAgeSec": 900,
    "minCoveredSec": 1200,
    "enterPolls": 3,
    "exitPolls": 2,
    "horizonHours": 1.0,
}

_LIMITS = {
    "widthPct": (0.05, 20.0),
    "targetPctPerHour": (0.0, 100.0),
    "minSwaps": (1, 500),
    "minBuckets": (1, 48),
    "maxAgeSec": (60, 7200),
    "minCoveredSec": (300, 86400),
    "enterPolls": (1, 12),
    "exitPolls": (1, 12),
    "horizonHours": (0.25, 24.0),
}
_INTS = ("minSwaps", "minBuckets", "maxAgeSec", "minCoveredSec", "enterPolls", "exitPolls")


def align_range(current_tick: int, spacing: int, width_pct: float) -> dict:
    """Center a band on the current tick and snap it to the nearest tick grid lines.

    `width_pct` is the full width of the band in percent of price (1 = about ±0.5%).
    The current tick is inside [tickLower, tickUpper). If one spacing is wider than
    the request, the result says so instead of pretending the band is tight.
    """
    current_tick = int(current_tick)
    spacing = int(spacing)
    if spacing <= 0:
        return {"ok": False, "error": "tick spacing tidak ada, range tidak bisa disejajarkan"}
    if not (0 < float(width_pct) < 180):
        return {"ok": False, "error": "lebar range harus lebih dari 0 dan di bawah 180%"}
    half = float(width_pct) / 2 / 100
    if half >= 0.9:
        return {"ok": False, "error": "lebar range terlalu besar"}
    down = math.log(1 / (1 - half)) / _LN
    up = math.log(1 + half) / _LN
    # nearest grid line on each side, so a 2% request on a coarse grid is not silently 3%
    lo = math.floor((current_tick - down) / spacing + 0.5) * spacing
    hi = math.floor((current_tick + up) / spacing + 0.5) * spacing
    if not (lo <= current_tick < hi):
        if current_tick < lo:
            lo = math.floor(current_tick / spacing) * spacing
        if current_tick >= hi:
            hi = math.floor(current_tick / spacing) * spacing + spacing
    if hi <= lo:
        hi = lo + spacing
    min_lo = math.ceil(MIN_TICK / spacing) * spacing
    max_hi = math.floor(MAX_TICK / spacing) * spacing
    lo = max(int(lo), int(min_lo))
    hi = min(int(hi), int(max_hi))
    if hi <= lo or not (lo <= current_tick < hi):
        return {"ok": False, "error": "range tidak muat di grid tick pool ini"}
    if lo % spacing or hi % spacing:
        return {"ok": False, "error": "tick tidak sejajar dengan spacing"}
    actual = (1.0001 ** (hi - lo) - 1) * 100
    return {
        "ok": True,
        "tickLower": int(lo),
        "tickUpper": int(hi),
        "requestedWidthPct": float(width_pct),
        "actualWidthPct": actual,
        "spacingCoarse": actual > float(width_pct) * 1.5,
    }


def range_status(current_tick: int, lo: int, hi: int) -> dict:
    """Where the price sits in a range. In range means lo <= tick < hi, same as Uniswap."""
    current_tick, lo, hi = int(current_tick), int(lo), int(hi)
    if hi <= lo:
        return {"ok": False, "error": "range rusak"}
    span = hi - lo
    if current_tick < lo:
        side, edge, outside = "below", lo, lo - current_tick
    elif current_tick >= hi:
        side, edge, outside = "above", hi, current_tick - hi + 1
    else:
        side, outside = "in", 0
        edge = lo if (current_tick - lo) <= (hi - current_tick) else hi
    dist = abs(current_tick - edge)
    return {
        "ok": True,
        "inRange": side == "in",
        "side": side,
        "positionPct": (current_tick - lo) / span * 100,
        "priceToEdgePct": abs(1.0001 ** dist - 1) * 100,
        "outsideTicks": outside,
        "ticksToEdge": 0 if side != "in" else min(current_tick - lo, hi - current_tick),
    }


def value_split(tick: int, lo: int, hi: int) -> dict:
    """Share of position value in token0 and token1. Independent of liquidity and decimals."""
    tick, lo, hi = int(tick), int(lo), int(hi)
    if tick < lo:
        return {"frac0": 1.0, "frac1": 0.0}
    if tick >= hi:
        return {"frac0": 0.0, "frac1": 1.0}
    sp = 1.0001 ** (tick / 2)
    sa = 1.0001 ** (lo / 2)
    sb = 1.0001 ** (hi / 2)
    price = 1.0001 ** tick
    a0 = (sb - sp) / (sp * sb)
    a1 = sp - sa
    value1 = a1 + a0 * price
    if value1 <= 0 or not math.isfinite(value1):
        return {"frac0": None, "frac1": None}
    return {"frac0": (a0 * price) / value1, "frac1": a1 / value1}


def quote_swap_frac(split: dict, quote_is_token0: bool) -> float | None:
    """Part of a quote-only deposit that must be swapped into the other token."""
    key = "frac1" if quote_is_token0 else "frac0"
    val = split.get(key)
    return None if val is None else float(val)


def gross_yield(fee_usd: float | None, tvl_usd: float | None, covered_sec: float | None) -> dict:
    """Observed pool fee / TVL, scaled to an hour, plus a simple APR of that rate.

    Simple APR is `% per hour × 24 × 365`. It is not compounded and it is not a forecast.
    Scaling a short window up to an hour is marked so the caller can refuse thin samples.
    """
    window = (covered_sec or 0) / 3600
    if fee_usd is None or tvl_usd is None or tvl_usd <= 0 or not covered_sec or covered_sec < 300:
        return {"grossPctPerHour": None, "simpleAprPct": None, "windowHours": window, "scaled": False}
    pct = fee_usd / tvl_usd * (3600 / covered_sec) * 100
    return {
        "grossPctPerHour": pct,
        "simpleAprPct": pct * 24 * 365,
        "windowHours": window,
        "scaled": covered_sec < 3000,
    }


def net_estimate(*, gross_pct_per_hour, notional_usd, swap_frac, fee_ppm, horizon_hours) -> dict:
    """Pool-proxy fees over the horizon, minus the pool's own swap fee on the swapped part.

    Gas, impermanent loss, and adverse selection are not subtracted. They are not knowable
    from the indexed snapshot, so the result stays partial on purpose.
    """
    limits = [
        "Gross ini fee seluruh pool dibagi TVL, bukan fee yang posisi ini hasilkan.",
        "Posisi sempit bisa dapat lebih atau jauh lebih sedikit. Share likuiditas di tick aktif tidak ada di index.",
        "IL dan adverse selection tidak dipotong: tidak bisa dihitung jujur dari data ini.",
        "Gas tidak masuk. Harga gas tidak ada di snapshot, dan tidak dikarang.",
    ]
    base = {"partial": True, "complete": False, "limitations": limits, "gasUsd": None, "ilUsd": None}
    if gross_pct_per_hour is None or notional_usd is None or notional_usd <= 0:
        return {**base, "known": False, "note": "Net tidak dihitung: fee pool atau ukuran posisi belum ada."}
    gross_usd = notional_usd * (gross_pct_per_hour / 100) * float(horizon_hours)
    if swap_frac is None or fee_ppm is None:
        return {**base, "known": False, "grossUsd": gross_usd,
                "note": "Biaya swap tidak ketahuan, jadi net tidak ditampilkan sebagai angka."}
    swap_cost = notional_usd * float(swap_frac) * (float(fee_ppm) / 1e6)
    return {
        **base, "known": True, "grossUsd": gross_usd, "swapCostUsd": swap_cost,
        "netUsd": gross_usd - swap_cost,
        "note": "Net parsial: proxy fee pool/TVL dikurangi fee swap. Bukan keuntungan yang diharapkan.",
    }


def assess(*, fee_usd, tvl_usd, covered_sec, swaps, buckets, age_sec, move_pct, width_pct,
           target_pct_per_hour, fee_source, spacing_coarse, vol_usd=None,
           min_swaps=12, min_buckets=3, max_age_sec=900, min_covered_sec=1200,
           fee_window_sec=None) -> dict:
    """Say why a pool is or isn't a candidate. No side effects, no notifications."""
    y = gross_yield(fee_usd, tvl_usd, fee_window_sec if fee_window_sec is not None else covered_sec)
    rejects = []
    notes = []
    source = fee_source or "tidak ketahuan"
    if source != "index":
        notes.append(f"Sumber fee: {source}. Bukan log swap yang kita index sendiri.")
    if y["grossPctPerHour"] is None:
        rejects.append("Fee 1 jam atau TVL belum ada, jadi %/jam tidak bisa dihitung.")
    elif y["scaled"]:
        notes.append(f"Angka %/jam diskalakan dari jendela {y['windowHours'] * 60:.0f} menit, bukan pengukuran sejam penuh.")
    if covered_sec is None or covered_sec < min_covered_sec:
        if not any("Jendela" in r or "belum ada" in r for r in rejects):
            rejects.append(f"Jendela data di bawah {min_covered_sec / 60:.0f} menit.")
    if swaps is None or swaps < min_swaps:
        rejects.append(f"Cuma {int(swaps or 0)} swap di jendela ini. Sampel sekecil itu bukan kepastian.")
    if buckets is None or buckets < min_buckets:
        rejects.append(f"Cuma {int(buckets or 0)} bucket ber-swap. Harga dan fee-nya belum kebentuk.")
    if age_sec is None or age_sec > max_age_sec:
        rejects.append(f"Data terakhir sudah basi (lebih dari {max_age_sec / 60:.0f} menit).")
    if move_pct is None:
        rejects.append("Volatilitas pendek tidak ketahuan: harga di index kurang dari 2 titik.")
    elif width_pct and move_pct > width_pct:
        rejects.append(
            f"Harga bergerak {move_pct:.2f}% di jendela ini, lebih lebar dari range {width_pct:.2f}%. "
            "Range sesempit itu gampang kelempar.")
    if spacing_coarse:
        rejects.append("Tick spacing pool ini lebih lebar dari range yang diminta. Range yang kejajar tidak sesempit yang lo tulis.")
    if vol_usd is None and tvl_usd:
        notes.append("Volume USD tidak ada di snapshot ini.")
    gross = y["grossPctPerHour"]
    above = gross is not None and not rejects and (target_pct_per_hour <= 0 or gross >= target_pct_per_hour)
    if not rejects and gross is not None and target_pct_per_hour > 0 and gross < target_pct_per_hour:
        status = "di bawah target"
        notes.append(
            f"Fee pool/TVL {gross:.2f}%/jam, di bawah target lo {target_pct_per_hour:.2f}%/jam. "
            "Target itu selera, bukan patokan aman dan bukan yield yang dijamin.")
    elif rejects:
        status = "tidak cocok"
    else:
        status = "lolos filter"
    thin = y["scaled"] or (swaps or 0) < 30 or (buckets or 0) < 6 or source != "index" or (age_sec or 9999) > 600
    return {
        "status": status,
        "rawAbove": above,
        "grossPctPerHour": gross,
        "simpleAprPct": y["simpleAprPct"],
        "windowHours": y["windowHours"],
        "scaled": y["scaled"],
        "feeSource": source,
        "poolProxy": True,
        "positionFeeUsd": None,
        "rejects": rejects,
        "notes": notes,
        "confidence": "terlalu tipis, jangan dianggap pasti" if (rejects or thin) else "cukup buat dilihat, tetap bukan jaminan",
        "movePct": move_pct,
        "label": "fee pool / TVL, bukan fee posisi",
    }


def realized_move(points: list, now: float, window: float = 3600) -> dict:
    """High/low of the indexed price series. Relative prices are enough; no RPC."""
    pts = [p for p in points or [] if p.get("px") and p.get("t") is not None and now - window <= p["t"] <= now + 1]
    traded = [p for p in pts if (p.get("swaps") or 0) > 0]
    if len(pts) < 2:
        return {"movePct": None, "buckets": len(traded), "coveredSec": 0, "ageSec": None}
    pxs = [p["px"] for p in pts]
    start, end = pts[0]["t"], pts[-1]["t"]
    return {
        "movePct": (max(pxs) / min(pxs) - 1) * 100 if min(pxs) > 0 else None,
        "buckets": len(traded),
        "coveredSec": max(0, end - start),
        "ageSec": max(0, now - (end + 300)),
    }


def screen_row(row: dict, points: list, now: float, cfg: dict) -> dict:
    """One pool, from a snapshot the caller already has. Does not fetch anything."""
    move = realized_move(points, now)
    width = float(cfg.get("widthPct") or 1)
    tick, spacing = row.get("tick"), int(row.get("tickSpacing") or 0)
    band = align_range(tick, spacing, width) if tick is not None and spacing > 0 else {"ok": False, "spacingCoarse": True}
    status = range_status(tick, band["tickLower"], band["tickUpper"]) if band.get("ok") else None
    verdict = assess(
        fee_usd=row.get("fees1h"), tvl_usd=row.get("tvlUsd"), covered_sec=move["coveredSec"],
        swaps=row.get("swaps1h"), buckets=move["buckets"], age_sec=move["ageSec"],
        move_pct=move["movePct"], width_pct=width, target_pct_per_hour=float(cfg.get("targetPctPerHour") or 0),
        fee_source=row.get("feeSource") or "tidak ketahuan", spacing_coarse=bool(band.get("spacingCoarse")),
        vol_usd=row.get("vol1h"), fee_window_sec=row.get("feeWindowSec"),
        min_swaps=int(cfg.get("minSwaps") or 12),
        min_buckets=int(cfg.get("minBuckets") or 3), max_age_sec=int(cfg.get("maxAgeSec") or 900),
        min_covered_sec=int(cfg.get("minCoveredSec") or 1200))
    if tick is None:
        verdict["rejects"] = ["Harga tick tidak ada di snapshot ini."] + verdict["rejects"]
        verdict["rawAbove"] = False
        verdict["status"] = "tidak cocok"
    split = value_split(tick, band["tickLower"], band["tickUpper"]) if band.get("ok") else {"frac0": None, "frac1": None}
    swap = quote_swap_frac(split, bool(row.get("quoteIsToken0"))) if split.get("frac0") is not None else None
    net = net_estimate(gross_pct_per_hour=verdict["grossPctPerHour"], notional_usd=row.get("notionalUsd"),
                       swap_frac=swap, fee_ppm=row.get("feePpm"), horizon_hours=float(cfg.get("horizonHours") or 1))
    return {
        "pool": row.get("pool"), "symbol": row.get("symbol"), "quoteSymbol": row.get("quoteSymbol"),
        "dex": row.get("dex"), "tick": tick, "tickSpacing": spacing,
        "band": band if band.get("ok") else None, "where": status, "verdict": verdict, "net": net,
        "swapFrac": swap, "hooks": row.get("hooks") or ZERO,
        "supported": (row.get("dex") in ("v3", "v4")) and (str(row.get("hooks") or ZERO).lower() == ZERO)
        and not row.get("dynamicFee") and spacing > 0 and bool(band.get("ok")),
    }


def apply_hysteresis(prev: dict | None, raw_above: bool, now: float, enter: int, exit_n: int) -> dict:
    """A pool joins the list only after several passes, and leaves only after several misses."""
    st = dict(prev or {})
    if raw_above:
        st["above"] = int(st.get("above") or 0) + 1
        st["below"] = 0
        if st["above"] >= enter and not st.get("on"):
            st["on"] = True
            st["since"] = now
    else:
        st["below"] = int(st.get("below") or 0) + 1
        st["above"] = 0
        if st.get("on") and st["below"] >= exit_n:
            st["on"] = False
            st.pop("since", None)
    st["on"] = bool(st.get("on"))
    st["at"] = now
    return st


def _num(v, lo, hi, default):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return min(hi, max(lo, x))


def sanitize_config(changes: dict, current: dict | None = None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if current:
        cfg.update({k: current[k] for k in DEFAULT_CONFIG if k in current})
    for k, (lo, hi) in _LIMITS.items():
        if k in changes:
            cfg[k] = _num(changes[k], lo, hi, cfg[k])
        if k in _INTS:
            cfg[k] = int(cfg[k])
    return cfg


def review_open(*, tick, spacing, width_pct, usd, quote_is_token0, fee_ppm, slippage_bps,
                 gross_pct_per_hour=None) -> dict:
    """Numbers for the confirm screen, from the snapshot. Nothing is sent."""
    band = align_range(tick, spacing, width_pct)
    if not band["ok"]:
        return band
    where = range_status(tick, band["tickLower"], band["tickUpper"])
    split = value_split(tick, band["tickLower"], band["tickUpper"])
    swap = quote_swap_frac(split, quote_is_token0)
    net = net_estimate(gross_pct_per_hour=gross_pct_per_hour, notional_usd=usd, swap_frac=swap,
                       fee_ppm=fee_ppm, horizon_hours=1)
    slip = max(1, min(1000, int(slippage_bps)))
    steps = []
    if swap is not None and swap >= 0.005:
        steps.append({"n": 1, "what": "swap", "usd": usd * swap,
                      "note": "Sebagian quote ditukar ke token, lewat pool yang sama. Batal kalau hasilnya lebih jelek dari batas slippage."})
    steps.append({"n": len(steps) + 1, "what": "mint", "usd": usd,
                  "note": "Buka range di sekitar harga saat itu, pakai quote + token dari langkah ini saja (bukan seluruh saldo wallet)."})
    return {
        "ok": True, "atomic": False, "band": band, "where": where, "swapFrac": swap, "steps": steps, "net": net,
        "slippageBps": slip,
        "minOutNote": f"Batas min dari swap = harga pool setelah fee, dipotong slippage {slip / 100:.2f}%.",
        "costNote": "Biaya yang kelihatan cuma fee swap pool. IL, adverse selection, dan gas tidak masuk.",
        "flowNote": "Swap dan mint itu dua transaksi berurutan dalam satu klik. Kalau swap masuk tapi mint gagal, token tetap di wallet (Cleanup bisa jual balik).",
    }


class Store:
    """Screening config and the hysteresis state of the pair list, on disk."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self._lock = threading.Lock()

    def _read(self, name, default):
        try:
            return json.loads((self.dir / name).read_text())
        except (OSError, ValueError):
            return default

    def _write(self, name, obj):
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1))
        tmp.replace(path)

    def config(self) -> dict:
        with self._lock:
            raw = self._read("tight_config.json", {})
            return sanitize_config(raw, raw)

    def save_config(self, changes: dict) -> dict:
        with self._lock:
            cfg = sanitize_config(changes, self._read("tight_config.json", {}))
            self._write("tight_config.json", cfg)
            return cfg

    def signals(self) -> dict:
        with self._lock:
            return self._read("tight_signals.json", {})

    def save_signals(self, signals: dict) -> None:
        with self._lock:
            self._write("tight_signals.json", signals)
