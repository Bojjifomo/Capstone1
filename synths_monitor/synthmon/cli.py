"""Command line: setup, find-synth, discover, poll, run, report, roundtrips, test-alert, reset-baseline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ConfigError, has_webhook, load_config, rpc_url
from .db import DB
from .util import fmt_duration, fmt_ts, is_address, now, parse_since

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"


def _load(args: argparse.Namespace) -> dict:
    try:
        return load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc


def _toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string literal is a valid TOML basic string


def cmd_setup(args: argparse.Namespace) -> None:
    """Ask for the wallet's public address and an alert webhook; write config.toml."""
    print("synthmon is read-only. It needs the synth wallet's PUBLIC address and never a private key or seed phrase.")
    while True:
        wallet = input("Synth wallet address (0x...): ").strip()
        if is_address(wallet):
            break
        if len(wallet.removeprefix("0x")) == 64:
            print("That is 64 hex characters, the length of a private key. Don't paste it anywhere; "
                  "enter the 42-character public address.")
        else:
            print("Expected 0x followed by 40 hex characters.")
    handle = input("Synth handle [bojjibot]: ").strip().lstrip("@") or "bojjibot"
    owner = ""
    synth = _lookup_synth(wallet.lower(), handle)
    if synth and input(f"{wallet} created synth @{synth.handle}, whose own account is {synth.account}. "
                       "Monitor that account? [Y/n] ").strip().lower() in ("", "y", "yes"):
        owner, wallet = wallet.lower(), synth.account
    discord = input("Discord webhook URL (blank to skip): ").strip()
    tg_token = tg_chat = ""
    if not discord:
        tg_token = input("Telegram bot token (blank to skip): ").strip()
        if tg_token:
            tg_chat = input("Telegram chat id: ").strip()
    path = Path(args.config)
    text = EXAMPLE.read_text() if EXAMPLE.exists() else ""
    if path.exists() and input(f"{path} exists. Overwrite? [y/N] ").strip().lower() != "y":
        raise SystemExit("left existing config untouched")
    text = (text.replace('wallet = ""', f"wallet = {_toml_str(wallet.lower())}", 1)
                .replace('owner = ""', f"owner = {_toml_str(owner)}", 1)
                .replace('handle = "bojjibot"', f"handle = {_toml_str(handle)}", 1)
                .replace('discord_webhook_url = ""', f"discord_webhook_url = {_toml_str(discord)}", 1)
                .replace('telegram_bot_token = ""', f"telegram_bot_token = {_toml_str(tg_token)}", 1)
                .replace('telegram_chat_id = ""', f"telegram_chat_id = {_toml_str(tg_chat)}", 1))
    path.write_text(text)
    path.chmod(0o600)  # it holds the webhook URL
    load_config(path)
    print(f"wrote {path}. Next: `python -m synthmon discover`, paste the endpoints in, then `python -m synthmon run`.")


def _lookup_synth(owner: str, handle: str | None):
    """The synth this wallet created (matching `handle` when given), if an RPC is configured."""
    from .http import Http, SourceError
    from .registry import find_synths
    from .sources.chain import RpcChain

    url = rpc_url(load_config(None))
    if not url:
        return None
    try:
        found = find_synths(RpcChain(url, Http()), owner)
    except SourceError:
        return None
    matches = [r for r in found if not handle or (r.handle or "").lower() == handle.lower()]
    return matches[-1] if matches else None


def cmd_find_synth(args: argparse.Namespace) -> None:
    from .http import Http
    from .registry import find_synths
    from .sources.chain import RpcChain

    cfg = _load(args)
    url = rpc_url(cfg)
    if not url:
        raise SystemExit("needs chain.rpc_url or ALCHEMY_API_KEY")
    if not is_address(args.owner):
        raise SystemExit("--owner must be a 0x address")
    found = find_synths(RpcChain(url, Http()), args.owner)
    if not found:
        print(f"no synths created by {args.owner} (through registry calls sent from that wallet)")
    for r in found:
        print(f"@{r.handle}  account {r.account}  created {fmt_ts(r.ts)}  block {r.block}  tx {r.tx_hash}")
        print(f"    details: {r.strings}")


def cmd_discover(args: argparse.Namespace) -> None:
    from .discover import run_discovery

    cfg = _load(args)
    res = run_discovery(cfg["synths"]["base_url"], args.handle or cfg["bot"]["handle"], args.out,
                        wallet=cfg["bot"]["wallet"] or None, headful=args.headful)
    print(f"captured {res['captured']} JSON responses (profile found: {res['profile_found']}); coins seen: {len(res['coins'])}")
    for kind, url in res["suggestions"].items():
        print(f"  {kind:<12} {url}")
    if res["websockets"]:
        print("websockets:", *res["websockets"], sep="\n  ")
    print(f"details in {args.out}/endpoints.json; suggested config in {args.out}/suggested_config.toml")


def _context(args: argparse.Namespace, require_webhook: bool):
    from .poller import build_context

    cfg = _load(args)
    if not cfg["bot"]["wallet"]:
        raise SystemExit("bot.wallet is not set. Run `python -m synthmon setup` (public address only).")
    if require_webhook and not has_webhook(cfg) and not args.no_webhook:
        raise SystemExit("no alert webhook configured. Run `python -m synthmon setup`, or pass --no-webhook to only log alerts.")
    if not any(cfg["synths"]["endpoints"].values()) and not (cfg["chain"]["explorer_api_url"] or rpc_url(cfg)):
        raise SystemExit("no data source: add synths endpoints (see `discover`), chain.explorer_api_url, "
                         "chain.rpc_url or ALCHEMY_API_KEY.")
    db = DB(cfg["storage"]["db_path"])
    return build_context(cfg, db)


def cmd_poll(args: argparse.Namespace) -> None:
    from .poller import poll_once

    ctx = _context(args, require_webhook=False)
    st = poll_once(ctx)
    print(json.dumps({"ok": st.ok, "sources": st.sources, "errors": st.errors}, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    from .poller import run_forever

    ctx = _context(args, require_webhook=True)
    logging.getLogger("synthmon").info(
        "monitoring @%s wallet %s every %ss", ctx.cfg["bot"]["handle"], ctx.cfg["bot"]["wallet"],
        ctx.cfg["poll"]["interval_seconds"])
    try:
        run_forever(ctx)
    except KeyboardInterrupt:
        print("stopped")


def cmd_report(args: argparse.Namespace) -> None:
    from .report import compute_report, format_report

    cfg = _load(args)
    db = DB(cfg["storage"]["db_path"])
    since = parse_since(args.since)
    until = parse_since(args.until) if args.until else None
    r = compute_report(db, cfg, since, until)
    print(json.dumps(r, indent=2) if args.json else format_report(r))


def cmd_roundtrips(args: argparse.Namespace) -> None:
    cfg = _load(args)
    db = DB(cfg["storage"]["db_path"])
    since = parse_since(args.since) if args.since else 0
    rows = db.query("SELECT * FROM round_trips WHERE COALESCE(close_ts, open_ts) >= ? ORDER BY open_ts", (since,))
    print(f"{'opened':<21}{'coin':<14}{'status':<8}{'buy ETH':>9}{'sell ETH':>10}{'PnL ETH':>10}{'PnL %':>8}  {'hold':<9}exit")
    for r in rows:
        coin = f"${r['symbol']}" if r["symbol"] else r["coin"][:12]
        pnl = f"{r['pnl_eth']:+.4f}" if r["pnl_eth"] is not None else "-"
        pct = f"{r['pnl_pct']:+.1f}" if r["pnl_pct"] is not None else "-"
        print(f"{fmt_ts(r['open_ts']):<21}{coin[:13]:<14}{r['status']:<8}{r['buy_eth']:>9.4f}{r['sell_eth']:>10.4f}"
              f"{pnl:>10}{pct:>8}  {fmt_duration(r['hold_seconds']):<9}{r['exit_type'] or ''}")


def cmd_test_alert(args: argparse.Namespace) -> None:
    from .alerts import Notifier

    cfg = _load(args)
    n = Notifier(cfg)
    if not n.enabled:
        raise SystemExit("no webhook configured")
    try:
        n.send(f"✅ synthmon test alert for @{cfg['bot']['handle']} at {fmt_ts(now())}")
    except RuntimeError as exc:
        raise SystemExit(f"test alert failed: {exc}") from None
    print("sent")


def cmd_reset_baseline(args: argparse.Namespace) -> None:
    cfg = _load(args)
    db = DB(cfg["storage"]["db_path"])
    db.execute("DELETE FROM state WHERE key LIKE 'wallet_baseline_%' OR key = 'wallet_drop_active'")
    db.commit()
    print("baseline cleared; the next poll sets a new one")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="synthmon", description="Read-only monitor for a synths.lol trading agent.")
    ap.add_argument("-c", "--config", default="config.toml", help="config file (default: config.toml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="ask for the wallet address and webhook, write config.toml").set_defaults(fn=cmd_setup)
    p = sub.add_parser("find-synth", help="find the synth account(s) a wallet created on synths.lol")
    p.add_argument("--owner", required=True, help="the wallet that created the synth")
    p.set_defaults(fn=cmd_find_synth)
    p = sub.add_parser("discover", help="capture the JSON endpoints synths.lol calls (Playwright)")
    p.add_argument("--handle")
    p.add_argument("--out", default="discovery")
    p.add_argument("--headful", action="store_true", help="show the browser window")
    p.set_defaults(fn=cmd_discover)
    p = sub.add_parser("poll", help="poll once and exit")
    p.add_argument("--no-webhook", action="store_true")
    p.set_defaults(fn=cmd_poll)
    p = sub.add_parser("run", help="poll every interval with backoff and send alerts")
    p.add_argument("--no-webhook", action="store_true", help="run without a webhook; alerts are only logged")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="performance since a time")
    p.add_argument("--since", required=True, help="24h, 7d, 2026-10-01, 2026-10-01T12:00Z or unix seconds")
    p.add_argument("--until")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("roundtrips", help="list round trips")
    p.add_argument("--since")
    p.set_defaults(fn=cmd_roundtrips)
    sub.add_parser("test-alert", help="send a test message to the webhook").set_defaults(fn=cmd_test_alert)
    sub.add_parser("reset-baseline", help="forget the wallet baseline").set_defaults(fn=cmd_reset_baseline)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    args.fn(args)
