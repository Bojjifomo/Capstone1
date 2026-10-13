import pytest

from synthmon import alerts
from synthmon.models import CoinStats, Post, Trade
from synthmon.report import compute_report, format_report
from synthmon.roundtrips import build_round_trips, store_round_trips
from synthmon.exits import ExitClassifier, assign_exit_types

from .conftest import COIN_A, OWN, WALLET

T0 = 1_791_849_600  # 2026-10-13 00:00 UTC


def trade(id, ts, side, eth, tokens, coin=COIN_A, symbol="AAA", gas=None):
    return Trade(id=id, ts=ts, coin=coin, side=side, eth_amount=eth, token_amount=tokens, symbol=symbol,
                 gas_fee_eth=gas, source="test")


class RecordingNotifier:
    enabled = True

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, text):
        if self.fail:
            raise RuntimeError("webhook down")
        self.sent.append(text)


def kinds(found):
    return sorted(a.kind for a in found)


def test_history_does_not_alert_but_new_own_coin_sell_does(db, cfg):
    db.upsert_trade(trade("old", T0 - 86400, "sell", 0.1, 10, coin=OWN, symbol="BOJJI"))
    # The first evaluation arms at T0: the old sell is history. Being idle for a day is still a current state.
    assert kinds(alerts.evaluate(db, cfg, T0)) == ["no_trades"]
    db.upsert_trade(trade("new", T0 + 60, "sell", 0.2, 20, coin=OWN, symbol="BOJJI"))
    found = alerts.evaluate(db, cfg, T0 + 120)
    assert kinds(found) == ["own_coin_sell"] and "$BOJJI" in found[0].message
    n = RecordingNotifier()
    assert len(alerts.dispatch(db, n, found, T0 + 120)) == 1
    assert alerts.dispatch(db, n, alerts.evaluate(db, cfg, T0 + 180), T0 + 180) == []  # sent once
    assert len(n.sent) == 1


def test_own_coin_sells_known_at_start_are_not_sent(db, cfg):
    db.upsert_trade(trade("recent", T0 - 30, "sell", 0.1, 10, coin=OWN))
    alerts.dispatch(db, RecordingNotifier(), alerts.evaluate(db, cfg, T0), T0)
    row = db.query("SELECT delivered FROM alerts WHERE key = 'own_sell:recent'")[0]
    assert row["delivered"] == 2  # recorded as history, never sent
    # A sell that happened after monitoring started but was indexed late still alerts.
    db.upsert_trade(trade("late", T0 + 5, "sell", 0.1, 10, coin=OWN))
    db.upsert_trade(trade("sweep", T0 + 6, "transfer_out", None, 1e-18, coin=OWN))  # 1-wei dust sweep
    assert [a.key for a in alerts.evaluate(db, cfg, T0 + 600) if a.kind == "own_coin_sell"] == ["own_sell:late"]


def test_progress_thresholds_fire_once_each(db, cfg):
    alerts.evaluate(db, cfg, T0)
    n = RecordingNotifier()
    for ts, pct in ((T0 + 60, 30.0), (T0 + 120, 55.0), (T0 + 180, 60.0), (T0 + 240, 91.0)):
        db.add_coin_snapshot(ts, CoinStats(coin=OWN, holders=10, progress_pct=pct, raised_eth=pct / 200))
        db.upsert_trade(trade(f"keepalive{ts}", ts, "buy", 0.01, 1))
        alerts.dispatch(db, n, alerts.evaluate(db, cfg, ts), ts)
    assert [m.split(":")[0] for m in n.sent] == ["📈 @bojjibot's coin passed 50% to open", "📈 @bojjibot's coin passed 90% to open"]


def test_wallet_drop_fires_and_rearms(db, cfg):
    alerts.evaluate(db, cfg, T0)
    db.upsert_trade(trade("x", T0, "buy", 0.01, 1))
    seq = [(1.00, None), (0.90, None), (0.84, "wallet_drop"), (0.80, "wallet_drop"), (0.95, None), (0.80, "wallet_drop")]
    keys = []
    for i, (eth, expect) in enumerate(seq):
        ts = T0 + 60 * (i + 1)
        db.add_wallet_snapshot(ts, WALLET, eth, None, "test")
        found = [a for a in alerts.evaluate(db, cfg, ts) if a.kind == "wallet_drop"]
        assert (found[0].kind if found else None) == expect
        keys += [a.key for a in found]
    # 0.84 and 0.80 are one episode; recovery to 0.95 re-arms; the next drop is a new alert
    assert keys == ["wallet_drop:1", "wallet_drop:1", "wallet_drop:2"]


def test_wallet_drop_uses_total_value_when_bags_known(db, cfg):
    alerts.evaluate(db, cfg, T0)
    db.add_wallet_snapshot(T0 + 1, WALLET, 1.0, 0.0, "t")
    db.add_wallet_snapshot(T0 + 2, WALLET, 0.5, 0.45, "t")  # ETH moved into a bag: not a loss
    assert [a for a in alerts.evaluate(db, cfg, T0 + 2) if a.kind == "wallet_drop"] == []


def test_no_trades_alert(db, cfg):
    alerts.evaluate(db, cfg, T0)
    db.upsert_trade(trade("a", T0, "buy", 0.1, 1))
    assert [a for a in alerts.evaluate(db, cfg, T0 + 7000) if a.kind == "no_trades"] == []
    found = [a for a in alerts.evaluate(db, cfg, T0 + 7300) if a.kind == "no_trades"]
    assert found and found[0].key == f"no_trades:{T0}" and "2h01m" in found[0].message
    db.upsert_trade(trade("b", T0 + 8000, "sell", 0.1, 1))
    assert [a for a in alerts.evaluate(db, cfg, T0 + 8100) if a.kind == "no_trades"] == []


def test_dispatch_retries_failed_delivery(db, cfg):
    a = [alerts.Alert("k1", "no_trades", "hello")]
    assert alerts.dispatch(db, RecordingNotifier(fail=True), a, T0) == []
    row = db.query("SELECT * FROM alerts WHERE key = 'k1'")[0]
    assert row["delivered"] == 0 and row["attempts"] == 1 and "webhook down" in row["error"]
    ok = RecordingNotifier()
    assert len(alerts.dispatch(db, ok, [], T0 + 60)) == 1 and ok.sent == ["hello"]


def test_report(db, cfg):
    trades = [
        trade("b1", T0 + 0, "buy", 0.10, 1000, gas=0.0002), trade("s1", T0 + 600, "sell", 0.15, 1000, gas=0.0002),
        trade("b2", T0 + 1000, "buy", 0.20, 500), trade("s2", T0 + 4600, "sell", 0.16, 500),
        trade("b3", T0 + 5000, "buy", 0.10, 100, coin=OWN, symbol="BOJJI"),
        trade("s3", T0 + 5100, "sell", 0.30, 100, coin=OWN, symbol="BOJJI"),
    ]
    for t in trades:
        db.upsert_trade(t)
    posts = [Post("p1", T0 + 610, "Took profit on $AAA +50%"), Post("p2", T0 + 4610, "Stopped out of $AAA"),
             Post("p3", T0 + 5110, "AI call: decided to sell $BOJJI, momentum faded")]
    for p in posts:
        db.upsert_post(p, None)
    exits = assign_exit_types(db.trades(), db.posts(), ExitClassifier())
    store_round_trips(db, build_round_trips(db.trades(), exits))
    db.add_coin_snapshot(T0 + 10, CoinStats(coin=OWN, holders=40, buys=100, sells=20, raised_eth=0.2, progress_pct=40.0, source="synths"))
    db.add_coin_snapshot(T0 + 5200, CoinStats(coin=OWN, holders=55, buys=130, sells=31, raised_eth=0.3, progress_pct=60.0, source="synths"))
    db.add_wallet_snapshot(T0 + 5200, WALLET, 1.2, 0.0, "synths")
    db.set_state("wallet_baseline_total", 1.0)

    r = compute_report(db, cfg, T0, T0 + 6000)
    assert r["trades"]["total"] == 6 and r["trades"]["buys"] == 3
    rt = r["round_trips"]
    assert rt["closed"] == 3 and rt["wins"] == 2 and rt["losses"] == 1
    assert rt["win_rate_pct"] == pytest.approx(200 / 3)
    assert rt["avg_win_eth"] == pytest.approx((0.05 + 0.2) / 2)
    assert rt["avg_win_pct"] == pytest.approx((50 + 200) / 2)
    assert rt["avg_loss_eth"] == pytest.approx(-0.04) and rt["avg_loss_pct"] == pytest.approx(-20)
    assert set(r["exits"]) == {"take_profit", "stop_loss", "ai"}
    # gas: 0.0004 measured on 2 trades, median 0.0002 extrapolated to the other 4
    assert r["fees"]["gas_eth"] == pytest.approx(0.0004 + 4 * 0.0002)
    assert r["fees"]["platform_eth"] == pytest.approx(1.01 * 0.01)
    oc = r["own_coin"]
    assert oc["holders"] == 55 and oc["change"]["holders"] == 15 and oc["change"]["buys"] == 30
    assert oc["bot_trades"]["sells"] == 1
    assert r["wallet"]["change_pct"] == pytest.approx(20)

    text = format_report(r)
    assert "Win rate         66.7%  (2W / 1L)" in text
    assert "take_profit" in text and "holders        55 (+15 since)" in text
