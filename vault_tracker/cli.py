"""Command line entry point: watch / snapshot / history / test-alert."""

import argparse
import json
import os
import sys
import time

from rich.console import Console
from rich.live import Live
from solders.pubkey import Pubkey

from . import copytrade, dashboard
from .alerts import DEFAULT_TYPES, AlertFormatter, Notifier, plain
from .prices import PriceOracle
from .rpc import Rpc
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
    rpc, leader, follower = build(args)
    copytrade.parse_mode(args.copy_mode)  # validate early
    trackers = [t for t in (follower, leader) if t]  # follower first so leader alerts see it
    types = set(args.alerts.split(",")) if args.alerts else None
    notifier = Notifier(types, min_usd=args.min_usd)
    formatter = AlertFormatter(leader, follower, args.copy_mode)

    def cycle(first=False):
        pending = []
        for t in trackers:
            pending += sync(t, args.history) if first else t.poll_transactions()
        for t in trackers:
            _, ev = t.refresh(emit=not first)
            pending += ev
            t.save()
        for ev in sorted(pending, key=lambda e: e.get("ts") or 0):
            title, lines = formatter.format(ev)
            if not notifier.wants(ev, follower.label if follower else None):
                continue
            notifier.send(title, lines, ev)
            if args.no_dashboard:
                console.print(f"[bold]{title}[/]")
                for l in lines:
                    console.print(f"    {plain(l)}", highlight=False)

    cycle(first=True)
    if notifier.channels:
        notifier.send("👀 Vault tracker aktif",
                      [f"Leader: {leader.address}", f"Copy mode: {args.copy_mode}",
                       f"Posisi terbuka: {len(leader.snapshot['positions'])} · nilai {dashboard.fmt_usd(leader.snapshot['total_usd'])}"])

    def view(next_poll):
        return dashboard.render(leader, follower, formatter, notifier, rpc, args.by, args.copy_mode,
                                args.interval, next_poll, console.width, args.show_dust)

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

    sg = sub.add_parser("signers", help="cari wallet manager yang menandatangani trade vault")
    common(sg, follower=False)
    sg.add_argument("--limit", type=int, default=10)
    sg.set_defaults(fn=cmd_signers)

    t = sub.add_parser("test-alert", help="kirim pesan test ke Telegram/Discord")
    t.set_defaults(fn=cmd_test_alert)

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
