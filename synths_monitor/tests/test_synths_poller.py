"""Synths client parsing and a full poll, against made-up response shapes.

These shapes are invented to exercise the tolerant parser; they are not
synths.lol's real API (run `synthmon discover` to see that).
"""

import pytest

from synthmon import alerts
from synthmon.exits import ExitClassifier
from synthmon.http import SourceError
from synthmon.poller import Context, poll_once
from synthmon.sources.chain import FallbackChain, TxActivity
from synthmon.sources.synths_api import SynthsClient, find_list, get_path

from .conftest import COIN_A, OWN, WALLET

TX = "0x" + "4" * 64


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        value = self.routes[url]
        if isinstance(value, Exception):
            raise value
        return value


ROUTES = {
    "https://synths.lol/api/synths/bojjibot/trades": {"data": {"items": [
        {"id": 1, "type": "BUY", "createdAt": "2026-10-13T00:00:00Z", "token": {"address": COIN_A, "symbol": "AAA"},
         "ethAmount": "100000000000000000", "tokenAmount": "1000", "txHash": "0x" + "5" * 64},
        {"id": 2, "isBuy": False, "timestamp": 1791850000000, "coinAddress": COIN_A, "amountEth": 0.15,
         "amount": 1000, "exitReason": "take profit"},
        {"id": 3, "type": "mystery", "createdAt": "2026-10-13T00:00:00Z", "tokenAddress": COIN_A},
    ]}},
    "https://synths.lol/api/synths/bojjibot/posts": [
        {"id": "p1", "content": "Took profit on $AAA", "created_at": "2026-10-13T00:07:00Z"},
        {"id": "p2", "text": "gm", "timestamp": 1791850100},
    ],
    "https://synths.lol/api/synths/bojjibot/bags": {"holdings": [
        {"token": {"address": OWN, "symbol": "BOJJI"}, "balance": "5000", "valueEth": 0.02},
    ]},
    "https://synths.lol/api/synths/bojjibot/launched": {"coins": [
        {"address": OWN, "symbol": "BOJJI", "name": "Bojji", "createdAt": "2026-10-10T00:00:00Z"},
    ]},
    f"https://synths.lol/api/coins/{OWN}": {"data": {"symbol": "BOJJI", "holderCount": 42, "txns": {"buys": 90, "sells": 12},
                                                   "raisedEth": 0.3}},
    "https://synths.lol/api/synths/bojjibot": {"synth": {"handle": "bojjibot", "walletAddress": WALLET, "ethBalance": 1.25}},
    "https://synths.lol/api/leaderboard": {"entries": [{"handle": "other"}, {"username": "@bojjibot", "pnl": 1}]},
}


@pytest.fixture
def scfg(cfg):
    cfg["bot"]["own_coin"] = ""
    cfg["synths"]["endpoints"].update({
        "trades": "/api/synths/{handle}/trades", "posts": "/api/synths/{handle}/posts",
        "bags": "/api/synths/{handle}/bags", "launched": "/api/synths/{handle}/launched",
        "coin": "/api/coins/{coin}", "profile": "/api/synths/{handle}", "leaderboard": "/api/leaderboard",
    })
    return cfg


def test_parsers(scfg):
    c = SynthsClient(scfg, FakeHttp(ROUTES))
    trades = c.trades()
    assert [(t.side, t.eth_amount, t.token_amount, t.coin) for t in trades] == [
        ("buy", pytest.approx(0.1), 1000.0, COIN_A), ("sell", 0.15, 1000.0, COIN_A)]
    assert trades[0].symbol == "AAA" and trades[0].tx_hash == "0x" + "5" * 64
    assert trades[1].ts == 1791850000 and trades[1].reason == "take profit"
    assert [p.text for p in c.posts()] == ["Took profit on $AAA", "gm"]
    (bag,) = c.bags()
    assert (bag.coin, bag.token_amount, bag.value_eth) == (OWN, 5000.0, 0.02)
    stats = c.coin(OWN, 0.5)
    assert (stats.holders, stats.buys, stats.sells, stats.raised_eth) == (42, 90, 12, 0.3)
    assert stats.progress_pct == pytest.approx(60)
    assert c.leaderboard_entry()[0] == 2
    assert c.profile()["eth_balance"] == 1.25


@pytest.mark.parametrize("body,expected", [
    ({"progress": 0.6}, 60.0),  # fraction
    ({"progress": 60}, 60.0),  # percent
    ({"progress": 0.6, "raisedEth": 0.3}, 60.0),  # fraction, confirmed by raised ETH
    ({"progress": 0.8, "raisedEth": 0.004}, 0.8),  # 0.8 percent, confirmed by raised ETH
    ({"raisedEth": 0.45}, 90.0),
])
def test_coin_progress_scale(scfg, body, expected):
    stats = SynthsClient(scfg, FakeHttp({f"https://synths.lol/api/coins/{OWN}": body})).coin(OWN, 0.5)
    assert stats.progress_pct == pytest.approx(expected)


def test_field_override(scfg):
    scfg["synths"]["fields"] = {"trades": {"eth_amount": "weird.nested.eth"}}
    routes = {"https://synths.lol/api/synths/bojjibot/trades": [
        {"id": 9, "side": "sell", "coin": COIN_A, "ts": 1791850000, "weird": {"nested": {"eth": 0.5}}, "ethAmount": 9}]}
    (t,) = SynthsClient(scfg, FakeHttp(routes)).trades()
    assert t.eth_amount == 0.5


def test_helpers():
    assert get_path({"Coin_Address": "x"}, "coinAddress") == "x"
    assert find_list({"data": {"edges": [{"node": {"a": 1}}]}}) == [{"a": 1}]


class FakeChainBackend:
    name = "rpc"

    def __init__(self):
        self.calls = []

    def latest_block(self):
        return 100

    def wallet_activity(self, wallet, start, end, weth):
        self.calls.append(("wallet_activity", start, end))
        return [TxActivity(TX, 50, 1791850000, {COIN_A: 10.0}, {COIN_A: "AAA"}, eth_delta=-0.05, gas_fee_eth=0.0001)]

    def eth_balance(self, wallet):
        return 0.9

    def token_balances(self, wallet):
        return {COIN_A: 10.0}


def make_ctx(cfg, db, routes, chain_backends):
    notifier = alerts.Notifier(cfg)
    return Context(cfg=cfg, db=db, synths=SynthsClient(cfg, FakeHttp(routes), db.save_raw),
                   chain=FallbackChain(chain_backends), notifier=notifier, classifier=ExitClassifier())


def test_poll_uses_synths_first(scfg, db):
    backend = FakeChainBackend()
    st = poll_once(make_ctx(scfg, db, ROUTES, [backend]))
    assert st.ok and st.sources["trades"] == "synths" and st.sources["balance"] == "synths"
    assert backend.calls == []  # chain not needed
    assert db.get_state("own_coin") == OWN  # learned from "launched"
    assert db.query("SELECT COUNT(*) AS n FROM posts")[0]["n"] == 2
    trip = db.query("SELECT * FROM round_trips")[0]
    assert trip["status"] == "closed" and trip["exit_type"] == "take_profit"
    assert trip["pnl_eth"] == pytest.approx(0.05)
    snap = db.latest_wallet_snapshot()
    assert snap["eth_balance"] == 1.25 and snap["total_value_eth"] == pytest.approx(1.27)
    assert db.latest_coin_snapshot(OWN)["progress_pct"] == pytest.approx(60)
    assert db.query("SELECT rank FROM leaderboard")[0]["rank"] == 2
    assert db.query("SELECT COUNT(*) AS n FROM raw_responses")[0]["n"] == 7


def test_poll_falls_back_to_chain_and_backs_off(scfg, db):
    routes = dict(ROUTES)
    routes["https://synths.lol/api/synths/bojjibot/trades"] = SourceError("HTTP 503")
    routes["https://synths.lol/api/synths/bojjibot"] = SourceError("HTTP 503")
    backend = FakeChainBackend()
    ctx = make_ctx(scfg, db, routes, [backend])
    st = poll_once(ctx)
    assert st.ok and st.sources["trades"] == "rpc" and st.sources["balance"] == "rpc"
    assert backend.calls == [("wallet_activity", 0, 100)]
    (t,) = [t for t in db.trades() if t.source == "chain:rpc"]
    assert t.side == "buy" and t.eth_amount == pytest.approx(0.05)
    assert any("synths:trades" in e for e in st.errors)
    # The failing endpoint is skipped until its backoff expires; the chain resumes from its cursor.
    st2 = poll_once(ctx)
    assert st2.ok and ctx.synths.http.calls.count("https://synths.lol/api/synths/bojjibot/trades") == 1
    assert db.get_state("wallet_block") == 101 and len(backend.calls) == 1  # no new blocks yet
