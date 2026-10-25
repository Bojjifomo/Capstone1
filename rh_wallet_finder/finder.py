#!/usr/bin/env python3
"""RH Wallet Finder: top Uniswap V4 LP wallets on Robinhood Chain.

  python3 finder.py                 the Streamlit app + background sync (default)
  python3 finder.py worker          background sync only, no UI
  python3 finder.py sync            load new LP activity once
  python3 finder.py score           price positions and rank wallets once
  python3 finder.py top [--kind multiday|degen] [-n 30] [--real] [--1d]
  python3 finder.py check 0xWALLET  one wallet: our metrics + LP Agent
  python3 finder.py import FILE     add wallets (one per line, optional label)
  python3 finder.py set-rpc URL     save the RPC link to .env
  python3 finder.py import-smart-lp saved wallets + pool rankings from RH Smart LP Finder
  python3 finder.py publish [--out FILE]  upload the boards to the web showcase (or write them to FILE)
  python3 finder.py compare FILE    our figures next to rows copied from RH Discovery (FILE, or - to paste)

Sync, scoring and the CLI use only the standard library; the app needs
`pip install -r requirements.txt` (Streamlit).
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
        sys.exit("No RPC link yet. Run:  python3 finder.py set-rpc 'https://…'  (or use the app's Settings tab)")
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
    import queries
    con = db.connect()
    view = ("d1" if args.d1 else "") + ("real" if args.real else "raw")
    rows, _ = queries.wallet_rows(con, view, args.kind)
    if not rows:
        sys.exit("No scored wallets yet. Run sync and score first.")
    if args.json:
        print(json.dumps(rows[:args.n], default=str))
        return
    hdr = f"{'#':>3} {'score':>5}  {'wallet':<42} {'class':<8} {'verdict':<7} {'net$':>9} {'roi%':>7} {'win%':>5} " \
          f"{'dd%':>6} {'trades':>6} {'hold':>6} {'copy':>4}  style"
    print(hdr)
    print("-" * len(hdr))
    for r in rows[:args.n]:
        hold = r["duration"] or 0
        hold_s = f"{hold / 3600:.1f}h" if hold >= 3600 else f"{hold / 60:.0f}m"
        win = f"{r['win']:>5.0f}" if r.get("win") is not None else f"{'–':>5}"
        print(f"{r['rank']:>3} {r['score']:>5.1f}  {r['addr']:<42} {r['kind']:<8} {r['verdict']:<7} {r['net']:>9.0f} "
              f"{r['roi']:>7.1f} {win} {r['dd']:>6.1f} {r.get('trades', r['pos']):>6} {hold_s:>6} {r['copy']:>4}  "
              f"{r.get('style') or ''}")


def cmd_check(args, cfg):
    import lpagent
    import queries
    con = db.connect()
    addr = args.wallet.lower()
    d = queries.wallet_detail(con, addr)
    m = (d["metrics"] or {}).get("real" if args.real else "raw")
    if args.json:
        print(json.dumps({**{k: d[k] for k in ("addr", "explorer", "lpagent_link", "label", "smart_lp")},
                          "metrics": m, "positions": d["positions"][:args.positions]}, default=str))
        return
    print(f"Wallet {addr}")
    print(f"  LP Agent: {lpagent.link(addr)}")
    print(f"  Explorer: {d['explorer']}")
    if m:
        win = f"{m['win']:.0f}%" if m.get("win") is not None else "–"
        print(f"  {m.get('kind', '?')} player, score {m['score']} ({m['verdict']}), net ${m['net']:,.0f}, "
              f"total ${m.get('total', m['net']):,.0f}, ROI {m['roi']:.1f}%/mo, win {win}, "
              f"trades {m.get('trades', m['pos'])} ({m.get('closed_trades', m['closed'])} closed), "
              f"open {m['open']}, style {m.get('style')}")
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


def cmd_status(args, cfg):
    import queries
    print(json.dumps(queries.status(db.connect()), default=str))


def cmd_twin(args, cfg):
    import queries
    res = queries.similar(db.connect(), args.wallet, "real" if args.real else "raw")
    if "rows" in res:
        res["rows"] = res["rows"][:args.n]
    print(json.dumps(res, default=str))


def cmd_smart_lp(args, cfg):
    import queries
    import smartlp
    con = db.connect()
    scored = {r[0]: r[1] for r in con.execute("SELECT addr, score FROM wallets WHERE score IS NOT NULL")}
    pools = [dict(r) for r in con.execute("""SELECT pool, COUNT(*) AS ranked, MAX(total) AS found,
                                             MAX(computed_at) AS computed_at FROM slf_ranks
                                             GROUP BY pool ORDER BY computed_at DESC""")]
    names = queries.pool_names(con, {p["pool"] for p in pools})
    for p in pools:
        p["pair"] = names.get(p["pool"])
    best = sorted(smartlp.best_ranks(con).values(), key=lambda b: (b["rank"], -(b["pnl_usd"] or 0)))
    for b in best:
        b["pair"], b["our_score"] = names.get(b["pool"]), scored.get(b["addr"])
    print(json.dumps({"dir": str(config.smart_lp_finder_dir() or ""), "db": str(smartlp.db_path() or ""),
                      "server": smartlp.server_url(), "pools": pools,
                      "saved": con.execute("SELECT COUNT(*) FROM labels WHERE source='smart-lp-finder'").fetchone()[0],
                      "best": best[:args.n]}, default=str))


def cmd_web_check(args, cfg):
    import urllib.request
    import publish
    con = db.connect()
    out = {"published": publish.last(con)}
    link = cfg.get("VERCEL_PUBLISH_URL")
    if not link:
        out["live"] = {"error": "no publish link in Settings (Web showcase)"}
    else:
        from urllib.parse import urlsplit
        u = urlsplit(config.split_publish_link(link)[0])
        site = f"{u.scheme}://{u.netloc}"
        out["site"] = site
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    site + "/api/snapshot", headers={"user-agent": "rh-wallet-finder/1.0",
                                                     "accept-encoding": "gzip"}), timeout=30) as r:
                body = r.read()
                if r.headers.get("content-encoding") == "gzip":
                    import gzip
                    body = gzip.decompress(body)
                snap = json.loads(body)
            gen = snap.get("generated") or 0
            out["live"] = {"generated": gen, "age_min": round((time.time() - gen) / 60, 1),
                           "wallets": len((snap.get("views") or {}).get("raw") or []), "v": snap.get("v"),
                           "status": snap.get("status")}
        except Exception as e:  # noqa: BLE001 - any failure is the answer here
            out["live"] = {"error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, default=str))


def cmd_import(args, cfg):
    import queries
    con = db.connect()
    text = Path(args.file).read_text() if args.file != "-" else sys.stdin.read()
    print(queries.import_text(con, text, "file"))


def cmd_import_smart_lp(args, cfg):
    import smartlp
    path = Path(args.db) if args.db else smartlp.db_path()
    if not path:
        sys.exit("RH Smart LP Finder not found next to this folder. Pass --db path/to/lp-finder.sqlite "
                 "or set SMART_LP_FINDER_DIR.")
    print(f"{path}: {smartlp.import_all(db.connect(), path)}")


def _port_free(port):
    import socket
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _ours_on(port):
    """pids listening on port that are this folder's app (an older Wallet Finder),
    found with lsof by their working folder. [] without lsof."""
    import subprocess
    here = str(Path(__file__).resolve().parent)

    def run(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    out = []
    for pid in {int(p) for p in run("lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN").split() if p.isdigit()}:
        cwd = next((ln[1:] for ln in run("lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn").splitlines()
                    if ln.startswith("n")), "")
        cmd = run("ps", "-o", "command=", "-p", str(pid))
        if "streamlit_app.py" in cmd and (cwd == here or here in cmd):
            out.append(pid)
    return out


def _free_port(port):
    """The port to run on: an older Wallet Finder still holding it (e.g. one
    from before a git pull) is stopped; anything else there, the next free port."""
    import os
    import signal
    if _port_free(port):
        return port
    ours = _ours_on(port)
    for pid in ours:
        print(f"Wallet Finder is already running on port {port} (pid {pid}): stopping it so this one "
              f"(maybe updated) takes over…")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(40 if ours else 0):
        if _port_free(port):
            return port
        time.sleep(0.25)
    for pid in ours:  # didn't stop politely
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if ours:
        time.sleep(1)
        if _port_free(port):
            return port
    for p in range(port + 1, port + 20):
        if _port_free(p):
            print(f"Port {port} is taken by another program, using {p} instead.")
            return p
    sys.exit(f"Ports {port}–{port + 19} are all taken. Close what uses them, or run: python3 finder.py app --port 8600")


def cmd_publish(args, cfg):
    import publish
    con = db.connect()
    if args.out:
        Path(args.out).write_text(json.dumps(publish.snapshot(con), separators=(",", ":")))
        print(f"Wrote {args.out}")
        return
    try:
        print(publish.publish(con, cfg))
    except publish.PublishError as e:
        sys.exit(str(e))


def cmd_compare(args, cfg):
    import compare
    if args.file == "-":
        print("Paste RH Discovery rows, then Ctrl+D:", file=sys.stderr)
        text = sys.stdin.read()
    else:
        text = Path(args.file).read_text()
    rows = compare.parse(text)
    if not rows:
        sys.exit("No RH Discovery rows found: copy rows of its table (with their 0x…  wallet cell).")
    con = db.connect()
    print(compare.table(compare.compare(con, rows, cfg=cfg)))


def cmd_app(args, cfg):
    """streamlit run streamlit_app.py, from this folder so .streamlit/config.toml applies."""
    import subprocess
    here = Path(__file__).resolve().parent
    try:
        import streamlit
    except ImportError:
        sys.exit("Streamlit isn't installed. Run:  python3 -m pip install -U -r requirements.txt")
    if tuple(int(x) for x in streamlit.__version__.split(".")[:2]) < (1, 40):
        sys.exit(f"Streamlit {streamlit.__version__} is too old. Run:  python3 -m pip install -U -r requirements.txt")
    port = _free_port(int(args.port or cfg["PORT"]))
    print(f"RH Wallet Finder: http://localhost:{port}  (keep this window open; Ctrl+C stops it)")
    cmd = [sys.executable, "-m", "streamlit", "run", "streamlit_app.py",
           "--server.port", str(port), "--server.address", "127.0.0.1"]
    if args.no_browser:
        cmd += ["--server.headless", "true"]
    sys.exit(subprocess.call(cmd, cwd=here))


def cmd_worker(args, cfg):
    from worker import Worker
    w = Worker()
    w.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        w.stop()


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
    s = sub.add_parser("app", help="the Streamlit app + background sync (default)")
    s.add_argument("--port", type=int)
    s.add_argument("--no-browser", action="store_true")
    sub.add_parser("worker", help="background sync + scoring only, no UI")
    s = sub.add_parser("sync")
    s.add_argument("--days", type=float, help="history to load (default BACKFILL_DAYS)")
    s.add_argument("--segments", type=int, help="stop after this many segments")
    sub.add_parser("score")
    s = sub.add_parser("top")
    s.add_argument("--kind", choices=["multiday", "degen"], help="one player class, ranked on its own")
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--real", action="store_true")
    s.add_argument("--1d", dest="d1", action="store_true")
    s.add_argument("--json", action="store_true", help="the rows as JSON")
    s = sub.add_parser("check")
    s.add_argument("wallet")
    s.add_argument("--real", action="store_true")
    s.add_argument("--json", action="store_true", help="metrics + latest positions as JSON (no LP Agent call)")
    s.add_argument("--positions", type=int, default=50, help="with --json: how many positions to include")
    sub.add_parser("status", help="sync / scoring / RPC state as JSON")
    s = sub.add_parser("twin", help="wallets whose LP style is closest to this one, as JSON")
    s.add_argument("wallet")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--real", action="store_true")
    s = sub.add_parser("smart-lp", help="RH Smart LP Finder pools + best placings next to our score, as JSON")
    s.add_argument("-n", type=int, default=30)
    sub.add_parser("web-check", help="the snapshot the web showcase serves now vs the last publish, as JSON")
    s = sub.add_parser("import")
    s.add_argument("file")
    s = sub.add_parser("import-smart-lp", help="saved wallets + pool rankings from RH Smart LP Finder")
    s.add_argument("--db", help="path to its data/lp-finder.sqlite (default: found next to this folder)")
    s = sub.add_parser("publish", help="upload the boards to the web showcase (Vercel)")
    s.add_argument("--out", help="write the snapshot JSON to this file instead of uploading it")
    s = sub.add_parser("compare", help="our figures next to rows copied from RH Discovery")
    s.add_argument("file", help="text file with the copied rows, or - to paste them")
    s = sub.add_parser("set-rpc")
    s.add_argument("url")
    args = ap.parse_args()
    cfg = config.load_env()
    t = time.time()
    if args.cmd is None:
        args = ap.parse_args(["app"])
    {"app": cmd_app, "worker": cmd_worker, "sync": cmd_sync, "score": cmd_score, "top": cmd_top, "check": cmd_check, "import": cmd_import,
     "status": cmd_status, "twin": cmd_twin, "smart-lp": cmd_smart_lp, "web-check": cmd_web_check,
     "set-rpc": cmd_set_rpc, "import-smart-lp": cmd_import_smart_lp, "publish": cmd_publish,
     "compare": cmd_compare}[args.cmd](args, cfg)
    if args.cmd in ("sync", "score"):
        print(f"done in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
