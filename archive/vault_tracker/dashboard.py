"""Rich terminal dashboard mirroring Hedgin's Allocation panel, plus copy-trade view."""

import time
from urllib.parse import urlparse

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import copytrade
from .alerts import fmt_age, fmt_num, fmt_usd, plain

PALETTE = ["#5b8def", "#4fb3cf", "#8b5cf6", "#f0a53a", "#e0559b", "#3fbf7f", "#d9d453", "#9aa5b1"]


def short(addr, n=4):
    return f"{addr[:n]}…{addr[-n:]}" if addr and len(addr) > 2 * n else addr


def allocation_rows(tracker, by="position"):
    snap = tracker.snapshot
    if by == "token":
        agg = {}
        for p in snap["positions"]:
            for mint, sym, amt in ((p["x"], p["symbol_x"], p["amount_x"]), (p["y"], p["symbol_y"], p["amount_y"])):
                agg.setdefault(mint, [sym, 0.0])[1] += amt
        for sp in snap["spot"]:
            agg.setdefault(sp["mint"], [sp["symbol"], 0.0])[1] += sp["amount"]
        return [{"label": sym, "tag": "token", "amount": amt, "value": tracker.prices.usd(mint, amt),
                 "in_range": None} for mint, (sym, amt) in agg.items()]
    rows = [{"label": p["pair"], "tag": "limit order" if p.get("kind") == "order" else "DLMM",
             "value": p["value_usd"], "amount": None,
             "in_range": p["in_range"] if p["has_liquidity"] else None} for p in snap["positions"]]
    rows += [{"label": sp["symbol"], "tag": "spot", "value": sp["value_usd"], "amount": sp["amount"],
              "in_range": None} for sp in snap["spot"]]
    return rows


def collapse_dust(rows, show_dust=False, keep=12):
    """Hide unpriced / sub-cent spot tokens (airdrop spam) behind one summary row."""
    if show_dust:
        return rows
    main = [r for r in rows if r["tag"] in ("DLMM", "limit order") or (r["value"] or 0) >= 0.01]
    dust = [r for r in rows if r not in main]
    main, extra = main[:keep], main[keep:]
    dust += extra
    if dust:
        unpriced = sum(1 for r in dust if r["value"] is None)
        label = f"+{len(dust)} token lain"
        if unpriced:
            label += f" ({unpriced} tanpa harga)"
        main.append({"label": label, "tag": "dust", "value": sum(r["value"] or 0 for r in dust),
                     "amount": None, "in_range": None})
    return main


def _bar(rows, total, width):
    t = Text()
    if not total:
        return Text("─" * width, style="grey35")
    used = 0
    for i, r in enumerate(rows):
        v = r["value"] or 0
        n = round(width * v / total)
        if i == len(rows) - 1:
            n = max(0, width - used)
        n = min(n, width - used)
        if n > 0:
            t.append("█" * n, style=PALETTE[i % len(PALETTE)])
            used += n
    if used < width:
        t.append("░" * (width - used), style="grey35")
    return t


def allocation_panel(tracker, by="position", width=100, show_dust=False):
    snap = tracker.snapshot
    if not snap:
        return Panel("memuat…", title=f"Allocation — {tracker.label}")
    rows = allocation_rows(tracker, by)
    rows.sort(key=lambda r: -(r["value"] or 0))
    rows = collapse_dust(rows, show_dust)
    total = snap["total_usd"]
    tbl = Table.grid(expand=True, padding=(0, 1))
    tbl.add_column(width=2)
    tbl.add_column(ratio=3)
    tbl.add_column(justify="right", ratio=2)
    tbl.add_column(justify="right", width=8)
    for i, r in enumerate(rows):
        dot = Text("●", style=PALETTE[i % len(PALETTE)])
        label = Text(r["label"], style="bold")
        label.append(f"  {r['tag']}", style="grey58")
        if r["in_range"] is not None:
            label.append("  in range" if r["in_range"] else "  OUT OF RANGE",
                         style="green" if r["in_range"] else "bold red")
        if r["amount"] is not None and r["tag"] not in ("DLMM", "limit order", "dust"):
            label.append(f"  {fmt_num(r['amount'])}", style="grey58")
        pct = (r["value"] or 0) / total * 100 if total else 0
        tbl.add_row(dot, label, fmt_usd(r["value"]), f"{pct:.1f}%")
    head = Text(f"Live value {fmt_usd(total)}", style="bold")
    head.append(f"   by {by}", style="grey58")
    return Panel(Group(head, Text(), _bar(rows, total, max(10, width - 6)), Text(), tbl),
                 title=f"Allocation — {tracker.display} {short(tracker.address)}", border_style="grey50")


def positions_table(tracker, copy_mode=None, follower=None):
    snap = tracker.snapshot
    tbl = Table(title=f"Posisi & limit order DLMM — {tracker.display}", expand=True, border_style="grey35",
                title_justify="left")
    tbl.add_column("Pool", ratio=3)
    tbl.add_column("Range", ratio=3)
    tbl.add_column("Status", ratio=2)
    tbl.add_column("Isi", ratio=3)
    tbl.add_column("Nilai", justify="right", ratio=2)
    if copy_mode:
        tbl.add_column(f"Range kamu ({copy_mode})", ratio=3)
    if not snap or not snap["positions"]:
        tbl.add_row("tidak ada posisi terbuka", *[""] * (len(tbl.columns) - 1))
        return tbl
    for r in sorted(snap["positions"], key=lambda r: -(r["value_usd"] or 0)):
        pl = (r["price_lower"] / r["price"] - 1) * 100
        pu = (r["price_upper"] / r["price"] - 1) * 100
        if r.get("kind") == "order":
            status = Text("ORDER " + ("JUAL" if r["side"] == "ask" else "BELI"), style="cyan")
            status.append(f"\n{r['filled_pct']:.0f}% terisi", style="grey58")
        elif not r["has_liquidity"]:
            status = Text("kosong", style="grey58")
        elif r["in_range"]:
            status = Text("IN RANGE", style="green")
        else:
            status = Text("OUT OF RANGE", style="bold red")
        status.append(f"\nharga {fmt_num(r['price'])}\n{r['symbol_y']} per {r['symbol_x']}", style="grey58")
        age = fmt_age(time.time() - r["opened_at"]) if r.get("opened_at") else "?"
        pool = Text(r["pair"], style="bold")
        bins_txt = f"{r['order_bins']} bin order" if r.get("kind") == "order" else f"{r['width']} bins"
        pool.append(f"\nstep {r['bin_step']} · {bins_txt}\n{short(r['address'])} · umur {age}", style="grey58")
        cells = [
            pool,
            f"{fmt_num(r['price_lower'])} – {fmt_num(r['price_upper'])}\n({pl:+.1f}% / {pu:+.1f}%)",
            status,
            f"{fmt_num(r['amount_x'])} {r['symbol_x']}\n{fmt_num(r['amount_y'])} {r['symbol_y']}",
            Text(fmt_usd(r["value_usd"])).append(f"\nfee {fmt_usd(r['fees_usd'])}", style="grey58"),
        ]
        if copy_mode:
            try:
                sug = copytrade.suggest_range(r, copy_mode)
                txt = Text(f"{fmt_num(sug['price_lower'])} – {fmt_num(sug['price_upper'])}\n"
                           f"{sug['width']} bins · {sug['deposit']}")
                if follower and follower.snapshot:
                    mine = copytrade.follower_positions_in_pool(follower.snapshot, r["lb_pair"])
                    txt.append("\nsudah copy ✓" if mine else "\nbelum copy", style="green" if mine else "yellow")
            except ValueError as e:
                txt = Text(str(e), style="red")
            cells.append(txt)
        tbl.add_row(*cells)
    return tbl


def sync_table(leader, follower):
    rows = copytrade.sync_status(leader.snapshot, follower.snapshot)
    tbl = Table(title=f"Sync copy-trade: LEADER vs KAMU {short(follower.address)}", expand=True,
                border_style="grey35", title_justify="left")
    for c in ("Pool", "Status", "Posisi leader", "Posisi kamu", "Range kamu"):
        tbl.add_column(c)
    if not rows:
        tbl.add_row("-", "tidak ada posisi", "", "", "")
    styles = {"SYNC": "green", "BELUM COPY": "yellow", "LEADER SUDAH CLOSE": "bold red"}
    for p in rows:
        fr = p["follower_in_range"]
        step = (p["leader"] or p["follower"])[0]["bin_step"]
        tbl.add_row(f"{p['pair']} · step {step}\n{short(p['lb_pair'])}", Text(p["status"], style=styles[p["status"]]),
                    str(len([r for r in p["leader"] if r["has_liquidity"]])),
                    str(len([r for r in p["follower"] if r["has_liquidity"]])),
                    "-" if fr is None else Text("in range" if fr else "OUT OF RANGE",
                                                style="green" if fr else "bold red"))
    return tbl


def events_panel(formatter, events, limit=12, notifier=None):
    """Latest events; with a notifier, only those that would also trigger an alert."""
    t = Text()
    shown = 0
    follower_label = formatter.follower.label if formatter.follower else None
    for ev in reversed(list(events)):
        if shown >= limit:
            break
        try:
            title, lines = formatter.format(ev)
        except Exception as e:  # never let one odd event kill the dashboard
            title, lines = f"{ev.get('type')} ({e})", []
        if notifier and not notifier.wants(ev, follower_label):
            continue
        shown += 1
        ts = time.strftime("%d/%m %H:%M", time.localtime(ev.get("ts") or 0))
        t.append(f"{ts}  ", style="grey58")
        t.append(title + "\n", style="grey62" if ev.get("historical") else "bold")
        detail = next((l for l in lines if not l.startswith(("🔗", "🔍"))), None)
        if detail:
            t.append(f"             {plain(detail)}\n", style="grey58")
    if not t:
        t.append("belum ada aktivitas", style="grey58")
    return Panel(t, title="Aktivitas terbaru", border_style="grey50")


def managers_text(tracker, limit=3):
    """Wallets that sign this vault's trades: for a Hedgin vault, the manager's own key."""
    rows = [m for m in tracker.managers() if m["trades"]][:limit]
    t = Text("Wallet yang menandatangani trade vault: ", style="grey58")
    if not rows:
        t.append("belum terdeteksi (vault belum trade di histori yang dibaca)", style="grey58")
        return t
    for i, m in enumerate(rows):
        if i:
            t.append(" · ", style="grey58")
        t.append(m["address"], style="bold")
        t.append(f" ({m['trades']} trade tx)", style="grey58")
    return t


def footer(rpc, notifier, trackers, interval, next_poll):
    host = urlparse(rpc.url).hostname
    ch = ", ".join(notifier.channels) or "console saja (set TELEGRAM_* / DISCORD_WEBHOOK_URL)"
    src = trackers[0].prices.source if trackers else "-"
    t = Text(f"RPC {host} · harga: {src} · alert: {ch} · refresh tiap {interval}s · "
             f"update berikutnya {max(0, int(next_poll - time.time()))}s · Ctrl+C keluar", style="grey58")
    if notifier.errors:
        t.append(f"\nalert error: {notifier.errors[-1]}", style="red")
    return t


def render(leader, follower, formatter, notifier, rpc, by, copy_mode, interval, next_poll, width,
           show_dust=False):
    parts = [allocation_panel(leader, by, width, show_dust), positions_table(leader, copy_mode, follower)]
    if follower:
        parts += [positions_table(follower), sync_table(leader, follower)]
    events = sorted(leader.state["events"] + (follower.state["events"] if follower else []),
                    key=lambda e: e.get("ts") or 0)
    parts += [events_panel(formatter, events, notifier=notifier), managers_text(leader), footer(rpc, notifier, [leader], interval, next_poll)]
    return Group(*parts)
