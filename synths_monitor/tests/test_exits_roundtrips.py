import pytest

from synthmon.exits import ExitClassifier, assign_exit_types
from synthmon.models import Post, Trade
from synthmon.roundtrips import build_round_trips

from .conftest import COIN_A, COIN_B

clf = ExitClassifier()


@pytest.mark.parametrize("text,expected", [
    ("Took profit on $PEPE at +42%. Onto the next one.", "take_profit"),
    ("TP hit on $DOG, sold the bag", "take_profit"),
    ("Stopped out of $CAT at -20%", "stop_loss"),
    ("stop-loss triggered, cutting losses on $XYZ", "stop_loss"),
    ("Timer exit: max hold reached on $ABC, sold flat", "timer"),
    ("Hold time expired, closing $ABC", "timer"),
    ("My analysis says momentum is gone, decided to sell $ABC", "ai"),
    ("gm frens, just vibing", None),
    ("exit_reason: take_profit", "take_profit"),
])
def test_classify(text, expected):
    assert clf.classify(text) == expected


def test_extra_patterns_win():
    c = ExitClassifier({"timer": [r"clock ran out"]})
    assert c.classify("the clock ran out on $X") == "timer"


def t(id, ts, side, eth, tokens, coin=COIN_A, symbol="AAA", gas=None, reason=None, tx=None):
    return Trade(id=id, ts=ts, coin=coin, side=side, eth_amount=eth, token_amount=tokens, symbol=symbol,
                 gas_fee_eth=gas, reason=reason, tx_hash=tx, source="test")


def test_assign_exit_types_by_symbol_time_and_tx():
    trades = [
        t("b1", 1000, "buy", 0.1, 100), t("s1", 2000, "sell", 0.15, 100),
        t("b2", 3000, "buy", 0.1, 50, coin=COIN_B, symbol="BBB"),
        t("s2", 4000, "sell", 0.05, 50, coin=COIN_B, symbol="BBB", tx="0x" + "e" * 64),
        t("s3", 9000, "sell", 0.01, 1, reason="stop_loss"),
    ]
    posts = [
        Post("p0", 1010, "Bought $AAA, TP at +50%"),  # a buy post near the buy, not the sell
        Post("p1", 2030, "Took profit on $AAA, +50%"),
        Post("p2", 4100, "sold it, stopped out", tx_hash="0x" + "e" * 64),
        Post("p3", 2020, "Stopped out of $BBB"),  # wrong coin for s1, too early for s2
    ]
    out = assign_exit_types(trades, posts, clf)
    assert out["s1"] == ("take_profit", "p1")
    assert out["s2"] == ("stop_loss", "p2")
    assert out["s3"] == ("stop_loss", None)


def test_unmatched_sell_is_unknown():
    out = assign_exit_types([t("s", 1000, "sell", 1, 1)], [Post("p", 99999, "Took profit on $AAA")], clf)
    assert out["s"] == ("unknown", None)


def test_round_trip_win_and_loss():
    trades = [
        t("b1", 0, "buy", 0.10, 1000, gas=0.001), t("s1", 600, "sell", 0.15, 1000, gas=0.001),
        t("b2", 1000, "buy", 0.20, 500), t("s2", 4600, "sell", 0.16, 500),
    ]
    trips = build_round_trips(trades, {"s1": ("take_profit", "p1"), "s2": ("stop_loss", None)})
    assert len(trips) == 2
    win, loss = trips
    assert win.pnl_eth == pytest.approx(0.05) and win.pnl_pct == pytest.approx(50)
    assert win.hold_seconds == 600 and win.exit_type == "take_profit" and win.gas_eth == pytest.approx(0.002)
    assert loss.pnl_eth == pytest.approx(-0.04) and loss.pnl_pct == pytest.approx(-20)
    assert loss.exit_type == "stop_loss" and loss.hold_seconds == 3600


def test_partial_sells_stay_in_one_round_trip():
    trades = [t("b", 0, "buy", 0.1, 1000), t("s1", 10, "sell", 0.06, 500), t("s2", 20, "sell", 0.07, 499.9)]
    (trip,) = build_round_trips(trades, {"s2": ("timer", None)})
    assert trip.status == "closed" and trip.n_sells == 2 and trip.close_ts == 20
    assert trip.pnl_eth == pytest.approx(0.03) and trip.exit_type == "timer"


def test_open_position_and_coins_separate():
    trades = [t("b", 0, "buy", 0.1, 1000), t("bb", 5, "buy", 0.2, 10, coin=COIN_B)]
    trips = build_round_trips(trades)
    assert [r.status for r in trips] == ["open", "open"]
    assert all(r.pnl_eth is None for r in trips)


def test_sell_without_buy_is_unscored():
    (trip,) = build_round_trips([t("s", 10, "sell", 0.5, 100)])
    assert trip.status == "closed" and not trip.cost_basis_complete and trip.pnl_eth is None


def test_transfer_in_breaks_cost_basis():
    trades = [t("in", 0, "transfer_in", None, 1000), t("b", 1, "buy", 0.1, 1000), t("s", 2, "sell", 0.3, 2000)]
    (trip,) = build_round_trips(trades)
    assert trip.status == "closed" and trip.pnl_eth is None


def test_missing_token_amounts_each_sell_closes():
    trades = [t("b1", 0, "buy", 0.1, None), t("s1", 5, "sell", 0.2, None), t("b2", 9, "buy", 0.1, None),
              t("s2", 12, "sell", 0.05, None)]
    trips = build_round_trips(trades)
    assert [round(r.pnl_eth, 4) for r in trips] == [0.1, -0.05]
