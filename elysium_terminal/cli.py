"""Elysium Terminal CLI.

  python3 -m elysium_terminal web                 # http://localhost:8893
  python3 -m elysium_terminal web --host 0.0.0.0  # phone over Tailscale
  python3 -m elysium_terminal screen              # pool table (OPEN/CAREFUL, fee 1h)
  python3 -m elysium_terminal portfolio [--wallet 0x..]
  python3 -m elysium_terminal open POOL --usd 400 [--slices 5 --bottom 60] [--dry-run]
  python3 -m elysium_terminal doctor              # env / key shape / RPC / Node, never prints the key
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

from . import chain

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

_FROM_FILE: dict[str, str] = {}


def env_files() -> tuple[Path, ...]:
    return (HERE / ".env",)


def _env_lines(p: Path):
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            yield k.strip().lstrip("\ufeff"), v.strip().strip('"').strip("'").strip("“”‘’")


def load_env(reload: bool = False) -> None:
    """elysium_terminal/.env only. A variable already set in the shell wins.
    RH_PRIVATE_KEY is ignored on purpose: that key belongs to Robinhood Chain."""
    os.environ.pop("RH_PRIVATE_KEY", None)
    seen = set()
    for p in env_files():
        if not p.exists():
            continue
        for k, v in _env_lines(p):
            if k == "RH_PRIVATE_KEY" or not v or k in seen:
                continue
            ours = k in _FROM_FILE and os.environ.get(k) == _FROM_FILE[k]
            if not os.environ.get(k) or (reload and ours):
                os.environ[k] = v
                _FROM_FILE[k] = v
                seen.add(k)
            elif ours:
                seen.add(k)


def practice_mode() -> bool:
    raw = os.environ.get("ELYSIUM_PRACTICE_MODE")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def env_report() -> list[str]:
    """Why ELYSIUM_PRIVATE_KEY is or is not read, without ever showing it."""
    out = []
    for p in env_files():
        if not p.exists():
            out.append(f"{p}: nggak ada (copy dari .env.example)")
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        if raw.lstrip().startswith("{\\rtf"):
            out.append(f"{p}: kesimpen sebagai RTF (TextEdit Rich Text). Format → Make Plain Text, save.")
            continue
        lines = raw.splitlines()
        hit = [i for i, ln in enumerate(lines) if ln.strip().startswith("ELYSIUM_PRIVATE_KEY")]
        if not hit:
            out.append(f"{p}: nggak ada baris ELYSIUM_PRIVATE_KEY=")
        for i in hit:
            v = lines[i].split("=", 1)[1].strip().strip('"').strip("'") if "=" in lines[i] else ""
            x = v[2:] if v.lower().startswith("0x") else v
            if not v:
                out.append(f"{p} baris {i + 1}: ELYSIUM_PRIVATE_KEY kosong")
            elif len(x) != 64:
                out.append(f"{p} baris {i + 1}: ELYSIUM_PRIVATE_KEY {len(x)} karakter (harus 64 hex, boleh pakai 0x)")
            elif not re.fullmatch(r"[0-9a-fA-F]{64}", x):
                out.append(f"{p} baris {i + 1}: ELYSIUM_PRIVATE_KEY ada karakter yang bukan hex")
            else:
                out.append(f"{p} baris {i + 1}: ELYSIUM_PRIVATE_KEY terisi, format kelihatan benar")
    return out


def load_config(path: str | None = None, overlay: bool = True) -> dict:
    from . import settings
    cfg = json.loads(Path(path or HERE / "config.json").read_text())
    for env, key in (("ELYSIUM_BANKROLL_USD", "bankrollUsd"), ("ELYSIUM_SIZE_USD", "defaultSizeUsd"),
                     ("ELYSIUM_LOSS_BUDGET_PCT", "lossBudgetPct")):
        v = os.environ.get(env, "").strip()
        if v:
            try:
                cfg["position"][key] = float(v)
            except ValueError:
                print(f"{env}={v!r} bukan angka; pakai config.json", file=sys.stderr)
    return settings.apply(cfg, None if overlay else {})


def append(name: str, row: dict) -> None:
    DATA.mkdir(exist_ok=True)
    with open(DATA / name, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def notify(text: str) -> None:
    print(text, flush=True)
    token = os.environ.get("ELYSIUM_TG_BOT_TOKEN") or os.environ.get("DLMM_TG_BOT_TOKEN")
    chat = os.environ.get("ELYSIUM_TG_CHAT_ID") or os.environ.get("DLMM_TG_CHAT_ID")
    if token and chat:
        try:
            body = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
            chain.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", timeout=10, data=body).read()
        except Exception as exc:
            print(f"  (telegram failed: {exc})", file=sys.stderr)


def make_index(cfg: dict):
    from . import activity
    return activity.Index(DATA, backfill_hours=float(cfg["scan"]["backfillHours"]), poll_sec=float(cfg["scan"]["pollSec"]))


def cmd_web(a, cfg):
    from . import web
    port = a.port or int(os.environ.get("ELYSIUM_WEB_PORT") or 8893)
    web.serve(cfg, host=a.host, port=port)


def _money(v, d=0):
    return "--" if v is None else f"${v:,.{d}f}"


def cmd_screen(a, cfg):
    from . import pools
    ix = make_index(cfg)
    print("Baca swap dari chain…", flush=True)
    try:
        ix.poll()
        ix.save()
    except Exception as exc:
        print(f"Index gagal baca chain: {exc}", flush=True)
        if not ix.pools:
            return
        print("Nampilin data yang sempat kebaca.", flush=True)
    try:
        d = pools.Book(ix, cfg).rows()
    except Exception as exc:
        print(f"Tabel gagal disusun: {exc}", flush=True)
        return
    rows = [r for r in d["rows"] if a.all or r["verdict"]["level"] in ("OPEN", "CAREFUL")]
    rows.sort(key=lambda r: -(r["feePctDay"].get("1h") or 0))
    hype = d.get("hypeUsd")
    print(f"{len(rows)} pool · HYPE {_money(hype)} · index {d['index'].get('coverageHours')}h"
          + (f" · index error: {d['index'].get('error')}" if d["index"].get("error") else ""))
    for r in rows[: a.top]:
        v = r["verdict"]
        tag = "v4" if r["dex"] == "v4" else ("ES" if r["venue"] == "elysiumswap" else "uni")
        print(f"{r['symbol'][:12]:12} {r['quoteSymbol']:5} {tag:3} fee {r['lpFeePct']}%  TVL {_money(r['tvlUsd'])}  mcap {_money(r['mcapUsd'])}"
              f"  1h {_money(r['fees'].get('1h'))} ({(r['feePctDay'].get('1h') or 0):.1f}%/d)  {v['level']}"
              f"  {' · '.join(v['gatesFailed'] + v['flags'])[:70]}  {r['pool']}")
    ping = d["ping"]["rows"]
    if ping:
        print("PING:", ", ".join(f"#{p['rank']} {p['symbol']}" for p in ping))
    elif not rows:
        print("Belum ada pool yang lolos gate. Coba `screen --all` buat lihat yang WAIT/SKIP.")


def cmd_portfolio(a, cfg):
    from . import node, pools, portfolio
    wallet = a.wallet or (node.trade_wallet().get("address")) or os.environ.get("ELYSIUM_WALLET")
    if not wallet:
        sys.exit("wallet: --wallet 0x.. (atau ELYSIUM_PRIVATE_KEY / ELYSIUM_WALLET di .env)")
    ix = make_index(cfg)
    try:
        pf = portfolio.portfolio(wallet, cfg, DATA, pools.Book(ix, cfg))
    except Exception as exc:
        sys.exit(f"portfolio gagal: {exc}")
    print(f"{wallet}: nilai {_money(pf['valueUsd'])} · fee {_money(pf['feesUsd'], 2)} · PnL {_money(pf['pnlUsd'], 2)}")
    for r in pf["rows"]:
        tag = "v4" if r["dex"] == "v4" else "v3"
        print(f"{r['symbol'][:12]:12}/{r['quoteSymbol']:5} {tag} {r['id']:10} {r['posCount']} NFT  {_money(r['valueUsd'])}  PnL {(r['pnlPct'] or 0):+.1f}%"
              f"  range {(r['rangePct'] if r['rangePct'] is not None else 0):.0f}% ({r['state']})  {' · '.join(e['msg'] for e in r['events'])}")


def cmd_open(a, cfg):
    from . import node
    pos = cfg["position"]
    hype = None
    try:
        hype = make_index(cfg).hype_usd()
    except Exception:
        hype = chain.hype_usd_from_slot()
    args = node.ladder_args(a.pool, a.usd, a.slices or pos["ladderSlices"], a.top if a.top is not None else pos["rangeTopPct"],
                            a.bottom or pos["rangeBottomPct"], hype)
    plan = node.run("open_ladder.mjs", args + ["--plan"])
    if not plan.get("ok"):
        sys.exit(f"plan gagal: {plan.get('error')}")
    p = plan["plan"]
    print(f"{p.get('dex')} {p.get('venue')}: {len(p['slices'])} slice, total {p['total']:.6g} {p.get('quoteSymbol')}, harga → −{p['realEnd']:.1f}%")
    for i, s in enumerate(p["slices"], 1):
        print(f"  {i}. −{s['nearPct']:.1f}% → −{s['farPct']:.1f}%   {s['amount']:.6g}")
    dry = a.dry_run or practice_mode()
    if dry:
        w = node.trade_wallet().get("address") or a.owner
        if not w:
            sys.exit("dry run butuh --owner 0x.. (atau key di .env)")
        print(node.run("open_ladder.mjs", args + ["--dry-run", "--owner", w]))
        return
    if input("Kirim transaksi beneran? ketik y: ").strip().lower() != "y":
        return print("batal")
    res = node.run("open_ladder.mjs", args, timeout=600)
    print(json.dumps(res, indent=1)[:2000])


def cmd_doctor(a, cfg):
    from . import node
    print("== .env ==")
    for line in env_report():
        print(" ", line)
    print("== wallet ==")
    w = node.trade_wallet(force=True)
    print("  ", f"OK: {w['address']}" if w.get("ok") else f"BELUM: {w.get('error')}")
    print("== lainnya ==")
    print("  ALCHEMY_API_KEY:", "terisi" if os.environ.get("ALCHEMY_API_KEY") else "kosong (baca/kirim lewat RPC publik, rate-limited)")
    print("  practice mode:", "NYALA (semua tombol cuma simulasi)" if practice_mode() else "mati (tombol kirim transaksi beneran)")
    print("  ELYSIUM_WEB_PIN:", "terisi (HP / MacBook bisa trading)" if os.environ.get("ELYSIUM_WEB_PIN") else "kosong (trading cuma dari Mac ini)")
    print("  ELYSIUM_WEB_PORT:", os.environ.get("ELYSIUM_WEB_PORT") or "8893 (default)")
    print("  executor:", node.ready() or "OK")
    try:
        cid = chain.chain_id()
        print("  chain id:", cid, "OK" if cid == chain.CHAIN_ID else "BUKAN 99901")
        print("  RPC: block", chain.block_number())
        meta = chain.token_meta([chain.USDC, chain.WHYPE])
        print(f"  USDC: {meta.get(chain.USDC, {}).get('symbol')} decimals {meta.get(chain.USDC, {}).get('decimals')}")
        print(f"  WHYPE: {meta.get(chain.WHYPE, {}).get('symbol')} decimals {meta.get(chain.WHYPE, {}).get('decimals')}")
        px = chain.hype_usd_from_slot()
        print("  HYPE dari pool USDC/WHYPE 1%:", f"${px:,.2f}" if px else "nggak kebaca")
        b = chain.budget_view()
        print(f"  RPC hari ini ({b['day']} UTC): publik {b['public']} · Alchemy {b['alchemy']}/{b['alchemyCap']} · cap-skip {b['skipped']} · error {b['errors']}")
    except Exception as e:
        print("  RPC: GAGAL", e)


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser(prog="elysium_terminal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("web")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int)
    s = sub.add_parser("screen")
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--all", action="store_true", help="semua level, bukan cuma OPEN/CAREFUL")
    p = sub.add_parser("portfolio")
    p.add_argument("--wallet")
    o = sub.add_parser("open")
    o.add_argument("pool")
    o.add_argument("--usd", type=float, required=True)
    o.add_argument("--slices", type=int)
    o.add_argument("--top", type=float)
    o.add_argument("--bottom", type=float)
    o.add_argument("--dry-run", action="store_true")
    o.add_argument("--owner")
    sub.add_parser("doctor")
    a = ap.parse_args(argv)
    cfg = load_config()
    {"web": cmd_web, "screen": cmd_screen, "portfolio": cmd_portfolio, "open": cmd_open, "doctor": cmd_doctor}[a.cmd](a, cfg)
