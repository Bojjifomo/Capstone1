"""Alert rules and delivery to Discord / Telegram.

Each alert has a stable key, so it is sent once even though the rules run on
every poll. Undelivered alerts are retried on later polls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

from .db import DB
from .http import redact
from .util import fmt_duration, fmt_ts, now

log = logging.getLogger("synthmon.alerts")
MAX_ATTEMPTS = 5
# Synths sweep leftover dust (often 1 wei of a token) after selling; that is not news.
OWN_COIN_DUST_TOKENS = 1e-6


@dataclass
class Alert:
    key: str
    kind: str
    message: str


class Notifier:
    def __init__(self, cfg: dict[str, Any], timeout: float = 15.0):
        a = cfg["alerts"]
        self.discord = a["discord_webhook_url"]
        self.tg_token = a["telegram_bot_token"]
        self.tg_chat = a["telegram_chat_id"]
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.discord or (self.tg_token and self.tg_chat))

    def send(self, text: str) -> None:
        """Send to every configured channel; raises if all of them fail."""
        errors = []
        sent = 0
        if self.discord:
            try:
                r = requests.post(self.discord, json={"content": text[:2000]}, timeout=self.timeout)
                r.raise_for_status()
                sent += 1
            except requests.RequestException as exc:
                errors.append(f"discord: {redact(str(exc))}")
        if self.tg_token and self.tg_chat:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            try:
                r = requests.post(url, json={"chat_id": self.tg_chat, "text": text[:4096],
                                             "disable_web_page_preview": True}, timeout=self.timeout)
                r.raise_for_status()
                sent += 1
            except requests.RequestException as exc:
                errors.append(f"telegram: {redact(str(exc))}")
        if errors and not sent:
            raise RuntimeError("; ".join(errors))
        for e in errors:
            log.warning("alert channel failed: %s", e)


def _fmt_eth(v: float | None) -> str:
    return "?" if v is None else f"{v:.4f} ETH"


def evaluate(db: DB, cfg: dict[str, Any], ts: int | None = None) -> list[Alert]:
    """Run every rule against the database and return the alerts that apply now."""
    ts = now() if ts is None else ts
    a = cfg["alerts"]
    handle = cfg["bot"]["handle"]
    alerts: list[Alert] = []
    armed_ts = db.get_state("alerts_armed_ts")
    first_run = armed_ts is None
    if first_run:
        armed_ts = ts
        db.set_state("alerts_armed_ts", armed_ts)

    # 1. The bot sells (or moves out) its own coin.
    own = db.get_state("own_coin") or cfg["bot"]["own_coin"]
    if own:
        for row in db.query(
            "SELECT * FROM trades WHERE coin = ? AND side IN ('sell', 'transfer_out') AND COALESCE(token_amount, 1) >= ? "
            "ORDER BY ts", (own, OWN_COIN_DUST_TOKENS),
        ):
            what = "SOLD" if row["side"] == "sell" else "moved out"
            sym = row["symbol"] or own[:10]
            alert = Alert(
                key=f"own_sell:{row['id']}", kind="own_coin_sell",
                message=(f"🚨 @{handle} {what} its own coin ${sym}: {row['token_amount'] or '?'} tokens"
                         f" for {_fmt_eth(row['eth_amount'])} at {fmt_ts(row['ts'])}"
                         + (f"\ntx {row['tx_hash']}" if row["tx_hash"] else "")),
            )
            # Sells already in the data when monitoring started are history, not news. Later polls
            # still alert on sells that show up late, as long as they happened after that.
            if first_run or row["ts"] < armed_ts - 600:
                _suppress(db, alert, ts)
            else:
                alerts.append(alert)

        # 2. Own coin passes 50% / 90% of the way to opening.
        snap = db.latest_coin_snapshot(own)
        if snap is not None and snap["progress_pct"] is not None:
            for threshold in sorted(a["progress_thresholds_pct"]):
                if snap["progress_pct"] >= threshold:
                    alerts.append(Alert(
                        key=f"progress:{own}:{threshold}", kind="own_coin_progress",
                        message=(f"📈 @{handle}'s coin passed {threshold}% to open: {snap['progress_pct']:.1f}%"
                                 f" ({_fmt_eth(snap['raised_eth'])} of {cfg['coin']['open_threshold_eth']} ETH)"
                                 + (f", {snap['holders']} holders" if snap["holders"] is not None else "")),
                    ))

    # 3. Wallet value falls more than N% below the baseline.
    alerts.extend(_wallet_drop(db, cfg, ts))

    # 4. No trades for N hours.
    last = db.query("SELECT MAX(ts) AS ts FROM trades")[0]["ts"]
    quiet_since = last if last else armed_ts
    quiet_for = ts - quiet_since
    if quiet_for >= a["no_trade_hours"] * 3600:
        last_ok = db.query("SELECT MAX(ts) AS ts FROM polls WHERE ok = 1")[0]["ts"]
        alerts.append(Alert(
            key=f"no_trades:{quiet_since}", kind="no_trades",
            message=(f"⏸️ @{handle} has not traded for {fmt_duration(quiet_for)}"
                     f" (last trade {fmt_ts(last) if last else 'none seen'}; last good poll {fmt_ts(last_ok)})"),
        ))
    recorded = {r["key"] for r in db.query("SELECT key FROM alerts")}
    return [a for a in alerts if a.key not in recorded]


def _suppress(db: DB, alert: Alert, ts: int) -> None:
    db.execute(
        "INSERT OR IGNORE INTO alerts (key, ts, kind, message, delivered, error) VALUES (?, ?, ?, ?, 2, ?)",
        (alert.key, ts, alert.kind, alert.message, "history at start of monitoring; not sent"),
    )


def _wallet_drop(db: DB, cfg: dict[str, Any], ts: int) -> list[Alert]:
    a = cfg["alerts"]
    handle = cfg["bot"]["handle"]
    snap = db.latest_wallet_snapshot()
    if snap is None:
        return []
    metric = "total" if a["wallet_metric"] == "total" and snap["total_value_eth"] is not None else "eth"
    value = snap["total_value_eth"] if metric == "total" else snap["eth_balance"]
    key = f"wallet_baseline_{metric}"
    baseline = a["baseline_eth"] or db.get_state(key)
    if not baseline:
        db.set_state(key, value)
        return []
    drop = (baseline - value) / baseline * 100
    episode = db.get_state("wallet_drop_episode", 0)
    active = db.get_state("wallet_drop_active", False)
    if drop > a["wallet_drop_pct"]:
        if not active:
            episode += 1
            db.set_state("wallet_drop_episode", episode)
            db.set_state("wallet_drop_active", True)
        label = "wallet value (ETH + bags)" if metric == "total" else "ETH balance"
        return [Alert(
            key=f"wallet_drop:{episode}", kind="wallet_drop",
            message=(f"🔻 @{handle} {label} is down {drop:.1f}% from baseline:"
                     f" {value:.4f} ETH vs {baseline:.4f} ETH (alert at >{a['wallet_drop_pct']}%)"),
        )]
    if active and drop < a["wallet_drop_rearm_pct"]:
        db.set_state("wallet_drop_active", False)
    return []


def dispatch(db: DB, notifier: Notifier, alerts: list[Alert], ts: int | None = None) -> list[Alert]:
    """Record new alerts and (re)try delivery of everything undelivered. Returns the alerts sent now."""
    ts = now() if ts is None else ts
    for alert in alerts:
        db.execute(
            "INSERT OR IGNORE INTO alerts (key, ts, kind, message) VALUES (?, ?, ?, ?)",
            (alert.key, ts, alert.kind, alert.message),
        )
    sent = []
    for row in db.query("SELECT * FROM alerts WHERE delivered = 0 AND attempts < ? ORDER BY ts", (MAX_ATTEMPTS,)):
        log.warning("ALERT %s", row["message"])
        if not notifier.enabled:
            db.execute("UPDATE alerts SET delivered = 1, error = 'no webhook configured; logged only' WHERE key = ?",
                       (row["key"],))
            continue
        try:
            notifier.send(row["message"])
        except Exception as exc:  # delivery problems must never stop the monitor
            db.execute("UPDATE alerts SET attempts = attempts + 1, error = ? WHERE key = ?", (str(exc)[:300], row["key"]))
            continue
        db.execute("UPDATE alerts SET delivered = 1, attempts = attempts + 1, error = NULL WHERE key = ?", (row["key"],))
        sent.append(Alert(row["key"], row["kind"], row["message"]))
    db.commit()
    return sent
