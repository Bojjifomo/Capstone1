#!/usr/bin/env python3
"""RH Wallet Finder: top Uniswap V4 LP wallets on Robinhood Chain.

  python3 finder.py                 dashboard + background sync (default)
  python3 finder.py sync            load new LP activity once
  python3 finder.py score           price positions and rank wallets once
  python3 finder.py top [-n 30] [--real] [--1d]
  python3 finder.py check 0xWALLET  one wallet: our metrics + LP Agent
  python3 finder.py import FILE     add wallets (one per line, optional label)
  python3 finder.py set-rpc URL     save the RPC link to .env

Only the Python standard library is used.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import db  # noqa: E402


def _rpcs(cfg):
    from rpc import make_rpcs
    if not cfg["RPC_URL"]:
        sys.exit("No RPC link yet. Run:  python3 finder.py set-rpc 'https://…'  (or use ⚙ Settings in the dashboard)")
    return make_rpcs(cfg)


def cmd_sync(args, cfg):
    import indexer
    if args.days:
        cfg["BACKFILL_DAYS"] = str(args.days)
    con = db.connect()
    rpc, call_rpc = _rpcs(cfg)
    while True:
        res = indexer.sync(con, rpc, cfg, max_segments=args.segments, call_rpc=call_rpc)
        print(json.dumps(res))
        if res["done"] or args.segments:
            break
    print(f"{rpc.calls:,} RPC calls")


def cmd_score(args, cfg):
    from scorer import Scorer
    con = db.connect()
    _, call_rpc = _rpcs(cfg)
    print(json.dumps(Scorer(con, call_rpc, cfg).run()))
    print(f"{call_rpc.calls:,} RPC calls")


def cmd_top(args, cfg):
    import server
    con = db.connect()
    view = ("d1" if args.d1 else "") + ("real" if args.real else "raw")
    rows, _ = server.wallet_rows(con, view)
    if not rows:
        sys.exit("No scored wallets yet. Run sync and score first.")
    hdr = f"{'#':>3} {'score':>5}  {'wallet':<42} {'verdict':<7} {'net$':>9} {'roi%':>7} {'win%':>5} " \
          f"{'dd%':>6} {'pos':>4} {'hold':>6} {'copy':>4}  style"
    print(hdr)
    print("-" * len(hdr))
    for r in rows[:args.n]:
        hold = r["duration"] or 0
        hold_s = f"{hold / 3600:.1f}h" if hold >= 3600 else f"{hold / 60:.0f}m"
        print(f"{r['rank']:>3} {r['score']:>5.1f}  {r['addr']:<42} {r['verdict']:<7} {r['net']:>9.0f} "
              f"{r['roi']:>7.1f} {r['win']:>5.0f} {r['dd']:>6.1f} {r['pos']:>4} {hold_s:>6} {r['copy']:>4}  "
              f"{r.get('style') or ''}")


def cmd_check(args, cfg):
    import lpagent
    import server
    con = db.connect()
    addr = args.wallet.lower()
    d = server.wallet_detail(con, addr)
    m = (d["metrics"] or {}).get("real" if args.real else "raw")
    print(f"Wallet {addr}")
    print(f"  LP Agent: {lpagent.link(addr)}")
    print(f"  Explorer: {d['explorer']}")
    if m:
        print(f"  score {m['score']} ({m['verdict']}), net ${m['net']:,.0f}, ROI {m['roi']:.1f}%/mo, "
              f"win {m['win']:.0f}%, closed {m['closed']}, open {m['open']}, style {m.get('style')}")
    else:
        print(f"  not scored yet ({len(d['positions'])} positions in the database)")
    key = cfg["LPAGENT_API_KEY"]
    if not key:
        print("  (set LPAGENT_API_KEY in .env to pull LP Agent's own numbers)")
        return
    try:
        res = lpagent.check(con, addr, key, fresh=True)
    except lpagent.LPAgentError as e:
        sys.exit(f"  LP Agent: {e}")
    for proto, vals in (res.get("overview") or {}).items():
        print(f"  LP Agent {proto}: " + ", ".join(f"{k}={v:,.2f}" for k, v in vals.items() if v is not None))
    for o in res.get("opening") or []:
        print(f"    open {o['pair']}: value {o['value']}, pnl {o['pnl']}")
    for e in res.get("errors") or []:
        print("  ! " + e)


def cmd_import(args, cfg):
    import server
    con = db.connect()
    text = Path(args.file).read_text() if args.file != "-" else sys.stdin.read()
    print(server.import_text(con, text, "file"))


def cmd_set_rpc(args, cfg):
    config.save_env({"RPC_URL": args.url.strip()})
    print(f"Saved to {config.ENV_FILE}: {config.mask(args.url.strip())}")
    from rpc import RPC
    try:
        cid = int(RPC(args.url.strip()).call("eth_chainId", []), 16)
        print(f"chain id {cid}" + ("  ✓ Robinhood Chain" if cid == config.CHAIN_ID else "  ✗ not Robinhood Chain (4663)"))
    except Exception as e:  # noqa: BLE001
        print(f"Couldn't reach it yet: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="dashboard + background sync")
    s.add_argument("--port", type=int)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-sync", action="store_true", help="only serve what's in the database")
    s = sub.add_parser("sync")
    s.add_argument("--days", type=float, help="history to load (default BACKFILL_DAYS)")
    s.add_argument("--segments", type=int, help="stop after this many segments")
    sub.add_parser("score")
    s = sub.add_parser("top")
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--real", action="store_true")
    s.add_argument("--1d", dest="d1", action="store_true")
    s = sub.add_parser("check")
    s.add_argument("wallet")
    s.add_argument("--real", action="store_true")
    s = sub.add_parser("import")
    s.add_argument("file")
    s = sub.add_parser("set-rpc")
    s.add_argument("url")
    args = ap.parse_args()
    cfg = config.load_env()
    t = time.time()
    if args.cmd in (None, "serve"):
        import server
        server.serve(port=getattr(args, "port", None), open_browser=not getattr(args, "no_browser", False),
                     run_worker=not getattr(args, "no_sync", False))
        return
    {"sync": cmd_sync, "score": cmd_score, "top": cmd_top, "check": cmd_check, "import": cmd_import,
     "set-rpc": cmd_set_rpc}[args.cmd](args, cfg)
    if args.cmd in ("sync", "score"):
        print(f"done in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
