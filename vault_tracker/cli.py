"""Command line entry point: watch / snapshot / history / test-alert."""

import argparse
import json
import os
import sys
import time

from rich.console import Console
from rich.live import Live
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from . import config, copytrade, dashboard
from .alerts import DEFAULT_TYPES, AlertFormatter, Notifier, plain
from .copier import Copier, load_keypair, save_keypair, settings_from_config
from .prices import PriceOracle
from .rpc import Rpc
from .runner import Session
from .tokens import TokenRegistry
from .vault import VaultTracker

console = Console()


def load_dotenv(path=".env"):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def check_address(addr, what):
    if not addr:
        sys.exit(f"{what} belum diisi (pakai --{what} atau env {what.upper()}_ADDRESS)")
    try:
        Pubkey.from_string(addr)
    except ValueError:
        sys.exit(f"{what}: {addr!r} bukan alamat Solana yang valid. "
                 "Isi dengan alamat vault/wallet on-chain (base58), bukan slug Hedgin.")
    return addr


def build(args):
    rpc = Rpc(args.rpc, min_interval=args.rpc_delay)
    tokens = TokenRegistry(rpc)
    prices = PriceOracle(rpc, tokens)
    leader = VaultTracker(rpc, tokens, prices, check_address(args.vault, "vault"), "leader",
                          args.state_dir, use_gpa=args.gpa)
    if getattr(args, "name", None):
        leader.display = args.name.upper()
    follower = None
    if getattr(args, "follower", None):
        follower = VaultTracker(rpc, tokens, prices, check_address(args.follower, "follower"), "follower",
                                args.state_dir, use_gpa=args.gpa)
    return rpc, leader, follower


def sync(tracker, history):
    if tracker.bootstrapped:
        return tracker.poll_transactions()
    with console.status(f"[{tracker.label}] baca histori transaksi…") as status:
        def progress(i, n):
            status.update(f"[{tracker.label}] baca histori transaksi {i}/{n}…")
        tracker.bootstrap(history, progress)
    return []


def cmd_snapshot(args):
    rpc, leader, follower = build(args)
    for t in filter(None, (leader, follower)):
        sync(t, args.history)
        t.refresh(emit=False)
        t.save()
    if args.json:
        out = {"leader": leader.snapshot, "follower": follower.snapshot if follower else None}
        for r in leader.snapshot["positions"]:
            r["copy"] = copytrade.suggest_range(r, args.copy_mode)
        print(json.dumps(out, indent=2))
        return
    formatter = AlertFormatter(leader, follower, args.copy_mode)
    console.print(dashboard.allocation_panel(leader, args.by, console.width, args.show_dust))
    console.print(dashboard.positions_table(leader, args.copy_mode, follower))
    if follower:
        console.print(dashboard.positions_table(follower))
        console.print(dashboard.sync_table(leader, follower))
    console.print(dashboard.events_panel(formatter, leader.state["events"], notifier=Notifier(min_usd=5)))
    console.print(dashboard.managers_text(leader))


def cmd_history(args):
    rpc, leader, _ = build(args)
    sync(leader, args.history)
    leader.refresh(emit=False)
    leader.save()
    formatter = AlertFormatter(leader, None, args.copy_mode)
    for ev in leader.state["events"][-args.limit:]:
        if ev["type"] not in ("OPEN", "CLOSE", "REBALANCE", "SWAP", "ORDER_PLACE", "ORDER_CANCEL",
                              "ORDER_CLOSE") and not args.all:
            continue
        title, lines = formatter.format(ev)
        console.print(f"[grey58]{time.strftime('%Y-%m-%d %H:%M', time.localtime(ev['ts']))}[/] [bold]{title}[/]")
        for l in lines:
            console.print(f"    {plain(l)}", highlight=False)


def cmd_watch(args):
    if not args.vault:
        sys.exit("vault belum diisi (pakai --vault atau env VAULT_ADDRESS)")
    try:
        session = Session(check_address(args.vault, "vault"),
                          check_address(args.follower, "follower") if args.follower else None,
                          rpc_url=args.rpc, rpc_delay=args.rpc_delay, copy_mode=args.copy_mode,
                          history=args.history, use_gpa=args.gpa, state_dir=args.state_dir,
                          alert_types=set(args.alerts.split(",")) if args.alerts else None,
                          min_usd=args.min_usd, name=args.name, **_copy_kwargs(args))
    except (ValueError, OSError) as e:
        sys.exit(str(e))
    leader, follower, notifier = session.leader, session.follower, session.notifier
    if session.copier:
        console.print(f"[bold]Copytrade bot: {session.copier.s['run'].upper()}[/] · wallet {session.copier.wallet}")

    def cycle(first=False):
        if first:
            with console.status("baca histori transaksi…") as status:
                sent = session.cycle(first=True, on_progress=lambda t, i, n: status.update(
                    f"[{t.label}] baca histori transaksi {i}/{n}…"))
        else:
            sent = session.cycle()
        if args.no_dashboard:
            for title, lines, _ in sent:
                console.print(f"[bold]{title}[/]")
                for l in lines:
                    console.print(f"    {plain(l)}", highlight=False)

    cycle(first=True)
    session.announce()

    def view(next_poll):
        return dashboard.render(leader, follower, session.formatter, notifier, session.rpc, args.by,
                                args.copy_mode, args.interval, next_poll, console.width, args.show_dust)

    next_poll = time.time() + args.interval
    if args.no_dashboard:
        console.print(f"watching {leader.address} tiap {args.interval}s — Ctrl+C untuk berhenti")
        while True:
            time.sleep(max(0, next_poll - time.time()))
            try:
                cycle()
            except Exception as e:
                console.print(f"[red]error: {e}[/]")
            next_poll = time.time() + args.interval
    with Live(view(next_poll), console=console, refresh_per_second=1, screen=args.fullscreen) as live:
        while True:
            while time.time() < next_poll:
                time.sleep(1)
                live.update(view(next_poll))
            try:
                cycle()
            except Exception as e:
                notifier.errors.append(f"{type(e).__name__}: {e}")
            next_poll = time.time() + args.interval
            live.update(view(next_poll))


def cmd_signers(args):
    rpc, leader, _ = build(args)
    sync(leader, args.history)
    leader.save()
    rows = leader.managers()
    if not rows:
        print("Belum ada signer lain yang terdeteksi. Coba naikkan --history.")
        return
    console.print("[bold]Wallet yang menandatangani transaksi vault[/] (urut dari jumlah trade DLMM/swap)")
    for m in rows[:args.limit]:
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["last_ts"])) if m["last_ts"] else "?"
        console.print(f"  {m['address']}  trade tx: {m['trades']:>4}  semua tx: {m['txs']:>4}  "
                      f"fee payer: {m['fee_payer']:>4}  terakhir: {last}", highlight=False)
    top = next((m for m in rows if m["trades"]), None)
    if top:
        console.print(f"\nKemungkinan wallet manager: [bold]{top['address']}[/]")
        console.print(f"Lacak juga: python -m vault_tracker watch --vault {top['address']} --name MANAGER",
                      highlight=False)


def cmd_web(args):
    from .web import serve
    serve(args.host, args.port, open_browser=not args.no_browser, autostart=args.start,
          state_dir=args.state_dir)


def cmd_mirrors(args):
    from .mirrors import find_mirrors
    rpc, leader, _ = build(args)
    sync(leader, args.history)
    leader.save()
    with console.status("cari wallet kembar…") as status:
        def progress(i, n, ev):
            status.update(f"posisi vault {i}/{n}: baca transaksi pool di sekitar waktu buka…")
        rows, info = find_mirrors(leader, window=args.window, max_events=args.events, max_txs=args.max_txs,
                                  on_progress=progress)
    leader.save()
    console.print(f"Dicek {info['vault_opens']} posisi yang dibuka vault, {info['txs_scanned']} transaksi pool "
                  f"(±{info['window_s']} detik dari tiap open).")
    console.print("Alamat pemilik posisi vault: " + ", ".join(info["position_owners"]), highlight=False)
    if not rows:
        console.print("Tidak ada wallet lain yang membuka posisi mirip di waktu yang sama. "
                      "Coba --window 1800 atau --events 30.")
        return
    strong = [r for r in rows if r["strong"]]
    shown = rows if args.all else strong
    if not shown:
        console.print(f"Tidak ada wallet yang konsisten membuka posisi mirip vault ({len(rows)} kecocokan lemah/"
                      "kebetulan). Tambah --all untuk melihatnya, atau coba --events 30 --window 1800.")
    for r in shown[:args.limit]:
        d = r["median_delay_s"]
        delay = f"{abs(d)} dtk {'sebelum' if d < 0 else 'setelah'} vault"
        console.print(f"\n[bold]{r['wallet']}[/]", highlight=False)
        console.print(f"  cocok {r['matches']}/{r['events']} posisi · range sama persis {r['exact']}x · "
                      f"overlap rata-rata {r['avg_overlap'] * 100:.0f}% · jeda median {delay}", highlight=False)
        console.print(f"  {r['label']}", style="green" if r["strong"] else "grey58", highlight=False)
    if shown and not args.all and len(rows) > len(strong):
        console.print(f"\n(+{len(rows) - len(strong)} kecocokan lemah disembunyikan, tambah --all untuk melihat)")
    if args.json:
        print(json.dumps({"rows": rows, "info": info}, indent=2))


DEFAULT_KEYPAIR = "~/.config/vault_tracker/copy-wallet.json"


def _copy_kwargs(args):
    cfg = config.load()
    settings = settings_from_config(cfg)
    if getattr(args, "copy", None):
        settings["run"] = args.copy
    return {"copy_settings": settings, "copy_keypair": cfg.get("COPY_KEYPAIR") or None}


def _copier(args):
    cfg = config.load()
    settings = settings_from_config(cfg)
    if settings["run"] == "off":
        settings["run"] = "dry"  # status / close still work, as a simulation
    kp = load_keypair(cfg["COPY_KEYPAIR"]) if cfg.get("COPY_KEYPAIR") else None
    rpc = Rpc(args.rpc)
    tokens = TokenRegistry(rpc)
    prices = PriceOracle(rpc, tokens)
    prices.refresh(set())
    return Copier(rpc, tokens, prices, settings, cfg.get("COPY_MODE") or "mirror", args.state_dir, keypair=kp,
                  sim_wallet=cfg.get("FOLLOWER_ADDRESS") or None)


def cmd_copy_wallet(args):
    path = os.path.abspath(os.path.expanduser(args.path))
    if args.action in ("new", "import"):
        if os.path.exists(path):
            sys.exit(f"{path} sudah ada. Pakai --path lain supaya wallet lama tidak tertimpa.")
        if args.action == "new":
            kp = Keypair()
        else:
            import getpass
            secret = getpass.getpass("Tempel private key wallet bot (base58 dari Phantom, atau array JSON): ").strip()
            try:
                kp = (Keypair.from_bytes(bytes(json.loads(secret))) if secret.startswith("[")
                      else Keypair.from_base58_string(secret))
            except Exception:
                sys.exit("Private key tidak valid.")
        save_keypair(kp, path)
        updates = {"COPY_KEYPAIR": path}
        if not config.load().get("FOLLOWER_ADDRESS"):
            updates["FOLLOWER_ADDRESS"] = str(kp.pubkey())
        config.save(updates)
        print(f"Wallet bot: {kp.pubkey()}")
        print(f"Keypair disimpan di {path} (hanya bisa dibaca user ini). COPY_KEYPAIR di .env sudah diisi.")
        print("Jangan bagikan file itu. Isi wallet ini dengan dana kecil secukupnya: SOL untuk fee/rent + modal.")
        return
    cfg = config.load()
    if not cfg.get("COPY_KEYPAIR"):
        sys.exit("COPY_KEYPAIR belum diisi. Jalankan dulu: python3 -m vault_tracker copy-wallet new")
    kp = load_keypair(cfg["COPY_KEYPAIR"])
    rpc = Rpc(args.rpc)
    print(f"Wallet bot: {kp.pubkey()}")
    print(f"SOL: {rpc.get_balance(str(kp.pubkey())) / 1e9:.4f}")
    for ta in rpc.get_token_accounts(str(kp.pubkey())):
        info = ta["account"]["data"]["parsed"]["info"]
        print(f"  {info['mint']}: {info['tokenAmount']['uiAmountString']}")


def cmd_copy_status(args):
    st = _copier(args).status()
    console.print(f"[bold]Copytrade bot[/] · mode {st['run'].upper()} · wallet {st['wallet']}"
                  + (" · [red]DIJEDA[/]" if st["paused"] else ""))
    if st.get("balances"):
        console.print(f"Saldo: {st['balances']['SOL']:.4f} SOL · {st['balances']['USDC']:.2f} USDC")
    console.print(f"Posisi bot terbuka: {len(st['positions'])}")
    for p in st["positions"]:
        opened = time.strftime("%d/%m %H:%M", time.localtime(p["opened_at"]))
        console.print(f"  {p['position']}  {p['pair']}  bins {p['lower']}..{p['upper']}  "
                      f"{dashboard.fmt_usd(p['usd'])}  dibuka {opened}", highlight=False)
    for e in st["log"][:args.limit]:
        console.print(f"[grey58]{time.strftime('%d/%m %H:%M', time.localtime(e['ts']))}[/] {e['title']}")
        for line in e["lines"]:
            console.print(f"    {line}", highlight=False)


def cmd_copy_close(args):
    try:
        title, lines = _copier(args).close_position(args.position)
    except Exception as e:
        sys.exit(f"Gagal: {e}")
    console.print(f"[bold]{title}[/]")
    for line in lines:
        console.print(f"    {line}", highlight=False)


def cmd_copy_resume(args):
    _copier(args).resume()
    print("Copytrade dilanjutkan (penghitung gagal di-reset).")


def cmd_test_alert(args):
    n = Notifier()
    if not n.channels:
        sys.exit("Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID atau DISCORD_WEBHOOK_URL dulu (di .env).")
    n.send("✅ Test alert vault tracker", ["Kalau pesan ini masuk, alert sudah siap."])
    if n.errors:
        sys.exit("gagal kirim: " + " | ".join(n.errors))
    print("terkirim ke:", ", ".join(n.channels))


def main(argv=None):
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m vault_tracker",
                                description="Live tracker + alert posisi vault Hedgin / wallet Meteora DLMM untuk copy-trade.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, follower=True):
        sp.add_argument("--vault", default=os.environ.get("VAULT_ADDRESS"),
                        help="alamat on-chain vault/wallet leader (env VAULT_ADDRESS)")
        if follower:
            sp.add_argument("--follower", default=os.environ.get("FOLLOWER_ADDRESS"),
                            help="wallet kamu, untuk cek sudah copy / belum (env FOLLOWER_ADDRESS)")
        sp.add_argument("--copy-mode", default=os.environ.get("COPY_MODE", "mirror"),
                        help="mirror | pct:LOW,HIGH (mis. pct:-10,10) | scale:F (mis. scale:0.5)")
        sp.add_argument("--rpc", default=None, help="Solana RPC URL (env SOLANA_RPC_URL)")
        sp.add_argument("--rpc-delay", type=float, default=float(os.environ.get("RPC_DELAY", "0.05")),
                        help="jeda minimum antar request RPC (detik)")
        sp.add_argument("--history", type=int, default=int(os.environ.get("HISTORY_LIMIT", "300")),
                        help="jumlah tx histori yang dibaca saat pertama jalan")
        sp.add_argument("--gpa", action="store_true", help="juga scan posisi via getProgramAccounts (Helius/QuickNode)")
        sp.add_argument("--state-dir", default=".vault_tracker")
        sp.add_argument("--name", default=os.environ.get("TRACKER_NAME"),
                        help="nama di alert/dashboard, default LEADER (mis. --name MANAGER)")
        sp.add_argument("--by", choices=("position", "token"), default="position")
        sp.add_argument("--show-dust", action="store_true", help="tampilkan semua token spot (termasuk spam/tanpa harga)")

    w = sub.add_parser("watch", help="dashboard live + alert open/close")
    common(w)
    w.add_argument("--interval", type=int, default=int(os.environ.get("POLL_INTERVAL", "30")))
    w.add_argument("--alerts", default=os.environ.get("ALERT_TYPES"),
                   help=f"tipe alert, default {','.join(sorted(DEFAULT_TYPES))} (+ CLAIM, TRANSFER_IN, TRANSFER_OUT)")
    w.add_argument("--min-usd", type=float, default=float(os.environ.get("MIN_ALERT_USD", "5")),
                   help="abaikan alert add/remove/swap/claim di bawah nilai ini (USD), default 5")
    w.add_argument("--no-dashboard", action="store_true", help="tanpa dashboard, cuma print alert (untuk server)")
    w.add_argument("--fullscreen", action="store_true")
    w.add_argument("--copy", choices=("off", "dry", "live"), help="mode bot copytrade (default: COPY_RUN di .env)")
    w.set_defaults(fn=cmd_watch)

    s = sub.add_parser("snapshot", help="tampilkan allocation + posisi sekali")
    common(s)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_snapshot)

    h = sub.add_parser("history", help="riwayat open/close/rebalance/swap")
    common(h, follower=False)
    h.add_argument("--limit", type=int, default=100)
    h.add_argument("--all", action="store_true", help="termasuk add/remove/claim/transfer")
    h.set_defaults(fn=cmd_history)

    mr = sub.add_parser("mirrors", help="cari wallet lain yang membuka posisi sama persis seperti vault")
    common(mr, follower=False)
    mr.add_argument("--window", type=int, default=600, help="detik sebelum/sesudah tiap open vault (default 600)")
    mr.add_argument("--events", type=int, default=15, help="jumlah posisi vault terakhir yang dicek")
    mr.add_argument("--max-txs", type=int, default=400, help="maks transaksi pool per sisi per posisi")
    mr.add_argument("--limit", type=int, default=15)
    mr.add_argument("--json", action="store_true")
    mr.add_argument("--all", action="store_true", help="tampilkan juga kecocokan lemah")
    mr.set_defaults(fn=cmd_mirrors)

    sg = sub.add_parser("signers", help="cari wallet manager yang menandatangani trade vault")
    common(sg, follower=False)
    sg.add_argument("--limit", type=int, default=10)
    sg.set_defaults(fn=cmd_signers)

    wb = sub.add_parser("web", help="buka dashboard di browser: start/stop, pengaturan, posisi, alert")
    wb.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", "8787")))
    wb.add_argument("--host", default="127.0.0.1",
                    help="default hanya komputer ini; 0.0.0.0 = bisa dibuka dari HP di Wi-Fi yang sama (pakai token)")
    wb.add_argument("--no-browser", action="store_true", help="jangan buka browser otomatis")
    wb.add_argument("--start", action="store_true", help="langsung start tracker kalau pengaturan sudah lengkap")
    wb.add_argument("--state-dir", default=".vault_tracker")
    wb.set_defaults(fn=cmd_web)

    cw = sub.add_parser("copy-wallet", help="wallet bot copytrade: new | import | show")
    cw.add_argument("action", choices=("new", "import", "show"))
    cw.add_argument("--path", default=DEFAULT_KEYPAIR, help=f"lokasi file keypair (default {DEFAULT_KEYPAIR})")
    cw.add_argument("--rpc", default=None)
    cw.set_defaults(fn=cmd_copy_wallet)

    for name, fn, helptext in (("copy-status", cmd_copy_status, "posisi dan log bot copytrade"),
                               ("copy-resume", cmd_copy_resume, "lanjutkan bot yang dijeda")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--rpc", default=None)
        sp.add_argument("--state-dir", default=".vault_tracker")
        sp.add_argument("--limit", type=int, default=15)
        sp.set_defaults(fn=fn)
    cc = sub.add_parser("copy-close", help="tutup satu posisi bot (simulasi kalau mode bukan live)")
    cc.add_argument("position")
    cc.add_argument("--rpc", default=None)
    cc.add_argument("--state-dir", default=".vault_tracker")
    cc.set_defaults(fn=cmd_copy_close)

    t = sub.add_parser("test-alert", help="kirim pesan test ke Telegram/Discord")
    t.set_defaults(fn=cmd_test_alert)

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
