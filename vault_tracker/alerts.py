"""Alert formatting (Indonesian) and delivery: Telegram, Discord, JSONL log."""

import html
import json
import os
import time

import requests

from . import copytrade, dlmm
from .tokens import SOL_MINT, STABLES

DEFAULT_TYPES = {"OPEN", "CLOSE", "ADD", "REMOVE", "REBALANCE", "SWAP", "OUT_OF_RANGE", "IN_RANGE",
                 "ORDER_PLACE", "ORDER_CANCEL", "ORDER_CLOSE", "ORDER_FILL"}
FOLLOWER_TYPES = {"OPEN", "CLOSE", "OUT_OF_RANGE", "IN_RANGE", "ORDER_FILL"}
ICONS = {"OPEN": "🟢", "CLOSE": "🔴", "ADD": "➕", "REMOVE": "➖", "REBALANCE": "🔁", "CLAIM": "💰",
         "SWAP": "💱", "TRANSFER_IN": "📥", "TRANSFER_OUT": "📤", "OUT_OF_RANGE": "⚠️", "IN_RANGE": "✅",
         "ORDER_PLACE": "📌", "ORDER_CANCEL": "✖️", "ORDER_CLOSE": "✖️", "ORDER_FILL": "🎯"}


BASE_MINTS = STABLES | {SOL_MINT}


def plain(line):
    """Strip the Telegram-only <code> tags for console output."""
    return line.replace("<code>", "").replace("</code>", "")


def fmt_num(x, digits=4):
    if x is None:
        return "?"
    ax = abs(x)
    if ax == 0:
        return "0"
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:,.{digits}f}".rstrip("0").rstrip(".")
    return f"{x:.{digits}g}" if ax >= 1e-4 else f"{x:.3e}"


def fmt_usd(x):
    if x is None:
        return "$?"
    if 0 < abs(x) < 0.01:
        return "<$0.01"
    return f"${x:,.2f}"


def fmt_age(seconds):
    if seconds is None:
        return "?"
    m = int(seconds // 60)
    d, h = m // 1440, (m // 60) % 24
    if d:
        return f"{d}h {h}j"
    if h:
        return f"{h}j {m % 60}m"
    return f"{m}m"


def tx_url(sig):
    return f"https://solscan.io/tx/{sig}"


class AlertFormatter:
    def __init__(self, leader, follower=None, copy_mode="mirror"):
        self.leader = leader
        self.follower = follower
        self.copy_mode = copy_mode

    def _tracker(self, ev):
        return self.follower if (self.follower and ev.get("wallet") == self.follower.label) else self.leader

    def _row(self, tracker, position):
        for r in (tracker.snapshot or {}).get("positions", []):
            if r["address"] == position:
                return r
        return None

    def _pair(self, tracker, ev):
        lb_pair = ev.get("lb_pair") or tracker.state["positions"].get(ev.get("position"), {}).get("lb_pair")
        if not lb_pair:
            return None, None, None
        try:
            return tracker.pair_info(lb_pair)
        except Exception:
            return None, None, None

    def format(self, ev):
        """Returns (title, body_lines) for an event."""
        tr = self._tracker(ev)
        who = "KAMU" if tr is self.follower else tr.display
        kind = ev["type"]
        icon = ICONS.get(kind, "•")
        row = self._row(tr, ev.get("position")) if ev.get("position") else None
        pair, tx, ty = self._pair(tr, ev) if ev.get("position") else (None, None, None)
        name = row["pair"] if row else (f"{tx.symbol}-{ty.symbol}" if tx else "?")
        lines = []

        if kind == "OPEN":
            title = f"{icon} [{who}] POSISI BARU: {name}"
            if row:
                lines += self._range_lines(row)
                lines.append(f"Isi: {fmt_num(row['amount_x'])} {row['symbol_x']} + {fmt_num(row['amount_y'])} "
                             f"{row['symbol_y']} (≈ {fmt_usd(row['value_usd'])})")
                if tr is self.leader:
                    lines += self._copy_lines(row)
            elif tx and "amount_x" in ev:
                lines.append(f"Deposit: {fmt_num(tx.ui(ev['amount_x']))} {tx.symbol} + {fmt_num(ty.ui(ev['amount_y']))} {ty.symbol}")
            if ev.get("discovered"):
                lines.append("(terdeteksi lewat scan posisi, bukan dari tx)")
        elif kind == "CLOSE":
            title = f"{icon} [{who}] POSISI DITUTUP: {name}"
            st = tr.state["positions"].get(ev["position"], {})
            age = (st["closed_at"] - st["opened_at"]) if st.get("opened_at") and st.get("closed_at") else None
            if tx and "amount_x" in ev:
                ax, ay = tx.ui(ev["amount_x"]), ty.ui(ev["amount_y"])
                usd = [tr.prices.usd(tx.mint, ax), tr.prices.usd(ty.mint, ay)]
                lines.append(f"Ditarik: {fmt_num(ax)} {tx.symbol} + {fmt_num(ay)} {ty.symbol} "
                             f"(≈ {fmt_usd(None if None in usd else sum(usd))}) · umur posisi {fmt_age(age)}")
            else:
                lines.append(f"Nilai terakhir ≈ {fmt_usd(st.get('last_value_usd'))} · umur posisi {fmt_age(age)}")
            if tr is self.leader and self.follower and pair:
                mine = copytrade.follower_positions_in_pool(self.follower.snapshot, pair.address)
                if mine:
                    lines.append(f"⚠️ Kamu masih punya {len(mine)} posisi di pool ini → pertimbangkan close juga")
        elif kind in ("ADD", "REMOVE"):
            verb = "Tambah likuiditas" if kind == "ADD" else "Tarik likuiditas"
            title = f"{icon} [{who}] {verb}: {name}"
            if tx:
                ax, ay = tx.ui(ev["amount_x"]), ty.ui(ev["amount_y"])
                usd = [tr.prices.usd(tx.mint, ax), tr.prices.usd(ty.mint, ay)]
                total = None if None in usd else sum(usd)
                ev["usd"] = total
                lines.append(f"{fmt_num(ax)} {tx.symbol} + {fmt_num(ay)} {ty.symbol} (≈ {fmt_usd(total)})")
            if row:
                lines += self._range_lines(row)
        elif kind == "REBALANCE":
            title = f"{icon} [{who}] REBALANCE range: {name}"
            if pair:
                def rng(r):
                    return (f"{fmt_num(dlmm.bin_price(r[0], pair.bin_step, tx.decimals, ty.decimals))} – "
                            f"{fmt_num(dlmm.bin_price(r[1], pair.bin_step, tx.decimals, ty.decimals))}")
                lines.append(f"Lama: {rng(ev['old_range'])}")
                lines.append(f"Baru: {rng(ev['new_range'])} {ty.symbol} per {tx.symbol}")
            if row and tr is self.leader:
                lines += self._copy_lines(row)
        elif kind == "CLAIM":
            title = f"{icon} [{who}] Claim fee: {name}"
            if tx:
                fx, fy = tx.ui(ev["fee_x"]), ty.ui(ev["fee_y"])
                usd = [tr.prices.usd(tx.mint, fx), tr.prices.usd(ty.mint, fy)]
                ev["usd"] = None if None in usd else sum(usd)
                lines.append(f"{fmt_num(fx)} {tx.symbol} + {fmt_num(fy)} {ty.symbol} (≈ {fmt_usd(ev['usd'])})")
        elif kind in ("SWAP", "TRANSFER_IN", "TRANSFER_OUT"):
            label = {"SWAP": "Swap / spot trade", "TRANSFER_IN": "Dana masuk", "TRANSFER_OUT": "Dana keluar"}[kind]
            title = f"{icon} [{who}] {label}"
            tr.tokens.load(list(ev.get("sent", {})) + list(ev.get("received", {})))
            sent = ", ".join(f"-{fmt_num(a)} {tr.tokens.get(m).symbol}" for m, a in ev.get("sent", {}).items())
            recv = ", ".join(f"+{fmt_num(a)} {tr.tokens.get(m).symbol}" for m, a in ev.get("received", {}).items())
            lines.append(" → ".join(x for x in (sent, recv) if x))
            vals = [tr.prices.usd(m, a) for side in ("sent", "received") for m, a in ev.get(side, {}).items()]
            known = [v for v in vals if v is not None]
            ev["usd"] = max(known) if known else None
            if known:
                lines[-1] += f" (≈ {fmt_usd(ev['usd'])})"
            if kind == "SWAP":
                traded = [m for m in list(ev.get("received", {})) + list(ev.get("sent", {}))
                          if m not in BASE_MINTS]
                lines += [f"Token: {tr.tokens.get(m).symbol} <code>{m}</code>" for m in dict.fromkeys(traded)]
        elif kind == "ORDER_PLACE":
            verb = "TAMBAH LIMIT ORDER" if ev.get("added_to_existing") else "LIMIT ORDER BARU"
            side = "JUAL" if ev.get("is_ask") else "BELI"
            title = f"{icon} [{who}] {verb} ({side}): {name.replace(' order', '')}"
            if tx:
                tok = tx if ev.get("is_ask") else ty
                lines.append(f"Jumlah: {fmt_num(tok.ui(ev['amount']))} {tok.symbol} di {len(ev.get('bins', []))} bin")
                bins = ev.get("bins") or []
                if bins:
                    lo_p = dlmm.bin_price(min(bins), pair.bin_step, tx.decimals, ty.decimals)
                    hi_p = dlmm.bin_price(max(bins), pair.bin_step, tx.decimals, ty.decimals)
                    now_p = dlmm.bin_price(ev["active_bin_id"], pair.bin_step, tx.decimals, ty.decimals)
                    lines.append(f"Harga order: {fmt_num(lo_p)} – {fmt_num(hi_p)} {ty.symbol} per {tx.symbol} "
                                 f"({(lo_p / now_p - 1) * 100:+.1f}% / {(hi_p / now_p - 1) * 100:+.1f}% dari harga saat order)")
            if row and tr is self.leader:
                lines += self._copy_lines(row)
        elif kind in ("ORDER_CANCEL", "ORDER_CLOSE"):
            title = f"{icon} [{who}] LIMIT ORDER DITUTUP: {name.replace(' order', '')}"
            if tx and "amount_x" in ev:
                ax, ay = tx.ui(ev["amount_x"]), ty.ui(ev["amount_y"])
                usd = [tr.prices.usd(tx.mint, ax), tr.prices.usd(ty.mint, ay)]
                lines.append(f"Ditarik: {fmt_num(ax)} {tx.symbol} + {fmt_num(ay)} {ty.symbol} "
                             f"(≈ {fmt_usd(None if None in usd else sum(usd))})")
            elif kind == "ORDER_CLOSE":
                st = tr.state["positions"].get(ev["position"], {})
                lines.append(f"Nilai terakhir ≈ {fmt_usd(st.get('last_value_usd'))}")
            if tr is self.leader and self.follower and pair:
                mine = copytrade.follower_positions_in_pool(self.follower.snapshot, pair.address)
                if mine:
                    lines.append(f"⚠️ Kamu masih punya {len(mine)} posisi/order di pool ini")
        elif kind == "ORDER_FILL":
            title = f"{icon} [{who}] LIMIT ORDER TERISI {ev.get('filled_pct', 0):.0f}%: {name.replace(' order', '')}"
            if row:
                lines.append(f"Sisa: {fmt_num(row['unfilled_x'])} {row['symbol_x']} + {fmt_num(row['unfilled_y'])} "
                             f"{row['symbol_y']} · nilai ≈ {fmt_usd(row['value_usd'])}")
        elif kind in ("OUT_OF_RANGE", "IN_RANGE"):
            title = (f"{icon} [{who}] {name} OUT OF RANGE" if kind == "OUT_OF_RANGE"
                     else f"{icon} [{who}] {name} kembali IN RANGE")
            if row:
                lines += self._range_lines(row)
        else:
            title = f"{icon} [{who}] {kind}"

        if kind in ("OPEN", "ADD", "REBALANCE", "ORDER_PLACE") and row:
            lines.append(f"🔗 Meteora: https://app.meteora.ag/dlmm/{row['lb_pair']}")
        if ev.get("sig"):
            lines.append(f"🔍 Tx: {tx_url(ev['sig'])}")
        return title, lines

    def _range_lines(self, row):
        if row.get("kind") == "order":
            side = "jual" if row["side"] == "ask" else "beli"
            return [f"Limit order {side}: {fmt_num(row['price_lower'])} – {fmt_num(row['price_upper'])} "
                    f"{row['symbol_y']} per {row['symbol_x']} · {row['filled_pct']:.0f}% terisi"]
        pl = (row["price_lower"] / row["price"] - 1) * 100
        pu = (row["price_upper"] / row["price"] - 1) * 100
        status = "IN RANGE" if row["in_range"] else "OUT OF RANGE"
        return [
            f"Range: {fmt_num(row['price_lower'])} – {fmt_num(row['price_upper'])} {row['symbol_y']} per {row['symbol_x']} "
            f"({pl:+.1f}% / {pu:+.1f}%)",
            f"Harga: {fmt_num(row['price'])} · bin step {row['bin_step']} · {row['width']} bins · {status}",
        ]

    def _copy_lines(self, row):
        try:
            s = copytrade.suggest_range(row, self.copy_mode)
        except ValueError as e:
            return [f"Copy range error: {e}"]
        out = [f"📋 Range kamu ({s['mode']}): {fmt_num(s['price_lower'])} – {fmt_num(s['price_upper'])} "
               f"({s['pct_lower']:+.1f}% / {s['pct_upper']:+.1f}%) · {s['width']} bins · deposit {s['deposit']}"]
        if s["note"]:
            out.append(f"   ⚠️ {s['note']}")
        if self.follower and not copytrade.follower_positions_in_pool(self.follower.snapshot, row["lb_pair"]):
            out.append("   Kamu belum punya posisi di pool ini")
        return out


SIZED_TYPES = {"ADD", "REMOVE", "CLAIM", "SWAP", "TRANSFER_IN", "TRANSFER_OUT"}


class Notifier:
    def __init__(self, types=None, follower_types=None, min_usd=0.0, log_path=".vault_tracker/alerts.jsonl"):
        self.types = set(types) if types else set(DEFAULT_TYPES)
        self.min_usd = min_usd
        self.follower_types = set(follower_types) if follower_types else set(FOLLOWER_TYPES)
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        self.discord = os.environ.get("DISCORD_WEBHOOK_URL")
        self.log_path = log_path
        self.errors = []

    @property
    def channels(self):
        ch = []
        if self.tg_token and self.tg_chat:
            ch.append("telegram")
        if self.discord:
            ch.append("discord")
        return ch

    def wants(self, ev, follower_label=None):
        """Call after AlertFormatter.format(ev), which fills ev["usd"] for sized events."""
        is_follower = follower_label is not None and ev.get("wallet") == follower_label
        allowed = self.follower_types if is_follower else self.types
        if ev["type"] not in allowed:
            return False
        if ev["type"] in SIZED_TYPES and ev.get("usd") is not None and ev["usd"] < self.min_usd:
            return False
        return True

    def send(self, title, lines, ev=None):
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"at": int(time.time()), "title": title, "lines": lines, "event": ev}) + "\n")
        if self.tg_token and self.tg_chat:
            text = f"<b>{html.escape(title)}</b>\n" + "\n".join(
                l if "<code>" in l else html.escape(l) for l in lines)
            try:
                r = requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage", timeout=15, json={
                    "chat_id": self.tg_chat, "text": text, "parse_mode": "HTML",
                    "disable_web_page_preview": True})
                if not r.ok:
                    self.errors.append(f"telegram {r.status_code}: {r.text[:120]}")
            except requests.RequestException as e:
                self.errors.append(f"telegram: {type(e).__name__}: {str(e).replace(self.tg_token, '***')[:160]}")
        if self.discord:
            text = f"**{title}**\n" + "\n".join(l.replace("<code>", "`").replace("</code>", "`") for l in lines)
            try:
                r = requests.post(self.discord, json={"content": text[:1990]}, timeout=15)
                if not r.ok:
                    self.errors.append(f"discord {r.status_code}: {r.text[:120]}")
            except requests.RequestException as e:
                self.errors.append(f"discord: {type(e).__name__}: {str(e).replace(self.discord, '***')[:160]}")
