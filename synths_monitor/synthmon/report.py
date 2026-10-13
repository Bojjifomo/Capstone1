"""`synthmon report --since ...`: performance summary from the SQLite store."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .db import DB
from .util import fmt_duration, fmt_ts, median, now

EXIT_ORDER = ("take_profit", "stop_loss", "timer", "ai", "unknown", "transfer")


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def compute_report(db: DB, cfg: dict[str, Any], since: int, until: int | None = None) -> dict[str, Any]:
    until = now() if until is None else until
    trades = [t for t in db.trades(since=since) if t.ts <= until]
    sides = Counter(t.side for t in trades)
    priced = [t for t in trades if t.side in ("buy", "sell")]

    trips = db.query(
        "SELECT * FROM round_trips WHERE status = 'closed' AND close_ts >= ? AND close_ts <= ? ORDER BY close_ts",
        (since, until),
    )
    open_trips = db.query("SELECT COUNT(*) AS n FROM round_trips WHERE status = 'open'")[0]["n"]
    scored = [r for r in trips if r["pnl_eth"] is not None]
    wins = [r for r in scored if r["pnl_eth"] > 0]
    losses = [r for r in scored if r["pnl_eth"] <= 0]

    by_exit: dict[str, dict[str, Any]] = {}
    for r in trips:
        e = by_exit.setdefault(r["exit_type"] or "unknown", {"count": 0, "wins": 0, "pnl_eth": 0.0, "pcts": [], "holds": []})
        e["count"] += 1
        if r["pnl_eth"] is not None:
            e["wins"] += r["pnl_eth"] > 0
            e["pnl_eth"] += r["pnl_eth"]
            e["pcts"].append(r["pnl_pct"])
        if r["hold_seconds"] is not None:
            e["holds"].append(r["hold_seconds"])
    exits = {
        k: {"count": v["count"], "wins": v["wins"], "pnl_eth": v["pnl_eth"], "avg_pnl_pct": _avg(v["pcts"]),
            "avg_hold_s": _avg(v["holds"])}
        for k, v in sorted(by_exit.items(), key=lambda kv: EXIT_ORDER.index(kv[0]) if kv[0] in EXIT_ORDER else 99)
    }

    # Fees: gas is measured where the source reports it; the rest is extrapolated from the median.
    gas_known = [t.gas_fee_eth for t in trades if t.gas_fee_eth is not None]
    gas_missing = len(trades) - len(gas_known)
    med = median(gas_known)
    gas_est = sum(gas_known) + (med * gas_missing if med is not None else 0.0)
    volume = sum(t.eth_amount or 0.0 for t in priced)
    bps = cfg["fees"]["protocol_fee_bps"]
    platform = volume * bps / 10_000

    return {
        "handle": cfg["bot"]["handle"],
        "since": since,
        "until": until,
        "trades": {"total": len(trades), "buys": sides["buy"], "sells": sides["sell"],
                   "transfers_in": sides["transfer_in"], "transfers_out": sides["transfer_out"],
                   "volume_eth": volume},
        "round_trips": {
            "closed": len(trips), "open_now": open_trips, "unscored": len(trips) - len(scored),
            "wins": len(wins), "losses": len(losses),
            "win_rate_pct": len(wins) / len(scored) * 100 if scored else None,
            "avg_win_eth": _avg([r["pnl_eth"] for r in wins]),
            "avg_win_pct": _avg([r["pnl_pct"] for r in wins]),
            "avg_loss_eth": _avg([r["pnl_eth"] for r in losses]),
            "avg_loss_pct": _avg([r["pnl_pct"] for r in losses]),
            "net_pnl_eth": sum(r["pnl_eth"] for r in scored) if scored else None,
            "avg_hold_win_s": _avg([r["hold_seconds"] for r in wins]),
            "avg_hold_loss_s": _avg([r["hold_seconds"] for r in losses]),
            "best": _trip_brief(max(scored, key=lambda r: r["pnl_eth"])) if scored else None,
            "worst": _trip_brief(min(scored, key=lambda r: r["pnl_eth"])) if scored else None,
        },
        "fees": {
            "gas_eth": gas_est if gas_known else None, "gas_measured_trades": len(gas_known),
            "gas_extrapolated_trades": gas_missing if gas_known else 0,
            "platform_eth": platform, "platform_fee_bps": bps,
            "total_eth": (gas_est if gas_known else 0.0) + platform,
        },
        "exits": exits,
        "own_coin": _own_coin(db, cfg, since, until),
        "wallet": _wallet(db, cfg),
        "alerts_sent": {r["kind"]: r["n"] for r in db.query(
            "SELECT kind, COUNT(*) AS n FROM alerts WHERE delivered = 1 AND ts >= ? AND ts <= ? GROUP BY kind",
            (since, until))},
    }


def _trip_brief(r: Any) -> dict[str, Any]:
    return {"coin": r["coin"], "symbol": r["symbol"], "pnl_eth": r["pnl_eth"], "pnl_pct": r["pnl_pct"],
            "exit_type": r["exit_type"], "close_ts": r["close_ts"]}


def _own_coin(db: DB, cfg: dict[str, Any], since: int, until: int) -> dict[str, Any] | None:
    coin = db.get_state("own_coin") or cfg["bot"]["own_coin"]
    if not coin:
        return None
    latest = db.query("SELECT * FROM own_coin_snapshots WHERE coin = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                      (coin, until))
    first = db.query("SELECT * FROM own_coin_snapshots WHERE coin = ? AND ts >= ? ORDER BY ts LIMIT 1", (coin, since))
    sym = db.query("SELECT symbol FROM launched WHERE coin = ? AND symbol IS NOT NULL UNION ALL "
                   "SELECT symbol FROM trades WHERE coin = ? AND symbol IS NOT NULL LIMIT 1", (coin, coin))
    out: dict[str, Any] = {"coin": coin, "symbol": sym[0]["symbol"] if sym else None,
                           "open_threshold_eth": cfg["coin"]["open_threshold_eth"]}
    if latest:
        last = latest[0]
        out.update({k: last[k] for k in ("holders", "buys", "sells", "raised_eth", "progress_pct", "source")})
        out["as_of"] = last["ts"]
        if first:
            f = first[0]
            out["change"] = {k: (last[k] - f[k]) if last[k] is not None and f[k] is not None else None
                             for k in ("holders", "buys", "sells", "progress_pct")}
    bot = [t for t in db.trades(since=since, coin=coin) if t.ts <= until]
    out["bot_trades"] = {
        "buys": sum(t.side == "buy" for t in bot), "sells": sum(t.side == "sell" for t in bot),
        "transfers_out": sum(t.side == "transfer_out" for t in bot),
        "buy_eth": sum(t.eth_amount or 0 for t in bot if t.side == "buy"),
        "sell_eth": sum(t.eth_amount or 0 for t in bot if t.side == "sell"),
    }
    return out


def _wallet(db: DB, cfg: dict[str, Any]) -> dict[str, Any] | None:
    snap = db.latest_wallet_snapshot()
    if snap is None:
        return None
    metric = "total" if cfg["alerts"]["wallet_metric"] == "total" and snap["total_value_eth"] is not None else "eth"
    baseline = cfg["alerts"]["baseline_eth"] or db.get_state(f"wallet_baseline_{metric}")
    value = snap["total_value_eth"] if metric == "total" else snap["eth_balance"]
    return {"eth_balance": snap["eth_balance"], "total_value_eth": snap["total_value_eth"], "metric": metric,
            "baseline_eth": baseline, "change_pct": (value - baseline) / baseline * 100 if baseline else None,
            "as_of": snap["ts"]}


def _e(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "-"
    places = 6 if 0 < abs(v) < 0.01 else 4
    return f"{v:+.{places}f}" if signed else f"{v:.{places}f}"


def _p(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "-"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def format_report(r: dict[str, Any]) -> str:
    t, rt, fees = r["trades"], r["round_trips"], r["fees"]
    lines = [
        f"@{r['handle']} - {fmt_ts(r['since'])} to {fmt_ts(r['until'])}",
        "",
        f"Trades           {t['total']}  (buys {t['buys']}, sells {t['sells']}"
        + (f", transfers in/out {t['transfers_in']}/{t['transfers_out']}" if t["transfers_in"] or t["transfers_out"] else "")
        + f")   volume {_e(t['volume_eth'])} ETH",
        f"Round trips      {rt['closed']} closed, {rt['open_now']} open now"
        + (f", {rt['unscored']} without full cost basis (not scored)" if rt["unscored"] else ""),
        f"Win rate         {_p(rt['win_rate_pct'], signed=False)}  ({rt['wins']}W / {rt['losses']}L)",
        f"Average win      {_e(rt['avg_win_eth'], True)} ETH ({_p(rt['avg_win_pct'])}), held {fmt_duration(rt['avg_hold_win_s'])}",
        f"Average loss     {_e(rt['avg_loss_eth'], True)} ETH ({_p(rt['avg_loss_pct'])}), held {fmt_duration(rt['avg_hold_loss_s'])}",
        f"Net PnL          {_e(rt['net_pnl_eth'], True)} ETH (closed round trips, before gas)",
    ]
    if rt["avg_win_eth"] and rt["avg_loss_eth"]:
        lines.append(f"Win/loss size    {abs(rt['avg_win_eth'] / rt['avg_loss_eth']):.2f}x")
    for label in ("best", "worst"):
        b = rt[label]
        if b:
            lines.append(f"{label.capitalize():<17}${b['symbol'] or b['coin'][:10]} {_e(b['pnl_eth'], True)} ETH"
                         f" ({_p(b['pnl_pct'])}, {b['exit_type']})")
    gas = "-" if fees["gas_eth"] is None else (
        f"{fees['gas_eth']:.5f} (measured on {fees['gas_measured_trades']} trades"
        + (f", extrapolated to {fees['gas_extrapolated_trades']} more" if fees["gas_extrapolated_trades"] else "") + ")")
    lines += [
        f"Estimated fees   {fees['total_eth']:.5f} ETH = gas {gas} + platform {fees['platform_eth']:.5f}"
        f" ({fees['platform_fee_bps'] / 100:.2f}% of volume)",
        "",
        "Exits by type    count  wins   PnL ETH   avg %   avg hold",
    ]
    if not r["exits"]:
        lines.append("  (no closed round trips)")
    for name, e in r["exits"].items():
        lines.append(f"  {name:<14} {e['count']:>5} {e['wins']:>5} {_e(e['pnl_eth'], True):>9} {_p(e['avg_pnl_pct']):>7}"
                     f"   {fmt_duration(e['avg_hold_s'])}")

    oc = r["own_coin"]
    lines.append("")
    if oc is None:
        lines.append("Own coin         not known yet (set bot.own_coin or configure the 'launched' endpoint)")
    else:
        ch = oc.get("change") or {}

        def delta(k: str, fmt: str = "{:+d}") -> str:
            return f" ({fmt.format(ch[k])} since)" if ch.get(k) is not None else ""

        lines.append(f"Own coin         ${oc['symbol'] or '?'} {oc['coin']}")
        if "as_of" in oc:
            if oc["progress_pct"] is None:
                to_open = "unknown (needs the synths coin endpoint or coin.curve_address)"
            else:
                raised = f"{oc['raised_eth']:.4f}" if oc["raised_eth"] is not None else "?"
                to_open = (f"{_p(oc['progress_pct'], signed=False)} ({raised} / {oc['open_threshold_eth']} ETH)"
                           + delta("progress_pct", "{:+.1f} pts"))
            lines += [
                f"  to open        {to_open}",
                f"  holders        {oc['holders'] if oc['holders'] is not None else '-'}" + delta("holders"),
                f"  buys / sells   {oc['buys'] if oc['buys'] is not None else '-'} / "
                f"{oc['sells'] if oc['sells'] is not None else '-'}"
                + (f" ({ch['buys']:+d} / {ch['sells']:+d} since)" if ch.get("buys") is not None and ch.get("sells") is not None else ""),
                f"  data           {oc['source']} as of {fmt_ts(oc['as_of'])}",
            ]
        b = oc["bot_trades"]
        lines.append(f"  bot's trades   {b['buys']} buys ({b['buy_eth']:.4f} ETH), {b['sells']} sells ({b['sell_eth']:.4f} ETH)"
                     + (f", {b['transfers_out']} transfers out" if b["transfers_out"] else ""))

    w = r["wallet"]
    if w:
        total = f", with bags {w['total_value_eth']:.4f} ETH" if w["total_value_eth"] is not None else ""
        lines += ["", f"Wallet           {w['eth_balance']:.4f} ETH{total}; {_p(w['change_pct'])} vs baseline"
                      f" {_e(w['baseline_eth'])} ({w['metric']}) as of {fmt_ts(w['as_of'])}"]
    if r["alerts_sent"]:
        lines.append("Alerts sent      " + ", ".join(f"{k} {v}" for k, v in sorted(r["alerts_sent"].items())))
    return "\n".join(lines)
