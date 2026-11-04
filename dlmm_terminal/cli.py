"""DLMM terminal CLI.

  python -m dlmm_terminal screen                 # scan SOL pools, gates + flags, log every candidate
  python -m dlmm_terminal screen --pool ADDR     # one pool (or several --pool)
  python -m dlmm_terminal decide ADDR enter|skip --reason "..."
  python -m dlmm_terminal monitor --wallet W     # every 5 min: log PnL, alerts, exit rules (auto TP/SL)
  python -m dlmm_terminal followup               # fill price 1h/6h/24h after each candidate
  python -m dlmm_terminal web                    # browser terminal on http://localhost:8790
  python -m dlmm_terminal open POOL --sol 8      # open a 5-slice Spot SOL-only ladder (RH style), you confirm
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

from . import api, exits, filters

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


def notify(text: str) -> None:
    print(text, flush=True)
    token, chat = os.environ.get("DLMM_TG_BOT_TOKEN"), os.environ.get("DLMM_TG_CHAT_ID")
    if token and chat:
        try:
            body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
            api.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", timeout=10, data=body).read()
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
    w = sub.add_parser("web", help="browser terminal: pools + entry check + chart + portfolio exits")
    w.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to open it from the phone over Tailscale")
    w.add_argument("--port", type=int, default=8790)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if a.cmd == "web":
        from . import web
        return web.serve(cfg, a.host, a.port)
    {"screen": cmd_screen, "decide": cmd_decide, "monitor": cmd_monitor, "followup": cmd_followup, "open": cmd_open}[a.cmd](a, cfg)


if __name__ == "__main__":
    main()
