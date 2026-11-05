"""Record what the Python terminal says about a fixed set of pools, so the JavaScript twin in
"DLMM Rules.js" can be checked against it (node dlmm_terminal/scriptable/test_rules.mjs).

  python3 dlmm_terminal/scriptable/tests/make_golden.py

Pools are live Meteora rows (a few edge cases are edited by hand), pinned to one `now`.
Needs data/benchmark.json + benchmark_entries.jsonl first:  python3 -m dlmm_terminal benchmark
Re-run it when filters.py / benchmark.py / activity.py / web.verdict / config.json change.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from dlmm_terminal import activity, api, benchmark, filters, web  # noqa: E402

PKG = ROOT / "dlmm_terminal"
OUT = Path(__file__).resolve().parent / "golden.json"


def strip_notes(o):
    if isinstance(o, dict):
        return {k: strip_notes(v) for k, v in o.items() if not k.startswith("_")}
    return o


def main():
    cfg = strip_notes(json.loads((PKG / "config.json").read_text()))
    now_ms = int(time.time()) * 1000
    now = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    sig = cfg["signal"]
    prof = json.loads((PKG / "data" / "benchmark.json").read_text())

    # --- pools: live rows + hand-made edge cases ---------------------------------------------
    base = f"is_blacklisted=false && tvl>=3000 && fee_tvl_ratio_24h>=0.5 && token_y={api.SOL_MINT}"
    live = api.list_pools(base, page_size=14)
    sol_x = api.list_pools(f"is_blacklisted=false && tvl>=50000 && token_x={api.SOL_MINT}", sort_by="tvl:desc", page_size=2)
    no_sol = api.list_pools("is_blacklisted=false && tvl>=100000", sort_by="tvl:desc", page_size=40)
    no_sol = [p for p in no_sol if api.SOL_MINT not in (p["token_x"]["address"], p["token_y"]["address"])][:1]
    cases = []
    for i, p in enumerate(live + sol_x + no_sol):
        cases.append({"snap": p, "changes": {"1h": 3.2 + i, "6h": -18.5 if i % 3 == 0 else 4.0, "24h": 10.0},
                      "market": {"vol1h": (p.get("volume") or {}).get("1h"), "vol24": (p.get("volume") or {}).get("24h"),
                                 "buys24": 120 + i * 7, "sells24": 90 + i * 11}, "size": None})
    p0 = live[0]
    edit = lambda f: json.loads(json.dumps(p0))  # noqa: E731
    young = edit(0); young["created_at"] = now_ms - 30 * 60 * 1000; young["address"] = "YOUNG"
    narrow = edit(0); narrow["pool_config"]["bin_step"] = 10; narrow["address"] = "BIN10"
    thin = edit(0); thin["fees"] = {"1h": 0, "24h": 3}; thin["address"] = "THIN"
    nofee = edit(0); nofee["fees"] = {}; nofee["address"] = "NOFEES"
    huge = edit(0); huge["address"] = "SIZE"
    cases += [{"snap": young, "changes": {}, "market": None, "size": None},
              {"snap": narrow, "changes": {"1h": 25.5}, "market": None, "size": None},     # 25.5 -> "+26%" (half to even)
              {"snap": thin, "changes": {"1h": 26.5, "6h": -17.0}, "market": None, "size": None},   # 26.5 -> "+26%"
              {"snap": nofee, "changes": {"1h": None, "6h": None, "24h": None}, "market": {"vol1h": 5, "vol24": 1000, "buys24": 10, "sells24": 70}, "size": None},
              {"snap": huge, "changes": {"1h": 1.0}, "market": None, "size": 30.0}]
    sized_cfg = json.loads(json.dumps(cfg))
    sized_cfg["gates"]["maxPositionPctOfTvl"] = 3        # a second config with the size gate on
    out_cases = []
    for k, c in enumerate(cases):
        use_cfg = sized_cfg if k % 4 == 1 else cfg
        row = filters.evaluate(c["snap"], [], use_cfg, now=now, size_sol=c["size"], changes=c["changes"])
        row.update(benchmark.candidate_dims(c["snap"]))
        m = benchmark.match(row, prof, sig.get("bandOverrides"))
        v = web.verdict(row, c["market"])
        out_cases.append({**c, "cfg": "sized" if use_cfg is sized_cfg else "base", "expected": {"c": row, "match": m, "verdict": v}})

    # --- dead-then-alive ----------------------------------------------------------------------
    def hourly(fees):
        return [{"timestamp": 1_000_000 + i * 3600, "fees": f, "volume": f * 100} for i, f in enumerate(fees)]
    series = {
        "alive": [10, 12, 9, 11, 14, 8, 10, 13, 12, 9, 11, 10, 12, 9, 8, 11, 10, 12, 14, 9, 10, 11, 13, 12],
        "revived": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 40, 60, 30],
        "tieDead": [0.5] * 6 + [99.5, 0, 0, 0],
        "young": [3, 90, 7],
        "empty": [0, 0, 0],
    }
    act = []
    for name, fees in series.items():
        for age in (30.0, 8.9, 2.0):
            rows = hourly(fees)
            f = activity.features(rows, age, sig["revival"]["deadHourPct"])
            act.append({"name": name, "ageH": age, "hourly": rows, "expected": f, "revived": activity.revived(f, sig["revival"]),
                        "text": activity.describe(f) if f else None})

    # --- benchmark profile + entry features ---------------------------------------------------
    entries = [json.loads(l) for l in (PKG / "data" / "benchmark_entries.jsonl").read_text().splitlines() if l.strip()]
    pr = benchmark.profile(entries, prof["wallet"], prof["days"])
    by_pool = {}
    for e in entries:
        by_pool.setdefault(e["pool"], []).append(e)
    pool_addr = next(a for a, es in by_pool.items() if len(es) >= 2 and len({e["entryTs"][:10] for e in es}) <= 2)
    since = time.time() - 40 * 86400
    pos = [p for p in api.position_pnl(pool_addr, prof["wallet"], "closed") if p["createdAt"] >= since]
    snap = api.pool(pool_addr)
    side = filters.sol_side(snap)
    start, end = int(min(p["createdAt"] for p in pos) - 25 * 3600), int(max(p["createdAt"] for p in pos) + 3600)
    vol = api.chunked(api.volume_history, pool_addr, start, end)
    candles = api.chunked(api.ohlcv, pool_addr, start, end)
    groups = benchmark.group_entries(pos)
    entry_case = {"positions": pos, "snap": snap, "vol": vol, "candles": candles, "side": side,
                  "groups": [[p["positionAddress"] for p in g] for g in groups],
                  "expected": [benchmark.entry_features(g, snap, vol, candles, side) for g in groups]}

    OUT.write_text(json.dumps({"now": now_ms, "cfg": cfg, "profile": prof, "pools": out_cases, "activity": act,
                               "profileCase": {"entries": entries, "expected": pr}, "entryCase": entry_case}, separators=(",", ":")))
    print(f"{OUT} {OUT.stat().st_size // 1024} KB · {len(out_cases)} pools · {len(act)} activity series · "
          f"{len(entries)} entries · entry case {pool_addr[:6]}… {len(groups)} groups")


if __name__ == "__main__":
    main()
