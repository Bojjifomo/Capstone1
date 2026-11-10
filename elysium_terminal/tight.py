"""Tight-range LP math, pair signals, and the auto-rebalance state machine.

Everything here is pure plus a JSON store. It does not call RPC and it does not
send transactions. The web layer may call `run_once` with an executor callback.

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
LIVE_PHRASE = "SAYA PAHAM LIVE"
ZERO = "0x" + "0" * 40

DEFAULT_CONFIG = {
    "enabled": False,
    "practice": True,
    "liveAck": "",
    "killed": False,
    "widthPct": 1.0,
    "targetPctPerHour": 5.0,
    "minSwaps": 12,
    "minBuckets": 3,
    "maxAgeSec": 900,
    "minCoveredSec": 1200,
    "enterPolls": 3,
    "exitPolls": 2,
    "persistenceSec": 180,
    "boundaryPct": 0.0,
    "cooldownSec": 900,
    "maxPerDay": 4,
    "maxSlippageBps": 100,
    "maxSwapUsd": 200.0,
    "maxNotionalUsd": 400.0,
    "minNetUsd": 0.0,
    "horizonHours": 1.0,
    "maxMovePct": 8.0,
    "staleSec": 180,
    "watch": [],
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
    "persistenceSec": (0, 86400),
    "boundaryPct": (0.0, 5.0),
    "cooldownSec": (60, 86400),
    "maxPerDay": (0, 48),
    "maxSlippageBps": (1, 500),
    "maxSwapUsd": (1.0, 100_000.0),
    "maxNotionalUsd": (1.0, 100_000.0),
    "minNetUsd": (0.0, 100_000.0),
    "horizonHours": (0.25, 24.0),
    "maxMovePct": (0.5, 50.0),
    "staleSec": (30, 3600),
}


def align_range(current_tick: int, spacing: int, width_pct: float) -> dict:
    """Center a band on the current tick and snap it outward onto the tick grid.

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
    lo = math.floor((current_tick - down) / spacing) * spacing
    hi = math.ceil((current_tick + up) / spacing) * spacing
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
            if k in ("minSwaps", "minBuckets", "maxAgeSec", "minCoveredSec", "enterPolls", "exitPolls",
                     "persistenceSec", "cooldownSec", "maxPerDay", "maxSlippageBps", "staleSec"):
                cfg[k] = int(cfg[k])
    if "enabled" in changes:
        cfg["enabled"] = bool(changes["enabled"])
    if "killed" in changes:
        cfg["killed"] = bool(changes["killed"])
    if changes.get("kill"):
        cfg["killed"] = True
        cfg["enabled"] = False
    if changes.get("clearKill"):
        cfg["killed"] = False
    # Live is a separate, explicit act. Enabling the loop does not turn practice off.
    if changes.get("allowLive"):
        if str(changes.get("liveAck") or "") == LIVE_PHRASE:
            cfg["practice"] = False
            cfg["liveAck"] = LIVE_PHRASE
        else:
            cfg["practice"] = True
    if changes.get("forcePractice"):
        cfg["practice"] = True
    if "watch" in changes and isinstance(changes["watch"], list):
        watch = []
        for w in changes["watch"][:20]:
            if not isinstance(w, dict):
                continue
            pool, tid = str(w.get("pool") or ""), str(w.get("tokenId") or "")
            if pool.startswith("0x") and tid.isdigit():
                watch.append({"pool": pool.lower(), "tokenId": int(tid), "symbol": str(w.get("symbol") or "")[:32]})
        cfg["watch"] = watch
    return cfg


def review_open(*, tick, spacing, width_pct, usd, quote_is_token0, fee_ppm, slippage_bps,
                 gross_pct_per_hour=None, include_close=False) -> dict:
    """Numbers for the confirm screen. Nothing is sent. The steps are separate transactions."""
    band = align_range(tick, spacing, width_pct)
    if not band["ok"]:
        return band
    where = range_status(tick, band["tickLower"], band["tickUpper"])
    split = value_split(tick, band["tickLower"], band["tickUpper"])
    swap = quote_swap_frac(split, quote_is_token0)
    net = net_estimate(gross_pct_per_hour=gross_pct_per_hour, notional_usd=usd, swap_frac=swap,
                       fee_ppm=fee_ppm, horizon_hours=1)
    slip = max(1, min(500, int(slippage_bps)))
    steps = []
    n = 1
    if include_close:
        steps.append({"n": n, "what": "close", "usd": usd,
                      "note": "Tutup posisi yang ada. Token keluar ke wallet. Ini transaksi sendiri."})
        n += 1
    if swap is not None and swap >= 0.01:
        steps.append({"n": n, "what": "swap", "usd": usd * swap,
                      "note": "Sebagian quote ditukar ke token satunya. Kalau ini masuk tapi mint gagal, token tetap di wallet dan tidak ditukar ulang otomatis."})
        n += 1
    steps.append({"n": n, "what": "mint", "usd": None if usd is None else usd * (1 - (swap or 0)),
                  "note": "Buka range baru. Dikirim hanya setelah langkah sebelumnya punya receipt. Bukan satu transaksi atomik."})
    return {
        "ok": True, "atomic": False, "band": band, "where": where, "swapFrac": swap, "steps": steps, "net": net,
        "slippageBps": slip,
        "minOutNote": f"Batas min token dari swap = perkiraan setelah fee pool, lalu dipotong slippage {slip / 100:.2f}%.",
        "gasNote": "Gas tidak dihitung. Tidak ada harga gas di snapshot.",
        "costNote": "Biaya yang kelihatan cuma fee swap pool. IL, adverse selection, dan gas tidak masuk.",
    }


def live_allowed(cfg: dict, env_practice: bool) -> bool:
    """True only when the file says live, the phrase matches, and the process is not in practice mode."""
    return (not env_practice) and (not cfg.get("practice")) and cfg.get("liveAck") == LIVE_PHRASE and not cfg.get("killed")


def _day(now: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%d")


def _near_or_out(status: dict, boundary_pct: float) -> bool:
    if not status["inRange"]:
        return True
    return boundary_pct > 0 and status["priceToEdgePct"] <= boundary_pct


def decide(pos: dict, snap: dict, cfg: dict, st: dict, now: float, env_practice: bool) -> dict:
    """One position, one snapshot. Returns an action and a state patch. Does not send anything."""
    st = dict(st or {})
    patch = {}
    dry = not live_allowed(cfg, env_practice)
    token_id = pos.get("tokenId")

    def stop(action, reason, **extra):
        return {"action": action, "reason": reason, "tokenId": token_id, "dryRun": dry, "patch": patch, **extra}

    if cfg.get("killed"):
        return stop("none", "kill switch nyala")
    if not cfg.get("enabled"):
        return stop("none", "auto-rebalance belum dinyalakan")
    if st.get("paused"):
        return stop("pause", st.get("pauseReason") or "posisi ini dijeda")
    if st.get("inflightKey") and not st.get("pendingHash"):
        return stop("pause", "langkah sempat dimulai tapi hash-nya tidak tersimpan. Tidak dikirim ulang. Cek explorer, lalu lepas jeda manual.")
    if st.get("pendingHash"):
        return stop("wait", "transaksi masih pending, tidak diganti dan tidak dikirim ulang", pendingHash=st["pendingHash"])
    hooks = (pos.get("hooks") or ZERO).lower()
    dex = pos.get("dex")
    spacing = int(pos.get("tickSpacing") or 0)
    if dex not in ("v3", "v4") or hooks != ZERO or spacing <= 0 or pos.get("dynamicFee"):
        return stop("unsupported", "protokol posisi ini tidak didukung buat rebalance otomatis (hook, fee dinamis, atau spacing kosong)")
    if not snap or not snap.get("rpcOk", True):
        patch["paused"] = True
        patch["pauseReason"] = "RPC gagal, rebalance berhenti sampai dilepas manual"
        return stop("pause", patch["pauseReason"])
    age = snap.get("ageSec")
    if age is None or age > cfg["staleSec"]:
        return stop("none", "snapshot basi, tidak bertindak")
    move = snap.get("movePct")
    if move is not None and move > cfg["maxMovePct"]:
        patch["paused"] = True
        patch["pauseReason"] = f"harga bergerak {move:.1f}% sejak snapshot sebelumnya, mencurigakan"
        return stop("pause", patch["pauseReason"])
    tick = snap.get("tick")
    if tick is None or pos.get("tickLower") is None:
        return stop("unsupported", "tick posisi atau harga tidak ada di snapshot")
    status = range_status(tick, pos["tickLower"], pos["tickUpper"])
    if not status["ok"]:
        return stop("unsupported", status["error"])
    triggered = _near_or_out(status, float(cfg["boundaryPct"]))
    if not triggered:
        if st.get("pendingHash"):
            return stop("wait", "harga sudah di dalam, tapi tx sebelumnya masih pending", status=status)
        if st.get("doneSteps"):
            patch["paused"] = True
            patch["pauseReason"] = "harga balik sebelum siklus selesai. Tidak dilanjut otomatis."
            return stop("pause", patch["pauseReason"], status=status)
        patch["outSince"] = None
        return stop("none", "masih di dalam range", status=status)
    out_since = st.get("outSince") or now
    patch["outSince"] = out_since
    if now - out_since < cfg["persistenceSec"]:
        return stop("none", "harga di tepi/di luar, masih nunggu waktu tahan", status=status)
    done = list(st.get("doneSteps") or [])
    continuing = bool(done)
    last = st.get("lastFire") or 0
    day = _day(now)
    count = st.get("dayCount") if st.get("day") == day else 0
    patch["day"] = day
    patch["dayCount"] = count
    if not continuing and now - last < cfg["cooldownSec"]:
        return stop("none", "masih cooldown", status=status)
    if not continuing and count >= cfg["maxPerDay"]:
        return stop("none", "sudah kena batas rebalance hari ini", status=status)
    notional = pos.get("valueUsd")
    if notional is None:
        return stop("none", "nilai posisi tidak ketahuan, tidak bertindak", status=status)
    if notional > cfg["maxNotionalUsd"]:
        return stop("none", "nilai posisi di atas batas notional", status=status)
    band = align_range(tick, spacing, cfg["widthPct"])
    if not band["ok"]:
        return stop("unsupported", band["error"], status=status)
    split = value_split(tick, band["tickLower"], band["tickUpper"])
    swap_frac = quote_swap_frac(split, bool(pos.get("quoteIsToken0")))
    if swap_frac is None:
        return stop("unsupported", "rasio token tidak bisa dihitung", status=status)
    swap_usd = notional * swap_frac
    if swap_usd > cfg["maxSwapUsd"] + 1e-9:
        return stop("none", "swap yang dibutuhkan di atas batas", status=status, swapUsd=swap_usd)
    net = net_estimate(gross_pct_per_hour=snap.get("grossPctPerHour"), notional_usd=notional,
                       swap_frac=swap_frac, fee_ppm=snap.get("feePpm"), horizon_hours=cfg["horizonHours"])
    if cfg["minNetUsd"] > 0:
        if not net.get("known"):
            return stop("none", "net tidak bisa dicek dari data yang ada, rebalance tidak jalan", status=status, net=net)
        if net["netUsd"] < cfg["minNetUsd"]:
            return stop("none", "perkiraan net parsial di bawah batas minimum", status=status, net=net)
    if "close" not in done:
        step = "close"
    elif dry:
        patch["outSince"] = None
        patch["doneSteps"] = []
        return stop("none", "simulasi close selesai. Buka ulang tidak dilanjutkan: practice tidak menghasilkan token.", status=status, net=net)
    elif "swap" not in done and swap_frac >= 0.01:
        step = "swap"
    elif "mint" not in done:
        step = "mint"
    else:
        patch["outSince"] = None
        patch["doneSteps"] = []
        return stop("none", "siklus selesai", status=status, net=net)
    episode = f"{token_id}:{int(out_since)}"
    key = f"{episode}:{step}"
    if key in (st.get("sentKeys") or []):
        return stop("wait", "langkah ini sudah pernah dicatat, tidak dikirim ulang", status=status)
    return stop("fire", "syarat rebalance terpenuhi", status=status, step=step, key=key, net=net,
                swapUsd=swap_usd, band=band, payload={
                    "pool": pos.get("pool"), "tokenId": token_id, "step": step,
                    "tickLower": band["tickLower"], "tickUpper": band["tickUpper"],
                    "slippageBps": cfg["maxSlippageBps"], "usd": notional, "swapFrac": swap_frac,
                    "dryRun": dry,
                })


class Store:
    """JSON state on disk. One lock so a restart sees an in-flight step instead of sending it again."""

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

    def positions(self) -> dict:
        with self._lock:
            return self._read("tight_positions.json", {})

    def save_position(self, token_id, patch: dict) -> dict:
        with self._lock:
            allp = self._read("tight_positions.json", {})
            cur = dict(allp.get(str(token_id)) or {})
            for k, v in patch.items():
                if v is None and k in ("outSince", "pendingHash", "inflightKey"):
                    cur.pop(k, None)
                else:
                    cur[k] = v
            allp[str(token_id)] = cur
            self._write("tight_positions.json", allp)
            return cur

    def audit(self, event: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, default=str)
        with self._lock:
            with open(self.dir / "tight_audit.jsonl", "a") as f:
                f.write(line + "\n")

    def audit_tail(self, n: int = 20) -> list:
        try:
            lines = (self.dir / "tight_audit.jsonl").read_text().splitlines()
        except OSError:
            return []
        out = []
        for ln in lines[-n:]:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out


def run_once(store: Store, positions: list[dict], snaps: dict, now: float, execute, env_practice: bool = True, lookup=None) -> dict:
    """At most one step, and only if the saved state says this step was not already sent.

    `lookup(hash)` returns None while the tx is pending, or {"ok": bool} for a receipt.
    A lookup error pauses. A pending receipt is never replaced.
    """
    cfg = store.config()
    if cfg.get("killed"):
        return {"ran": False, "reason": "kill switch nyala"}
    if not cfg.get("enabled"):
        return {"ran": False, "reason": "auto-rebalance mati"}
    watched = {int(w["tokenId"]) for w in cfg.get("watch") or []}
    chosen = [p for p in positions if int(p.get("tokenId") or -1) in watched]
    if not chosen:
        store.audit({"ts": now, "kind": "skip", "reason": "tidak ada posisi yang diikutkan"})
        return {"ran": False, "reason": "tidak ada posisi yang diikutkan"}
    if lookup:
        for tid, st in list(store.positions().items()):
            if not st.get("pendingHash"):
                continue
            try:
                receipt = lookup(st["pendingHash"])
            except Exception as exc:
                store.save_position(tid, {"paused": True, "pauseReason": f"RPC gagal saat cek receipt: {exc}"[:240]})
                store.audit({"ts": now, "kind": "rpc-fail", "tokenId": tid, "error": str(exc)[:200]})
                return {"ran": False, "reason": "RPC gagal"}
            resolve_receipt(store, tid, receipt, now)
    results = []
    fired = False
    saved = store.positions()
    for pos in chosen:
        tid = str(pos["tokenId"])
        st = dict(saved.get(tid) or {})
        if st.get("inflightKey") or st.get("pendingHash"):
            # somebody, including a manual click, already holds the single flight
            decision = decide(pos, snaps.get(pos.get("pool")) or {}, cfg, st, now, env_practice)
            results.append(decision)
            if decision["patch"]:
                saved[tid] = store.save_position(tid, decision["patch"])
            continue
        decision = decide(pos, snaps.get(pos.get("pool")) or {}, cfg, st, now, env_practice)
        if decision["patch"]:
            st = store.save_position(tid, decision["patch"])
            saved[tid] = st
        if decision["action"] != "fire" or fired:
            results.append(decision)
            continue
        others = [i for i, s in saved.items() if i != tid and (s.get("pendingHash") or s.get("inflightKey"))]
        if others:
            results.append({**decision, "action": "wait", "reason": "masih ada proses lain, tidak ada tx baru"})
            continue
        fired = True
        key = decision["key"]
        store.save_position(tid, {"inflightKey": key, "pendingStep": decision["step"]})
        store.audit({"ts": now, "kind": "fire", "tokenId": tid, "step": decision["step"], "key": key,
                     "dryRun": decision["dryRun"], "reason": decision["reason"]})
        try:
            res = execute(decision["payload"])
        except Exception as exc:
            store.save_position(tid, {"paused": True, "pauseReason": f"executor error: {exc}"[:240]})
            store.audit({"ts": now, "kind": "error", "tokenId": tid, "error": str(exc)[:240]})
            decision = {**decision, "action": "pause", "reason": str(exc)[:240]}
            results.append(decision)
            break
        if not isinstance(res, dict) or not res.get("ok"):
            err = (res or {}).get("error") or "gagal"
            unsupported = bool((res or {}).get("unsupported"))
            store.save_position(tid, {"paused": True, "pauseReason": err[:240],
                                      "unsupported": unsupported})
            store.audit({"ts": now, "kind": "fail", "tokenId": tid, "error": err[:240], "unsupported": unsupported})
            results.append({**decision, "action": "pause" if not unsupported else "unsupported", "result": res})
            break
        if res.get("hash"):
            sent = list(st.get("sentKeys") or []) + [key]
            store.save_position(tid, {"pendingHash": res["hash"], "pendingStep": decision["step"],
                                      "sentKeys": sent, "inflightKey": key})
            store.audit({"ts": now, "kind": "sent", "tokenId": tid, "hash": res["hash"], "step": decision["step"]})
        else:
            # Simulated, or a step that finished without a broadcast. Count it and allow the next step later.
            done = list(st.get("doneSteps") or []) + [decision["step"]]
            sent = list(st.get("sentKeys") or []) + [key]
            count = (st.get("dayCount") or 0) + (1 if decision["step"] == "close" else 0)
            store.save_position(tid, {"doneSteps": done, "sentKeys": sent, "inflightKey": None,
                                      "dayCount": count, "day": _day(now), "lastFire": now})
            store.audit({"ts": now, "kind": "simulated" if res.get("simulated") else "done",
                         "tokenId": tid, "step": decision["step"]})
        results.append({**decision, "result": res})
        break
    return {"ran": True, "results": results}


def resolve_receipt(store: Store, token_id, receipt: dict | None, now: float) -> dict:
    """Fold a receipt into state. A missing receipt means still pending: do not clear, do not replace."""
    st = store.positions().get(str(token_id)) or {}
    if not st.get("pendingHash"):
        return {"state": "idle"}
    if receipt is None:
        return {"state": "pending", "hash": st["pendingHash"]}
    if not receipt.get("ok"):
        store.save_position(token_id, {"paused": True, "pauseReason": "receipt gagal atau revert",
                                       "pendingHash": None, "lastHash": st["pendingHash"]})
        store.audit({"ts": now, "kind": "receipt-fail", "tokenId": token_id, "hash": st["pendingHash"]})
        return {"state": "failed"}
    done = list(st.get("doneSteps") or [])
    step = st.get("pendingStep")
    if step and step not in done:
        done.append(step)
    store.save_position(token_id, {"pendingHash": None, "inflightKey": None, "doneSteps": done,
                                   "lastHash": st["pendingHash"], "lastFire": now})
    store.audit({"ts": now, "kind": "receipt-ok", "tokenId": token_id, "hash": st["pendingHash"], "step": step})
    return {"state": "ok", "step": step}


def manual_claim(store: Store, key: str, token_id) -> dict | None:
    """Reserve a manual step. None means it is free to run. Otherwise a reason to not broadcast."""
    st = store.positions().get(str(token_id)) or {}
    if st.get("pendingHash"):
        return {"ok": False, "wait": True, "error": "masih ada tx pending, tidak dikirim ulang", "hash": st["pendingHash"]}
    if st.get("inflightKey"):
        return {"ok": False, "error": "langkah ini sempat dimulai tanpa hash tersimpan. Tidak dikirim ulang."}
    if key in (st.get("sentKeys") or []):
        return {"ok": False, "duplicate": True, "error": "kunci ini sudah pernah dikirim"}
    # Also block if any other position holds the flight.
    for other_id, other in store.positions().items():
        if other_id != str(token_id) and (other.get("inflightKey") or other.get("pendingHash")):
            return {"ok": False, "error": "masih ada proses lain. Selesaiin dulu."}
    store.save_position(token_id, {"inflightKey": key, "pendingStep": key.split(":")[-1]})
    return None


def manual_finish(store: Store, token_id, key: str, result: dict, now: float) -> None:
    st = store.positions().get(str(token_id)) or {}
    if not result.get("ok"):
        store.save_position(token_id, {"paused": True, "pauseReason": (result.get("error") or "gagal")[:240]})
        store.audit({"ts": now, "kind": "manual-fail", "tokenId": token_id, "key": key, "error": result.get("error")})
        return
    sent = list(dict.fromkeys(list(st.get("sentKeys") or []) + [key]))
    if result.get("hash"):
        store.save_position(token_id, {"pendingHash": result["hash"], "sentKeys": sent, "inflightKey": key})
        store.audit({"ts": now, "kind": "manual-sent", "tokenId": token_id, "key": key, "hash": result["hash"]})
        return
    done = list(st.get("doneSteps") or [])
    step = key.split(":")[-1]
    if step not in done:
        done.append(step)
    store.save_position(token_id, {"sentKeys": sent, "doneSteps": done, "inflightKey": None, "lastFire": now})
    store.audit({"ts": now, "kind": "manual-simulated", "tokenId": token_id, "key": key})
