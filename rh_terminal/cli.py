"""RH Terminal CLI.

  python3 -m rh_terminal web                 # browser terminal on http://localhost:8892 (RH_WEB_PORT)
  python3 -m rh_terminal web --host 0.0.0.0  # reachable from the phone over Tailscale
  python3 -m rh_terminal screen              # pool table in the terminal (top by fee 1h, entry check)
  python3 -m rh_terminal portfolio [--wallet 0x..]
  python3 -m rh_terminal open POOL --usd 400 [--slices 5 --bottom 60] [--dry-run]
  python3 -m rh_terminal doctor              # .env / RH Secrets / key / RPC / Node, never shows the key
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from . import chain

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ICLOUD_SCRIPTABLE = Path.home() / "Library" / "Mobile Documents" / "iCloud~dk~simonbs~Scriptable" / "Documents"

_FROM_FILE: dict[str, str] = {}
_SECRETS_NOTE: list[str] = []


def env_files() -> tuple[Path, Path]:
    return (HERE / ".env", HERE.parent / ".env")


def _env_lines(p: Path):
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            yield k.strip().lstrip("﻿"), v.strip().strip('"').strip("'").strip("“”‘’")


def secrets_file() -> Path | None:
    """RH Secrets from the Scriptable folder in iCloud Drive (the phone's own keys), if this Mac has it."""
    p = os.environ.get("RH_SECRETS_FILE")
    for c in ([Path(p).expanduser()] if p else []) + [ICLOUD_SCRIPTABLE / "RH Secrets.js", ICLOUD_SCRIPTABLE / "RHSecrets.js"]:
        if c.is_file():
            return c
    return None


def read_secrets(p: Path) -> dict[str, str]:
    """The string and boolean values of RH Secrets.js (a JS object literal), without running it."""
    txt = p.read_text(encoding="utf-8", errors="replace")
    txt = re.sub(r"//[^\n]*", "", txt)
    out = {k: v for k, v in re.findall(r"\b([A-Z][A-Z0-9_]*)\s*:\s*[\"']([^\"']*)[\"']", txt)}
    for k, v in re.findall(r"\b([A-Z][A-Z0-9_]*)\s*:\s*(true|false)\b", txt):
        out[k] = v
    return out


def load_env(reload: bool = False) -> None:
    """rh_terminal/.env, then the repo root's .env, then RH Secrets in iCloud (keys only).
    A variable set in the shell wins. reload=True (the web, on every config read) re-reads files,
    so a key pasted into .env works without restarting."""
    seen = set()
    for p in env_files():
        if not p.exists():
            continue
        for k, v in _env_lines(p):
            if not v or k in seen:
                continue
            ours = k in _FROM_FILE and os.environ.get(k) == _FROM_FILE[k]
            if not os.environ.get(k) or (reload and ours):
                os.environ[k] = v
                _FROM_FILE[k] = v
                seen.add(k)
            elif ours:
                seen.add(k)
    sf = secrets_file()
    _SECRETS_NOTE.clear()
    if sf:
        try:
            sec = read_secrets(sf)
        except OSError as e:
            _SECRETS_NOTE.append(f"{sf}: nggak kebaca ({e})")
            return
        for src, dst in (("PRIVATE_KEY", "RH_PRIVATE_KEY"), ("ALCHEMY_API_KEY", "ALCHEMY_API_KEY"),
                         ("PRACTICE_MODE", "RH_PRACTICE_MODE")):
            v = (sec.get(src) or "").strip()
            # .env wins over RH Secrets, and the shell wins over both: a value from Secrets only
            # fills what nothing else set (or replaces what Secrets itself set on an earlier read).
            if dst in seen or (os.environ.get(dst) and _FROM_FILE.get(dst) != os.environ.get(dst)):
                continue
            if v and (not os.environ.get(dst) or reload):
                os.environ[dst] = v
                _FROM_FILE[dst] = v
                _SECRETS_NOTE.append(f"{dst} dari {sf.name}")


def practice_mode() -> bool:
    return str(os.environ.get("RH_PRACTICE_MODE", "")).strip().lower() in ("1", "true", "yes", "on")


def env_report() -> list[str]:
    """Why RH_PRIVATE_KEY is or is not read, without ever showing it."""
    out = []
    for p in env_files():
        if not p.exists():
            out.append(f"{p}: nggak ada")
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        if raw.lstrip().startswith("{\\rtf"):
            out.append(f"{p}: kesimpen sebagai RTF (TextEdit Rich Text). Buka, Format → Make Plain Text, save.")
            continue
        lines = raw.splitlines()
        hit = [i for i, l in enumerate(lines) if l.strip().startswith("RH_PRIVATE_KEY")]
        if not hit:
            out.append(f"{p}: nggak ada baris RH_PRIVATE_KEY=")
        for i in hit:
            v = lines[i].split("=", 1)[1].strip().strip('"').strip("'") if "=" in lines[i] else ""
            x = v[2:] if v.lower().startswith("0x") else v
            if not v:
                out.append(f"{p} baris {i + 1}: RH_PRIVATE_KEY kosong")
            elif len(x) != 64:
                out.append(f"{p} baris {i + 1}: RH_PRIVATE_KEY {len(x)} karakter (harus 64 hex, boleh pakai 0x)")
            elif not re.fullmatch(r"[0-9a-fA-F]{64}", x):
                out.append(f"{p} baris {i + 1}: RH_PRIVATE_KEY ada karakter yang bukan hex (spasi / kutip miring?)")
            else:
                out.append(f"{p} baris {i + 1}: RH_PRIVATE_KEY terisi, format kelihatan benar")
    sf = secrets_file()
    out.append(f"RH Secrets (iCloud Scriptable): {sf if sf else 'nggak ketemu'}")
    out += _SECRETS_NOTE
    return out


def load_config(path: str | None = None, overlay: bool = True) -> dict:
    """config.json, then sizing from .env, then what you set in the web (⚙, data/settings.json)."""
    from . import settings
    cfg = json.loads(Path(path or HERE / "config.json").read_text())
    for env, key in (("RH_BANKROLL_USD", "bankrollUsd"), ("RH_SIZE_USD", "defaultSizeUsd"), ("RH_LOSS_BUDGET_PCT", "lossBudgetPct")):
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
    """Print, and send to Telegram when RH_TG_BOT_TOKEN + RH_TG_CHAT_ID (or DLMM's) are set."""
    print(text, flush=True)
    token = os.environ.get("RH_TG_BOT_TOKEN") or os.environ.get("DLMM_TG_BOT_TOKEN")
    chat = os.environ.get("RH_TG_CHAT_ID") or os.environ.get("DLMM_TG_CHAT_ID")
    if token and chat:
        try:
            body = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}).encode()
            chain.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", timeout=10, data=body).read()
        except Exception as exc:
            print(f"  (telegram failed: {exc})", file=sys.stderr)


def make_index(cfg: dict):
    from . import activity
    return activity.Index(DATA, backfill_hours=float(cfg["scan"]["backfillHours"]), poll_sec=float(cfg["scan"]["pollSec"]))


# ------------------------------------------------------------------ commands ----
def cmd_web(a, cfg):
    from . import web
    port = a.port or int(os.environ.get("RH_WEB_PORT") or 8892)
    web.serve(cfg, host=a.host, port=port)


def _money(v, d=0):
    return "--" if v is None else f"${v:,.{d}f}"


def cmd_screen(a, cfg):
    from . import pools
    ix = make_index(cfg)
    print("Baca swap dari chain…", flush=True)
    ix.poll()
    ix.save()
    d = pools.Book(ix, cfg).rows()
    rows = [r for r in d["rows"] if a.all or r["verdict"]["level"] in ("OPEN", "CAREFUL")]
    rows.sort(key=lambda r: -(r["feePctDay"].get("1h") or 0))
    print(f"{len(rows)} pool · ETH ${d['ethUsd'] or 0:,.0f} · index {d['index']['coverageHours']}h")
    for r in rows[: a.top]:
        v = r["verdict"]
        print(f"{r['symbol'][:12]:12} {r['quoteSymbol']:4} fee {r['lpFeePct']}%  TVL {_money(r['tvlUsd'])}  mcap {_money(r['mcapUsd'])}"
              f"  1h {_money(r['fees'].get('1h'))} ({(r['feePctDay'].get('1h') or 0):.1f}%/d)  {v['level']}"
              f"  {' · '.join(v['gatesFailed'] + v['flags'])[:80]}  {r['pool']}")
    ping = d["ping"]["rows"]
    if ping:
        print("PING:", ", ".join(f"#{p['rank']} {p['symbol']}" for p in ping))


def cmd_portfolio(a, cfg):
    from . import node, pools, portfolio
    wallet = a.wallet or (node.trade_wallet().get("address")) or os.environ.get("RH_WALLET")
    if not wallet:
        sys.exit("wallet: --wallet 0x.. (atau RH_PRIVATE_KEY / RH_WALLET di .env)")
    ix = make_index(cfg)
    pf = portfolio.portfolio(wallet, cfg, DATA, pools.Book(ix, cfg))
    print(f"{wallet}: nilai {_money(pf['valueUsd'])} · fee {_money(pf['feesUsd'], 2)} · PnL {_money(pf['pnlUsd'], 2)}")
    for r in pf["rows"]:
        print(f"{r['symbol'][:12]:12}/{r['quoteSymbol']:4} {r['posCount']} slice  {_money(r['valueUsd'])}  PnL {(r['pnlPct'] or 0):+.1f}%"
              f"  range {(r['rangePct'] if r['rangePct'] is not None else 0):.0f}% ({r['state']})  {' · '.join(e['msg'] for e in r['events'])}")


def cmd_open(a, cfg):
    from . import node, pools
    pos = cfg["position"]
    eth = make_index(cfg).eth_usd()
    args = node.ladder_args(a.pool, a.usd, a.slices or pos["ladderSlices"], a.top if a.top is not None else pos["rangeTopPct"],
                            a.bottom or pos["rangeBottomPct"], eth)
    plan = node.run("open_ladder.mjs", args + ["--plan"])
    if not plan.get("ok"):
        sys.exit(f"plan gagal: {plan.get('error')}")
    p = plan["plan"]
    print(f"{len(p['slices'])} slice, total {p['total']:.6g} ({'USDG' if p['quote'] == chain.USDG else 'ETH'}), harga → −{p['realEnd']:.1f}%")
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
    print("== .env / RH Secrets ==")
    for line in env_report():
        print(" ", line)
    print("== wallet ==")
    w = node.trade_wallet(force=True)
    print("  ", f"OK: {w['address']}" if w.get("ok") else f"BELUM: {w.get('error')}")
    print("== lainnya ==")
    print("  ALCHEMY_API_KEY:", "terisi" if os.environ.get("ALCHEMY_API_KEY") else "kosong (kirim tx lewat RPC publik)")
    print("  practice mode:", "NYALA (semua tombol cuma simulasi)" if practice_mode() else "mati (tombol kirim transaksi beneran)")
    print("  RH_WEB_PIN:", "terisi (HP / MacBook bisa trading)" if os.environ.get("RH_WEB_PIN") else "kosong (trading cuma dari Mac ini)")
    print("  RH_WEB_PORT:", os.environ.get("RH_WEB_PORT") or "8892 (default)")
    print("  executor:", node.ready() or "OK")
    print("== RPC (dari Mac ini) ==")
    import socket
    ok_wide = False
    for url, span in chain.endpoints():
        host = url.split("/")[2]
        try:
            ips = sorted({a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
        except OSError as e:
            ips = [f"DNS gagal: {e}"]
        try:
            req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": chain.UA})
            with chain.urlopen(req, timeout=12) as r:
                res = "OK block " + str(int(json.load(r)["result"], 16))
            ok_wide = ok_wide or span >= 1_000_000
        except urllib.error.HTTPError as e:
            res = f"HTTP {e.code} {e.read(160).decode('utf-8', 'replace')[:120]}"
        except Exception as e:
            res = f"GAGAL {str(e)[:120]}"
        label = "alchemy" if "alchemy" in host else host
        print(f"  {label:40} log ≤{span:>10,} blok   IP {', '.join(ips)[:46]:46}  {res}")
    if not ok_wide:
        print("  ⚠ RPC resmi & PublicNode nggak bisa dijangkau: index swap pakai dRPC/Daybreak, Portfolio nyari NFT lewat ownerOf (lebih lambat).")
        print("    Kalau di atas ada 'refused' / IP aneh (bukan IP Cloudflare/Google): DNS provider internet lu ngeblok.")
        print("    Fix: System Settings → Network → Wi-Fi/Ethernet → Details → DNS → ganti ke 1.1.1.1 dan 8.8.8.8, lalu restart RH Terminal.")


def main(argv=None):
    load_env()
    ap = argparse.ArgumentParser(prog="rh_terminal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
