"""DLMM terminal CLI.

  python -m dlmm_terminal screen                 # scan SOL pools, gates + flags, log every candidate
  python -m dlmm_terminal screen --pool ADDR     # one pool (or several --pool)
  python -m dlmm_terminal decide ADDR enter|skip --reason "..."
  python -m dlmm_terminal monitor --wallet W     # every 5 min: log PnL, alerts, exit rules (auto TP/SL)
  python -m dlmm_terminal followup               # fill price 1h/6h/24h after each candidate
  python -m dlmm_terminal web                    # browser terminal on http://localhost:8790
  python -m dlmm_terminal open POOL --sol 8      # open a 5-slice Spot SOL-only ladder (RH style), you confirm
  python -m dlmm_terminal benchmark              # benchmark wallet's last 20 days -> habitat profile
  python -m dlmm_terminal signal                 # every 5 min: ping pools that pass gates + match the benchmark
  python -m dlmm_terminal rpc                    # what the Helius RPC was spent on (open / close / plan)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import activity, api, benchmark, blast, exits, filters

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


# ---------- plumbing ----------

def load_env() -> None:
    for p in (HERE / ".env", HERE.parent / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    # An empty line from the template ("KEY=") means "not set": it must not
                    # block a value given later in the file or in the other .env.
                    if v and not os.environ.get(k):
                        os.environ[k] = v


def load_config(path: str | None) -> dict:
    cfg = json.loads(Path(path or HERE / "config.json").read_text())
    # Per-person sizing from .env, so nobody has to edit config.json.
    for env, key in (("DLMM_BANKROLL_SOL", "bankrollSol"), ("DLMM_SIZE_SOL", "defaultSizeSol"), ("DLMM_LOSS_BUDGET_PCT", "lossBudgetPct")):
        v = os.environ.get(env, "").strip()
        if v:
            try:
                cfg["position"][key] = float(v)
            except ValueError:
                print(f"{env}={v!r} bukan angka; pakai config.json", file=sys.stderr)
    return cfg


def append(name: str, row: dict) -> None:
    DATA.mkdir(exist_ok=True)
    with open(DATA / name, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_rows(name: str) -> list[dict]:
    p = DATA / name
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _tg_chunks(text: str, limit: int = 4000) -> list[str]:
    """Telegram caps a message at 4096 UTF-16 units (an emoji counts 2): split on blank lines."""
    def n(t: str) -> int:
        return len(t.encode("utf-16-le")) // 2
    out, cur = [], ""
    for part in text.split("\n\n"):
        if cur and n(cur) + 2 + n(part) > limit:
            out.append(cur)
            cur = part
        else:
            cur = f"{cur}\n\n{part}" if cur else part
    return out + ([cur] if cur else [])


def notify(text: str, html: str | None = None) -> None:
    """Print `text`; send it to Telegram when configured. `html` = Telegram-HTML version to send instead."""
    print(text, flush=True)
    token, chat = os.environ.get("DLMM_TG_BOT_TOKEN"), os.environ.get("DLMM_TG_CHAT_ID")
    if token and chat:
        for chunk in _tg_chunks(html or text):
            try:
                fields = {"chat_id": chat, "text": chunk, "disable_web_page_preview": "true"}
                if html:
                    fields["parse_mode"] = "HTML"
                api.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", timeout=10,
                            data=urllib.parse.urlencode(fields).encode()).read()
            except Exception as exc:  # alerts must never kill the monitor
                print(f"  (telegram failed: {exc})", file=sys.stderr)


def candles_1h(pool: str, hours: int = 26) -> list[dict]:
    now = int(time.time())
    return api.ohlcv(pool, "1h", now - hours * 3600, now)


# ---------- screen ----------

def scan_pools(cfg: dict) -> list[dict]:
    s, u = cfg["scan"], cfg["universe"]
    min_ratio = cfg["gates"]["minFeeApr24hPct"] / 365  # fee_tvl_ratio_24h is % per day
    base = (f"is_blacklisted=false && tvl>={s['prefilterMinTvlUsd']} && fee_tvl_ratio_24h>={min_ratio:.4f}")
    out = []
    for side in ("token_y", "token_x"):
        for page in range(1, s["pages"] + 1):
            rows = api.list_pools(f"{base} && {side}={api.SOL_MINT}", page=page, page_size=s["pageSize"])
            out += [r for r in rows if u["binStepMin"] <= (r.get("pool_config") or {}).get("bin_step", 0) <= u["binStepMax"]]
            if len(rows) < s["pageSize"]:
                break
    return out


def fmt_row(c: dict) -> str:
    def pct(v):
        return "   n/a" if v is None else f"{v:+6.0f}%"
    mark = "PROPOSE" if c["decision"] == "propose" else "skip   "
    line = (f"{mark} {c['symbol'] or '?':<10} age {c['poolAgeHours']:6.1f}h  tvl ${c['tvlUsd']:>9,.0f}  "
            f"feeAPR24h {c['feeApr24h']:>8,.0f}%  1h{pct(c['priceChg1h'])} 6h{pct(c['priceChg6h'])}  "
            f"bin {c['binStep']:>3}  size {c['plan']['sizeSol']} SOL ({c['sizeVsTvlPct']}% TVL)")
    if c["flags"]:
        line += "\n          FLAG: " + " · ".join(c["flags"])
    if c["gatesFailed"]:
        line += "\n          GATE: " + " · ".join(c["gatesFailed"])
    if c["decision"] == "propose":
        line += (f"\n          plan: {c['plan']['strategy']} {c['plan']['side']}, range bottom "
                 f"{c['plan']['rangeBottomPct']}% → {c['plan']['rangeBottomPriceSol']:.3e} SOL  ·  {c['pool']}")
    return line


def cmd_screen(a, cfg):
    snaps = [api.pool(p) for p in a.pool] if a.pool else scan_pools(cfg)
    max_sol = filters.max_position_sol(cfg)
    print(f"bankroll {cfg['position']['bankrollSol']} SOL, loss budget {cfg['position']['lossBudgetPct']}% "
          f"→ max position {max_sol:.2f} SOL · {len(snaps)} candidate pools\n")
    rows = []
    for s in snaps:
        try:
            c = filters.evaluate(s, candles_1h(s["address"]), cfg, size_sol=a.size)
        except Exception as exc:
            print(f"error {s.get('name')}: {exc}", file=sys.stderr)
            continue
        append("candidates.jsonl", c)
        rows.append(c)
    rows.sort(key=lambda c: (c["decision"] != "propose", -c["feeApr24h"]))
    shown = rows if a.all else [c for c in rows if c["decision"] == "propose"]
    for c in shown:
        print(fmt_row(c))
    n = sum(c["decision"] == "propose" for c in rows)
    print(f"\n{n} pass all gates, {len(rows) - n} skipped (all logged to {DATA / 'candidates.jsonl'})."
          + ("" if a.all else " Use --all to see the skipped ones and why."))


def cmd_open(a, cfg):
    """Gates first, then the ladder plan, then you type y. Sends with DLMM_KEYPAIR."""
    from . import node
    pos = cfg["position"]
    sol = filters.planned_size_sol(cfg, a.sol)
    if a.sol and a.sol > sol + 1e-9:
        print(f"⚠ {a.sol} SOL is over the loss-budget cap; using {sol} SOL (--force-size to override)")
        if a.force_size:
            sol = a.sol
    slices = a.slices or pos.get("ladderSlices", 5)
    bottom = abs(a.bottom if a.bottom is not None else pos["rangeBottomPct"])
    c = filters.evaluate(api.pool(a.pool), candles_1h(a.pool), cfg, size_sol=sol)
    append("candidates.jsonl", {**c, "source": "open"})
    print(fmt_row(c))
    if c["gatesFailed"] and not a.force:
        sys.exit("\nGate gagal: nggak dibuka. --force kalau lu tetap mau (dicatat sebagai override).")
    plan = node.run("open_ladder.mjs", node.ladder_args(a.pool, sol, slices, bottom, a.top, a.strategy) + ["--plan"])
    if not plan.get("ok"):
        sys.exit(f"plan gagal: {plan.get('error')}")
    p = plan["plan"]
    print(f"\n{p['strategy'].title()} SOL-only ladder · {sol} SOL · {len(p['slices'])} slice · {p['bins']} bin · "
          f"harga → −{p['realEnd']:.1f}%  (bin step {p['binStep']})")
    for i, s_ in enumerate(p["slices"], 1):
        print(f"  slice {i}: {s_['sol']:.4f} SOL  −{s_['nearPct']:.1f}% → −{s_['farPct']:.1f}%  ({s_['width']} bin)")
    mode = ["--dry-run", "--owner", a.owner or os.environ.get("DLMM_WALLET", "")] if a.dry_run else []
    if a.dry_run and not mode[-1]:
        sys.exit("--dry-run butuh --owner WALLET (atau DLMM_WALLET di .env)")
    if not a.dry_run:
        if not node.key_configured():
            sys.exit("\nBelum ada key: isi DLMM_PRIVATE_KEY (base58 dari Phantom) di .env, atau pakai --dry-run.")
        if not a.yes and input("\nKirim? ketik y: ").strip().lower() != "y":
            append("decisions.jsonl", {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pool": a.pool,
                                       "symbol": c["symbol"], "decision": "skip", "decisionReason": "cancelled at confirm"})
            return print("Batal.")
    res = node.run("open_ladder.mjs", node.ladder_args(a.pool, sol, slices, bottom, a.top, a.strategy) + mode)
    for r in res.get("results", []):
        print(f"  slice {r['slice']}: {r['position']}  " + (r.get("signature") or str(r.get("simulate") or r.get("error"))))
    if res.get("error"):
        print("error:", res["error"])
    append("decisions.jsonl", {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pool": a.pool, "symbol": c["symbol"],
                               "decision": "enter", "dryRun": a.dry_run, "override": bool(c["gatesFailed"]), "sizeSol": sol,
                               "slices": slices, "rangeBottomPct": -bottom, "positions": [r["position"] for r in res.get("results", [])],
                               "ok": res.get("ok")})
    print("\n" + ("OK" if res.get("ok") else "Nggak semua slice jalan; yang belum kebuka tetap SOL di wallet.")
          + (" (simulasi, nggak dikirim)" if a.dry_run else ""))


def cmd_decide(a, cfg):
    last = [c for c in read_rows("candidates.jsonl") if c["pool"] == a.pool]
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pool": a.pool,
           "decision": a.decision, "decisionReason": a.reason,
           "candidateTs": last[-1]["ts"] if last else None, "symbol": last[-1]["symbol"] if last else None}
    append("decisions.jsonl", row)
    print(f"logged: {row['symbol'] or a.pool} → {a.decision} ({a.reason})")


# ---------- monitor (auto TP / exits) ----------

def ticks_for_wallet(wallet: str, now: float):
    for p in api.open_portfolio(wallet):
        side = "y" if p.get("tokenYMint") == api.SOL_MINT else "x" if p.get("tokenXMint") == api.SOL_MINT else None
        if side is None:
            continue
        symbol = p["tokenX"] if side == "y" else p["tokenY"]
        for pos in api.position_pnl(p["poolAddress"], wallet, "open"):
            lo, hi = float(pos["minPrice"]), float(pos["maxPrice"])
            lo, hi = (lo, hi) if side == "y" else (1 / hi, 1 / lo)
            price = filters.to_token_in_sol(float(pos.get("poolActivePrice") or p.get("poolPrice") or 0), side)
            pnl = pos.get("pnlSolPctChange") or pos.get("pnlPctChange") or 0
            dep_tok = (pos.get("allTimeDeposits") or {}).get("tokenX" if side == "y" else "tokenY") or {}
            yield exits.Tick(position=pos["positionAddress"], pool=p["poolAddress"], symbol=symbol, now=now,
                             created_at=pos.get("createdAt"), pnl_pct=float(pnl),
                             pnl_sol=float(pos["pnlSol"]) if pos.get("pnlSol") else None,
                             price=price, range_min=lo, range_max=hi,
                             sol_only=float(dep_tok.get("amount") or 0) == 0)


def execute_close(t: exits.Tick, wallet: str, dry_run: bool) -> bool:
    cmd = ["node", str(HERE / "executor" / "close_position.mjs"), "--pool", t.pool, "--position", t.position]
    if dry_run:
        cmd += ["--dry-run", "--owner", wallet]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    print(r.stdout.strip() or r.stderr.strip())
    from . import node
    node.log_rpc("close_position.mjs", cmd, node.last_json(r.stdout) or {})
    return r.returncode == 0


def monitor_once(wallet: str, cfg: dict, states: dict, mode: str, dry_run: bool) -> None:
    ex, now = cfg["exits"], time.time()
    seen = set()
    for t in ticks_for_wallet(wallet, now):
        seen.add(t.position)
        st = exits.State.from_dict(states.get(t.position))
        events = exits.evaluate(t, st, ex)
        append("pnl_log.jsonl", {"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
                                 "position": t.position, "pool": t.pool, "symbol": t.symbol,
                                 "pnlPct": t.pnl_pct, "pnlSol": t.pnl_sol, "peakPnlPct": st.peak_pnl,
                                 "priceVsEntryPct": round((t.price / t.range_max - 1) * 100, 2) if t.range_max else None,
                                 "inRange": t.range_min <= t.price <= t.range_max,
                                 "events": [e["rule"] for e in events]})
        print(f"{datetime.now():%H:%M} {t.symbol:<10} PnL {t.pnl_pct:+6.2f}% (peak {st.peak_pnl:+.2f}%)  "
              f"price {(t.price / t.range_max - 1) * 100:+6.1f}% vs range top  {t.position[:6]}…")
        for e in events:
            tag = f"[{t.symbol} {t.position[:6]}…]"
            if e["kind"] == "alert":
                notify(f"⚠️ {tag} {e['msg']}")
            elif mode == "auto":
                notify(f"🔴 {tag} CLOSING ({e['rule']}): {e['msg']}")
                st.close_sent = execute_close(t, wallet, dry_run)
                notify(f"{tag} close {'sent' if st.close_sent else 'FAILED, will retry next tick'}")
                append("closes.jsonl", {"ts": now, "position": t.position, "pool": t.pool, "rule": e["rule"],
                                        "msg": e["msg"], "pnlPct": t.pnl_pct, "ok": st.close_sent, "dryRun": dry_run})
            else:
                st.close_sent = True  # alert mode: say it once
                notify(f"🟠 {tag} exit rule '{e['rule']}' hit: {e['msg']} (mode=alert, close it yourself)")
        states[t.position] = st.to_dict()
    for gone in set(states) - seen:
        states.pop(gone)


def cmd_monitor(a, cfg):
    mode = a.mode or cfg["exits"]["mode"]
    on = {k: v for k, v in cfg["exits"].items() if not k.startswith("_") and k != "mode" and v not in (None, False)}
    print(f"monitor {a.wallet} · mode={mode} · rules: {on}")
    risky = [k for k in ("takeProfitPct", "stopLossPct", "trailingPts", "maxHoldMin") if cfg["exits"].get(k) is not None]
    if risky:
        print(f"note: {risky} are on. In the spec data every fixed TP/SL/trailing exit lost SOL vs. holding.")
    if mode == "auto" and not a.dry_run and not (os.environ.get("DLMM_PRIVATE_KEY") or os.environ.get("DLMM_KEYPAIR")):
        sys.exit("mode=auto needs DLMM_PRIVATE_KEY (or DLMM_KEYPAIR) and SOLANA_RPC_URL in .env; or pass --dry-run.")
    state_file = DATA / "monitor_state.json"
    states = json.loads(state_file.read_text()) if state_file.exists() else {}
    while True:
        try:
            monitor_once(a.wallet, cfg, states, mode, a.dry_run)
            DATA.mkdir(exist_ok=True)
            state_file.write_text(json.dumps(states))
        except Exception as exc:
            print(f"tick failed: {exc}", file=sys.stderr)
        if a.once:
            return
        time.sleep(cfg["exits"]["pollEveryMin"] * 60)


# ---------- follow-up on candidates ----------

def cmd_followup(a, cfg):
    rows, done = read_rows("candidates.jsonl"), {(r["pool"], r["candidateTs"]) for r in read_rows("followup.jsonl")}
    now, n = time.time(), 0
    for c in rows:
        ts = datetime.fromisoformat(c["ts"]).timestamp()
        if (c["pool"], c["ts"]) in done or now - ts < 24 * 3600 or not c.get("priceSol"):
            continue
        try:
            bars = api.ohlcv(c["pool"], "1h", int(ts), int(ts + 25 * 3600))
        except Exception as exc:
            print(f"{c['symbol']}: {exc}", file=sys.stderr)
            continue
        side = c["solSide"]

        def after(h):
            hit = [b for b in bars if b["timestamp"] >= ts + h * 3600 - 1800]
            return round((filters.to_token_in_sol(hit[0]["close"], side) / c["priceSol"] - 1) * 100, 2) if hit else None
        row = {"pool": c["pool"], "candidateTs": c["ts"], "symbol": c["symbol"], "decision": c["decision"],
               "chg1hPct": after(1), "chg6hPct": after(6), "chg24hPct": after(24),
               "minVsEntryPct": round((min(filters.to_token_in_sol(b["low" if side == "y" else "high"], side)
                                           for b in bars) / c["priceSol"] - 1) * 100, 2) if bars else None}
        append("followup.jsonl", row)
        n += 1
    print(f"filled {n} candidates (price 1h / 6h / 24h later + worst dip) → {DATA / 'followup.jsonl'}")


# ---------- RPC usage (Helius free tier) ----------

def cmd_rpc(a, cfg):
    """What the keyed RPC (Helius) was spent on. Only the executor (open / close / plan) uses RPC;
    screen, signal, benchmark, monitor (alert) and the web lists use the Meteora + DexScreener APIs."""
    from . import node
    rows = [json.loads(l) for l in node.USAGE_LOG.read_text().splitlines() if l.strip()] if node.USAGE_LOG.exists() else []
    since = datetime.now(timezone.utc).timestamp() - a.days * 86400
    rows = [r for r in rows if datetime.fromisoformat(r["ts"]).timestamp() >= since]
    if not rows:
        return print(f"No executor runs in the last {a.days:g} days → 0 RPC requests. "
                     "(screen / signal / benchmark / monitor alert / web lists never touch RPC.)")
    def total(rs, host=None):
        return sum((r.get("byHost") or {}).get(host, 0) if host else r.get("calls", 0) for r in rs)
    print(f"RPC requests, last {a.days:g} days: {total(rows)} total · helius {total(rows, 'helius')} · "
          f"public {total(rows, 'public')}")
    by: dict = {}
    for r in rows:
        k = f"{r['script'].replace('.mjs', '')} --{r['mode']}"
        v = by.setdefault(k, [0, 0, 0])
        v[0] += 1
        v[1] += (r.get("byHost") or {}).get("helius", 0)
        v[2] += r.get("calls", 0)
    for k, (n, h, t) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print(f"  {k:<28} {n:>4} runs · helius {h:>5} ({h / n:.0f}/run) · all {t}")
    methods: dict = {}
    for r in rows:
        for m, n in (r.get("byMethod") or {}).items():
            methods[m] = methods.get(m, 0) + n
    print("  by method: " + ", ".join(f"{m} {n}" for m, n in sorted(methods.items(), key=lambda kv: -kv[1])))
    days = max(a.days, 1)
    print(f"  pace ≈ {total(rows, 'helius') / days * 30:,.0f} Helius requests / 30 days")


# ---------- benchmark wallet + entry signal ping ----------

def _short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if addr and len(addr) > 12 else addr or "?"


def load_benchmark(cfg: dict, wallet: str | None = None, days: float | None = None, force: bool = False) -> dict:
    """data/benchmark.json, rebuilt when it is for another wallet/window or older than refreshBenchmarkHours."""
    sig = cfg["signal"]
    wallet, days = wallet or sig["benchmarkWallet"], days or sig["benchmarkDays"]
    path = DATA / "benchmark.json"
    if path.exists() and not force:
        prof = json.loads(path.read_text())
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(prof["builtAt"])).total_seconds() / 3600
        if prof["wallet"] == wallet and prof["days"] == days and age_h < sig["refreshBenchmarkHours"]:
            return prof
    print(f"building benchmark: {_short(wallet)}, last {days:g} days …", flush=True)
    entries = benchmark.build(wallet, days, log=lambda m: print(m, flush=True))
    if not entries:
        raise RuntimeError(f"no closed SOL DLMM positions for {wallet} in the last {days:g} days")
    prof = benchmark.profile(entries, wallet, days)
    DATA.mkdir(exist_ok=True)
    path.write_text(json.dumps(prof, indent=1))
    (DATA / "benchmark_entries.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    return prof


def print_benchmark(prof: dict, overrides: dict | None = None) -> None:
    f = benchmark._fmt
    print(f"\nBenchmark {_short(prof['wallet'])} · last {prof['days']:g} days · built {prof['builtAt']}")
    print(f"  {prof['entries']} entries in {prof['tokens']} pools · win {prof['wins']}/{prof['entries']} "
          f"({prof['winRatePct']}%) · PnL {prof['pnlSol']:+.2f} SOL (Meteora PnL, before selling leftover tokens) "
          f"· median size {prof['capitalSolMedian']} SOL")
    print("  bin steps:  " + "  ".join(f"{k}: {v['entries']}x {v['pnlSol']:+.2f} SOL"
                                       for k, v in sorted(prof["binSteps"].items(), key=lambda kv: -kv[1]["entries"])))
    q = prof["quantiles"]
    print(f"  habitat (p{q[0] * 100:.0f}–p{q[1] * 100:.0f} of entries; * = matched in the signal):")
    for d, b in benchmark.bands_for(prof, overrides).items():
        mark = "*" if d in benchmark.MATCH_DIMS else " "
        print(f"   {mark} {d:<13} {benchmark._band_txt(d, b):<22}"
              + (" (fixed in config)" if b.get("fixed") else "")
              + (f" median {f(d, b['median'])}" if b.get("median") is not None else "")
              + (f", winners {f(d, b['winMedian'])}" if b.get("winMedian") is not None else ""))


def cmd_benchmark(a, cfg):
    prof = load_benchmark(cfg, a.wallet, a.days, force=not a.cached)
    print_benchmark(prof, cfg["signal"].get("bandOverrides"))
    print(f"\nsaved → {DATA / 'benchmark.json'} (+ benchmark_entries.jsonl, one row per entry)")


def signal_message(c: dict, m: dict, prof: dict, head: str) -> str:
    f = benchmark._fmt
    lines = [
        f"{head} · {c['name'] or c['symbol']}",
        f"gates v1 ✅ · benchmark {len(m['hits'])}/{m['dims']} ({_short(prof['wallet'])}, {prof['days']:g}d)"
        if not c["gatesFailed"] else f"gates ⛔ {'; '.join(c['gatesFailed'])} · benchmark {len(m['hits'])}/{m['dims']}",
        f"fee APR 24h {c['feeApr24h']:,.0f}% · 1h {c['feeApr1h']:,.0f}% · TVL {f('tvlUsd', c['tvlUsd'])} · "
        f"age {f('poolAgeHours', c['poolAgeHours'])} · mcap {f('mcapUsd', c['mcapUsd']) if c['mcapUsd'] else 'n/a'} · "
        f"bin {c['binStep']}",
        f"token age {f('tokenAgeHours', c['tokenAgeHours']) if c.get('tokenAgeHours') is not None else 'n/a'} · "
        f"vol 24h {f('volume24hUsd', c['volume24hUsd']) if c.get('volume24hUsd') is not None else 'n/a'} · "
        f"total fees {f('totalFeesSol', c['totalFeesSol']) if c.get('totalFeesSol') is not None else 'n/a'}",
        f"fees 1h {f('fees1hUsd', c['fees1hUsd'])} · 24h {f('fees24hUsd', c['fees24hUsd'])} · "
        f"price 1h {c['priceChg1h'] if c['priceChg1h'] is not None else 'n/a'}% · 6h "
        f"{c['priceChg6h'] if c['priceChg6h'] is not None else 'n/a'}%",
    ]
    if m["misses"]:
        lines.append("outside habitat: " + " · ".join(m["misses"]))
    lines.append("flags: " + (" · ".join(f"⚠️ {x}" for x in c["flags"]) if c["flags"] else "none"))
    p = c["plan"]
    lines.append(f"plan: {p['strategy']} {p['side']} {p['sizeSol']} SOL, bottom {p['rangeBottomPct']}% "
                 f"({c['sizeVsTvlPct']}% of TVL)")
    lines.append(f"https://app.meteora.ag/dlmm/{c['pool']}")
    lines.append("assist mode: lu yang konfirmasi (`python3 -m dlmm_terminal open " + c["pool"] + "`)")
    return "\n".join(lines)


def _candidate(snap: dict, cfg: dict, prof: dict, with_candles: bool, now: float | None = None,
               token: dict | None = None) -> tuple[dict, dict]:
    when = datetime.fromtimestamp(now, timezone.utc) if now else None
    c = filters.evaluate(snap, candles_1h(snap["address"]) if with_candles else [], cfg, now=when)
    c.update(benchmark.candidate_dims(snap, token, c["poolAgeHours"]))
    sig = cfg["signal"]
    return c, benchmark.match(c, prof, sig.get("bandOverrides"), sig.get("matchDims"))


def scan_hits(cfg: dict, prof: dict, state: dict, now: float) -> list[tuple[dict, dict, dict]]:
    """Every SOL pool that passes layer 1 (v1 gates) and layer 2 (signal.matchDims bands + benchmark
    bin steps), minus dead-then-alive pools: [(candidate, match, snapshot)].
    Pass 1 is snapshot-only gates; only pools that pass get the DexScreener token stats (one request
    per token), candles and hourly history."""
    sig = cfg["signal"]
    log_all = now - state.get("lastLog", 0) >= 15 * 60
    hits, excluded, passed = [], [], []
    for snap in scan_pools(cfg):
        try:
            c, m = _candidate(snap, cfg, prof, with_candles=False, now=now)
        except Exception as exc:
            print(f"error {snap.get('name')}: {exc}", file=sys.stderr)
            continue
        if c["gatesFailed"]:
            if log_all:
                append("candidates.jsonl", {**c, "source": "signal", "benchmark": m})
            continue
        passed.append((snap, c))
    tokens = blast.token_stats([c["tokenMint"] for _, c in passed], now) if passed else {}
    for snap, c0 in passed:
        try:
            tok = tokens.get(c0["tokenMint"])
            c, m = _candidate(snap, cfg, prof, with_candles=False, now=now, token=tok)
            if m["score"] >= sig["minMatchScore"]:
                c, m = _candidate(snap, cfg, prof, with_candles=True, now=now, token=tok)  # price moves for flags
        except Exception as exc:
            print(f"error {snap.get('name')}: {exc}", file=sys.stderr)
            continue
        hit = m["score"] >= sig["minMatchScore"]
        if hit:
            hit = _revival_check(c, snap, sig, now, excluded)
        if log_all or hit:
            append("candidates.jsonl", {**c, "source": "signal", "benchmark": m})
        if hit:
            hits.append((c, m, snap))
    if log_all:
        state["lastLog"] = now
    state["revivedExcluded"] = excluded
    return hits


def _revival_check(c: dict, snap: dict, sig: dict, now: float, excluded: list) -> bool:
    """Dead-then-alive (pump-and-dump on a quiet token): one hourly-history request per pool that
    already passed everything else. mode gate = drop it, flag = keep with a warning, off = skip."""
    rv = sig.get("revival") or {}
    mode = rv.get("mode", "gate")
    if mode == "off":
        return True
    try:
        hourly = api.chunked(api.volume_history, c["pool"], int(now - 26 * 3600), int(now))
    except Exception as exc:
        print(f"activity {c['symbol']}: {exc}", file=sys.stderr)
        return True
    f = activity.features(hourly, c["poolAgeHours"], rv.get("deadHourPct", 0.5))
    c["activity"] = f
    if not activity.revived(f, rv):
        return True
    why = activity.describe(f)
    if mode == "flag":
        c["flags"].append(why)
        return True
    c["gatesFailed"].append(f"revival: {why}")
    c["decision"], c["decisionReason"] = "skip", "; ".join(c["gatesFailed"])
    excluded.append({"pool": c["pool"], "symbol": c["symbol"], **f})
    return False


def blast_once(cfg: dict, prof: dict, state: dict, now: float | None = None, hits: list | None = None) -> list[dict]:
    """Top-N card (signal.blastTop, default 10) of the pools passing gates + benchmark, sorted by
    signal.blastSortBy. Sent when signal.blastEveryMin has passed since the last one."""
    sig, now = cfg["signal"], now or time.time()
    hits = scan_hits(cfg, prof, state, now) if hits is None else hits
    dx = blast.dex_5m([c["pool"] for c, _, _ in hits])
    rows = [blast.row(c, snap, m, dx.get(c["pool"]), now) for c, m, snap in hits]
    best = blast.top(rows, sig.get("blastTop", 10), sig.get("blastSortBy", "volTvl1h"))
    skipped = len(state.get("revivedExcluded") or [])
    notify(blast.message(best, len(rows), cfg, prof, html_mode=False, revived=skipped),
           html=blast.message(best, len(rows), cfg, prof, html_mode=True, revived=skipped))
    state["lastBlast"] = now
    ts = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    for i, r in enumerate(best, 1):
        append("signals.jsonl", {"ts": ts, "kind": "blast", "rank": i, "pool": r["pool"], "symbol": r["symbol"],
                                 "feeTvl24h": round(r["feeTvl24h"], 2), "volTvl1h": round(r["volTvl1h"], 2),
                                 "tvl": r["tvl"], "flags": r["flags"]})
    return best


def signal_once(cfg: dict, prof: dict, state: dict, now: float | None = None) -> list[dict]:
    """mode 'new': ping a pool when it *starts* passing gates + benchmark (not every tick it stays
    there), at most once per cooldownHours. The first scan of a fresh state sends one digest instead.
    state: {'pinged': {pool: ts}, 'matching': [pool], 'walletPools': [pool], 'lastLog': ts}."""
    sig, now = cfg["signal"], now or time.time()
    pinged, out = state.setdefault("pinged", {}), []
    was = set(state.get("matching", []))
    first = "matching" not in state
    hits = [(c, m) for c, m, _ in scan_hits(cfg, prof, state, now)]
    hits.sort(key=lambda cm: (-cm[1]["score"], len(cm[0]["flags"]), -cm[0]["feeApr24h"]))
    state["matching"] = sorted(c["pool"] for c, _ in hits)
    if first and hits:
        f = benchmark._fmt
        notify(f"🟢 DLMM signal watcher on · {len(hits)} pool(s) pass v1 gates + benchmark "
               f"{_short(prof['wallet'])} ({prof['days']:g}d) right now:\n" + "\n".join(
                   f"• {c['name'] or c['symbol']}  APR24h {c['feeApr24h']:,.0f}% · TVL {f('tvlUsd', c['tvlUsd'])} · "
                   f"age {f('poolAgeHours', c['poolAgeHours'])}" + (f" · ⚠️ {', '.join(c['flags'])}" if c["flags"] else "")
                   + f"\n  https://app.meteora.ag/dlmm/{c['pool']}" for c, _ in hits[:10])
               + ("" if len(hits) <= 10 else f"\n… +{len(hits) - 10} more (data/signals.jsonl)")
               + "\nFrom here on: one ping per pool when it newly matches.")
    for c, m in hits:
        new = c["pool"] not in was and now - pinged.get(c["pool"], 0) >= sig["cooldownHours"] * 3600
        if not first and not new:
            continue
        if not first:
            notify(signal_message(c, m, prof, "🟢 DLMM ENTRY SIGNAL"))
        pinged[c["pool"]] = now
        row = {"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
               "kind": "digest" if first else "match", "pool": c["pool"], "symbol": c["symbol"], "score": m["score"],
               "hits": m["hits"], "misses": m["misses"], "flags": c["flags"], "feeApr24h": c["feeApr24h"],
               "tvlUsd": c["tvlUsd"], "priceSol": c["priceSol"]}
        append("signals.jsonl", row)
        out.append(row)
    if sig.get("pingWalletEntries"):
        out += _wallet_entries(cfg, prof, state, now)
    for pool in [p for p, t in pinged.items() if now - t > 7 * 86400]:
        pinged.pop(pool)
    return out


def tick(cfg: dict, prof: dict, state: dict, now: float | None = None) -> list[dict]:
    """One loop step (every pollEveryMin). mode 'blast' (default): scan, 🆕 alert for tokens that just
    entered detection, the top-N card every blastEveryMin, wallet-entry pings. mode 'new': per-pool
    pings on new matches (signal_once)."""
    sig, now = cfg["signal"], now or time.time()
    if sig.get("mode", "blast") != "blast":
        return signal_once(cfg, prof, state, now)
    hits = scan_hits(cfg, prof, state, now)
    out = _new_entrants(hits, cfg, prof, state, now)
    if now - state.get("lastBlast", 0) >= sig.get("blastEveryMin", 30) * 60 - 30:
        out += blast_once(cfg, prof, state, now, hits=hits)
    if sig.get("pingWalletEntries"):
        out += _wallet_entries(cfg, prof, state, now)
    return out


def _new_entrants(hits: list, cfg: dict, prof: dict, state: dict, now: float) -> list[dict]:
    """🆕 alert when a token enters detection that was not in it on the previous scan (and was not
    alerted within newAlertCooldownHours). Keyed by token, so a token with several pools alerts once,
    on its best pool. The first scan of a fresh state only records what is there (the blast covers it)."""
    sig = cfg["signal"]
    if not sig.get("newEntryAlerts", True):
        return []
    best: dict = {}
    for c, m, snap in hits:
        k = c["tokenMint"] or c["pool"]
        if k not in best or c["feeApr24h"] > best[k][0]["feeApr24h"]:
            best[k] = (c, m, snap)
    first = "detected" not in state
    was = set(state.get("detected", []))
    alerted = state.setdefault("newAlerted", {})
    state["detected"] = sorted(best)
    out = []
    if first:
        return out
    cool = sig.get("newAlertCooldownHours", 6) * 3600
    for k, (c, m, snap) in best.items():
        if k in was or now - alerted.get(k, 0) < cool:
            continue
        others = sum(1 for cc, _, _ in hits if (cc["tokenMint"] or cc["pool"]) == k) - 1
        notify(signal_message(c, m, prof, "🆕 NEW IN DETECTION")
               + (f"\n(+{others} other pool(s) of this token also pass)" if others else ""))
        alerted[k] = now
        row = {"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"), "kind": "newEntry",
               "token": k, "pool": c["pool"], "symbol": c["symbol"], "feeApr24h": c["feeApr24h"],
               "tvlUsd": c["tvlUsd"], "mcapUsd": c["mcapUsd"], "volume24hUsd": c.get("volume24hUsd"),
               "totalFeesSol": c.get("totalFeesSol"), "tokenAgeHours": c.get("tokenAgeHours"), "flags": c["flags"]}
        append("signals.jsonl", row)
        out.append(row)
    for k in [k for k, t in alerted.items() if now - t > 7 * 86400]:
        alerted.pop(k)
    return out


def _wallet_entries(cfg: dict, prof: dict, state: dict, now: float) -> list[dict]:
    """Ping when the benchmark wallet opens a position in a pool it wasn't in on the last tick."""
    wallet = prof["wallet"]
    try:
        open_now = {p["poolAddress"] for p in api.open_portfolio(wallet)}
    except Exception as exc:
        print(f"benchmark wallet poll failed: {exc}", file=sys.stderr)
        return []
    first = "walletPools" not in state
    new = open_now - set(state.get("walletPools", []))
    state["walletPools"] = sorted(open_now)
    if first:  # don't blast the positions that were already open when the watcher started
        return []
    out = []
    for pool in sorted(new):
        try:
            c, m = _candidate(api.pool(pool), cfg, prof, with_candles=True, now=now)
        except Exception as exc:
            notify(f"👀 benchmark {_short(wallet)} entered {pool} (detail failed: {exc})")
            continue
        notify(signal_message(c, m, prof, f"👀 BENCHMARK WALLET ENTERED ({_short(wallet)})"))
        row = {"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"), "kind": "walletEntry",
               "pool": pool, "symbol": c["symbol"], "gatesFailed": c["gatesFailed"], "score": m["score"],
               "flags": c["flags"], "priceSol": c["priceSol"]}
        append("signals.jsonl", row)
        out.append(row)
    return out


def cmd_signal(a, cfg):
    sig = cfg["signal"]
    if a.wallet:
        sig["benchmarkWallet"] = a.wallet
    if a.min_score is not None:
        sig["minMatchScore"] = a.min_score
    prof = load_benchmark(cfg)
    print_benchmark(prof, sig.get("bandOverrides"))
    tg = bool(os.environ.get("DLMM_TG_BOT_TOKEN") and os.environ.get("DLMM_TG_CHAT_ID"))
    how = (f"blast top {sig.get('blastTop', 10)} every {sig.get('blastEveryMin', 30)} min, sorted by {sig.get('blastSortBy', 'volTvl1h')}"
           if sig.get("mode", "blast") == "blast" else f"ping new matches, cooldown {sig['cooldownHours']}h per pool")
    print(f"\nsignal: gates v1 + benchmark score ≥ {sig['minMatchScore']} · {how} · wallet check every "
          f"{sig['pollEveryMin']} min · telegram {'on' if tg else 'off (set DLMM_TG_BOT_TOKEN / DLMM_TG_CHAT_ID)'}")
    state_file = DATA / "signal_state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    while True:
        try:
            if (datetime.now(timezone.utc) - datetime.fromisoformat(prof["builtAt"])).total_seconds() > sig["refreshBenchmarkHours"] * 3600:
                prof = load_benchmark(cfg)
            sent = tick(cfg, prof, state)
            DATA.mkdir(exist_ok=True)
            state_file.write_text(json.dumps(state))
            print(f"{datetime.now():%H:%M} scan done · {len(sent)} ping(s)", flush=True)
        except Exception as exc:
            print(f"tick failed: {exc}", file=sys.stderr)
        if a.once:
            return
        time.sleep(sig["pollEveryMin"] * 60)


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser(prog="dlmm_terminal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("screen", help="gates + flags on SOL DLMM pools")
    s.add_argument("--pool", action="append", help="pool address (repeatable); default: scan")
    s.add_argument("--size", type=float, help="planned SOL size (capped by the loss budget)")
    s.add_argument("--all", action="store_true", help="also print skipped pools")
    d = sub.add_parser("decide", help="log your enter/skip decision for a candidate")
    d.add_argument("pool")
    d.add_argument("decision", choices=["enter", "skip"])
    d.add_argument("--reason", default="")
    m = sub.add_parser("monitor", help="5-min PnL log + alerts + exit rules")
    m.add_argument("--wallet", default=os.environ.get("DLMM_WALLET"), required=not os.environ.get("DLMM_WALLET"))
    m.add_argument("--mode", choices=["alert", "auto"])
    m.add_argument("--dry-run", action="store_true", help="auto mode: build + simulate the close, don't send")
    m.add_argument("--once", action="store_true")
    sub.add_parser("followup", help="price 1h/6h/24h after each logged candidate")
    o = sub.add_parser("open", help="open a SOL-only Spot ladder (RH style, 5 slices) after the gates")
    o.add_argument("pool")
    o.add_argument("--sol", type=float, help="SOL to deposit (default: config, capped by the loss budget)")
    o.add_argument("--slices", type=int, help="default config position.ladderSlices (5)")
    o.add_argument("--bottom", type=float, help="range bottom %% below price (default 70)")
    o.add_argument("--top", type=float, default=0, help="where the ladder starts below the price, %% (0 = at the price)")
    o.add_argument("--strategy", default="spot", choices=["spot", "curve", "bidask"])
    o.add_argument("--dry-run", action="store_true", help="build + simulate only")
    o.add_argument("--owner", help="wallet for --dry-run (default DLMM_WALLET)")
    o.add_argument("--force", action="store_true", help="open even when a gate fails (logged as override)")
    o.add_argument("--force-size", action="store_true", help="allow a size over the loss-budget cap")
    o.add_argument("-y", "--yes", action="store_true", help="don't ask before sending")
    b = sub.add_parser("benchmark", help="profile a benchmark wallet's entries of the last N days")
    b.add_argument("--wallet", help="default config signal.benchmarkWallet")
    b.add_argument("--days", type=float, help="default config signal.benchmarkDays (20)")
    b.add_argument("--cached", action="store_true", help="reuse data/benchmark.json if still fresh")
    sg = sub.add_parser("signal", help="ping pools that pass the gates and match the benchmark wallet")
    sg.add_argument("--wallet", help="benchmark wallet (default config signal.benchmarkWallet)")
    sg.add_argument("--min-score", type=float, help="0..1, share of habitat dims the pool must match (default config: 1.0 = all)")
    sg.add_argument("--once", action="store_true", help="one scan, then exit")
    r = sub.add_parser("rpc", help="RPC (Helius) requests made by open / close / plan, from data/rpc_usage.jsonl")
    r.add_argument("--days", type=float, default=30)
    w = sub.add_parser("web", help="browser terminal: pools + entry check + chart + portfolio exits")
    w.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to open it from the phone over Tailscale")
    w.add_argument("--port", type=int, default=8790)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if a.cmd == "web":
        from . import web
        return web.serve(cfg, a.host, a.port)
    {"screen": cmd_screen, "decide": cmd_decide, "monitor": cmd_monitor, "followup": cmd_followup, "open": cmd_open,
     "benchmark": cmd_benchmark, "signal": cmd_signal, "rpc": cmd_rpc}[a.cmd](a, cfg)


if __name__ == "__main__":
    main()
