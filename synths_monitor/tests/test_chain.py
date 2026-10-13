import pytest

from synthmon.http import SourceError
from synthmon.sources.chain import ExplorerChain, FallbackChain, RpcChain, TxActivity, trades_from_activity

from .conftest import COIN_A, COIN_B, WALLET

WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
POOL = "0x9999999999999999999999999999999999999999"
RELAYER = "0x8888888888888888888888888888888888888888"
TX_BUY, TX_SELL, TX_DEPOSIT = "0x" + "1" * 64, "0x" + "2" * 64, "0x" + "3" * 64
E18 = 10**18


def test_trades_from_activity():
    acts = [
        TxActivity(TX_BUY, 10, 100, {COIN_A: 1000.0}, {COIN_A: "AAA"}, eth_delta=-0.1, gas_fee_eth=0.0001),
        TxActivity(TX_SELL, 20, 200, {COIN_A: -1000.0, WETH: 0.0}, eth_delta=0.15),
        TxActivity("0xa", 30, 300, {COIN_B: 5.0}, eth_delta=0.0),  # airdrop / launch allocation
        TxActivity("0xb", 40, 400, {WETH: 1.0}, eth_delta=1.0),  # wrapping is not a trade
        TxActivity("0xc", 50, 500, {COIN_A: -10.0, COIN_B: 20.0}, eth_delta=-0.01),  # token-for-token
    ]
    trades = trades_from_activity(acts, [WETH], "chain:test")
    got = [(tr.coin, tr.side, tr.eth_amount, tr.token_amount) for tr in trades]
    assert got == [
        (COIN_A, "buy", pytest.approx(0.1), 1000.0),
        (COIN_A, "sell", pytest.approx(0.15), 1000.0),
        (COIN_B, "transfer_in", None, 5.0),
        (COIN_A, "transfer_out", None, 10.0),
        (COIN_B, "transfer_in", None, 20.0),
    ]
    assert trades[0].gas_fee_eth == 0.0001 and trades[0].id == f"tx:{TX_BUY}:{COIN_A}"


class FakeRpcHttp:
    """Answers the JSON-RPC calls RpcChain makes, from a canned chain state."""

    def __init__(self):
        self.balances = {9: 1.0, 10: 0.8999, 19: 0.8999, 20: 1.0498}  # ETH balance at block
        self.calls = []

    def post_json(self, url, payload):
        if isinstance(payload, list):  # JSON-RPC batch
            return [{"id": item["id"], **self.post_json(url, item)} for item in payload]
        m, p = payload["method"], payload["params"]
        self.calls.append(m)
        if m == "alchemy_getAssetTransfers":
            f = p[0]
            out = []
            if f.get("fromAddress") == WALLET:
                out = [
                    {"category": "external", "hash": TX_BUY, "blockNum": hex(10), "from": WALLET, "to": POOL,
                     "value": 0.1, "uniqueId": "b:ext", "metadata": {"blockTimestamp": "2026-10-13T00:00:10Z"}},
                    {"category": "erc20", "hash": TX_SELL, "blockNum": hex(20), "from": WALLET, "to": POOL,
                     "value": 1000.0, "uniqueId": "s:log:1", "asset": "AAA",
                     "rawContract": {"address": COIN_A, "value": hex(1000 * E18), "decimal": "0x12"},
                     "metadata": {"blockTimestamp": "2026-10-13T00:00:20Z"}},
                ]
            elif f.get("toAddress") == WALLET:
                out = [
                    {"category": "erc20", "hash": TX_BUY, "blockNum": hex(10), "from": POOL, "to": WALLET,
                     "value": 1000.0, "uniqueId": "b:log:1", "asset": "AAA",
                     "rawContract": {"address": COIN_A, "value": hex(1000 * E18), "decimal": "0x12"},
                     "metadata": {"blockTimestamp": "2026-10-13T00:00:10Z"}},
                    {"category": "external", "hash": TX_DEPOSIT, "blockNum": hex(5), "from": RELAYER, "to": WALLET,
                     "value": 1.0, "uniqueId": "d:ext", "metadata": {"blockTimestamp": "2026-10-13T00:00:05Z"}},
                ]
            return {"result": {"transfers": out}}
        if m == "eth_getTransactionReceipt":
            # gasUsed 100000 * 1 gwei = 0.0001 ETH, paid by the wallet in both trades
            return {"result": {"from": WALLET, "gasUsed": hex(100000), "effectiveGasPrice": hex(10**9)}}
        if m == "eth_getBalance":
            return {"result": hex(int(self.balances[int(p[1], 16)] * E18))}
        raise AssertionError(m)


def test_rpc_wallet_activity_uses_balance_diff_for_eth_leg():
    http = FakeRpcHttp()
    chain = RpcChain("https://rpc.example/v2/KEY", http)
    acts = chain.wallet_activity(WALLET, 0, 100, [WETH])
    trades = trades_from_activity(acts, [WETH], "chain:rpc")
    buy, sell = trades
    assert (buy.side, sell.side) == ("buy", "sell")
    assert buy.eth_amount == pytest.approx(0.1) and buy.token_amount == pytest.approx(1000)
    # the sell's ETH arrived as an internal transfer: only the balance change shows it
    assert sell.eth_amount == pytest.approx(0.1499 + 0.0001)
    assert sell.gas_fee_eth == pytest.approx(0.0001)
    assert buy.ts == 1791849610


class FakeExplorerHttp:
    def get_json(self, url, params=None, headers=None):
        a = params["action"]
        rows = {
            "txlist": [
                {"hash": TX_BUY, "blockNumber": "10", "timeStamp": "1791849610", "from": WALLET, "to": POOL,
                 "value": str(10**17), "gasUsed": "100000", "gasPrice": str(10**9), "isError": "0"},
                {"hash": TX_SELL, "blockNumber": "20", "timeStamp": "1791849620", "from": WALLET, "to": POOL,
                 "value": "0", "gasUsed": "100000", "gasPrice": str(10**9), "isError": "0"},
            ],
            "txlistinternal": [
                {"hash": TX_SELL, "blockNumber": "20", "timeStamp": "1791849620", "from": POOL, "to": WALLET,
                 "value": str(15 * 10**16), "isError": "0"},
            ],
            "tokentx": [
                {"hash": TX_BUY, "blockNumber": "10", "timeStamp": "1791849610", "from": POOL, "to": WALLET,
                 "contractAddress": COIN_A, "value": str(1000 * E18), "tokenDecimal": "18", "tokenSymbol": "AAA"},
                {"hash": TX_SELL, "blockNumber": "20", "timeStamp": "1791849620", "from": WALLET, "to": POOL,
                 "contractAddress": COIN_A, "value": str(1000 * E18), "tokenDecimal": "18", "tokenSymbol": "AAA"},
            ],
        }
        if a in rows:
            return {"status": "1", "message": "OK", "result": rows[a]}
        if a == "balance":
            return {"status": "1", "message": "OK", "result": str(2 * E18)}
        return {"status": "0", "message": "No transactions found", "result": []}


def test_explorer_wallet_activity_uses_internal_transfers():
    chain = ExplorerChain("https://explorer.example/api", FakeExplorerHttp())
    trades = trades_from_activity(chain.wallet_activity(WALLET, 0, 100, [WETH]), [WETH], "chain:explorer")
    assert [(t.side, round(t.eth_amount, 6), t.symbol) for t in trades] == [("buy", 0.1, "AAA"), ("sell", 0.15, "AAA")]
    assert chain.eth_balance(WALLET) == 2.0


def test_fallback_chain_tries_next_backend():
    class Broken:
        name = "explorer"

        def eth_balance(self, a):
            raise SourceError("down")

    class Works:
        name = "rpc"

        def eth_balance(self, a):
            return 1.5

    errors = []
    fc = FallbackChain([Broken(), Works()], on_error=errors.append)
    assert fc.call("eth_balance", WALLET) == 1.5 and fc.last_source == "rpc"
    with pytest.raises(SourceError):
        FallbackChain([Broken()], on_error=errors.append).call("eth_balance", WALLET)
    assert errors and "down" in errors[0]


class RateLimitedOnceHttp:
    def __init__(self):
        self.posts = 0

    def post_json(self, url, payload):
        self.posts += 1
        if self.posts == 1:
            return [{"id": 0, "error": {"code": 429, "message": "Your app has exceeded its compute units per second capacity"}}]
        return [{"id": item["id"], "result": "0x60806040" if item["params"][0] == POOL else "0x"} for item in payload]


def test_batch_retries_after_rate_limit():
    pauses = []
    chain = RpcChain("https://rpc.example/v2/KEY", RateLimitedOnceHttp(), sleep=pauses.append)
    assert chain.is_contract_many([POOL, WALLET]) == {POOL: True, WALLET: False}
    assert pauses == [1.0]


class FakeTokenChain:
    """Transfers of one coin: a curve sells to three wallets, one sells back."""

    name = "rpc"
    CURVE, ROUTER = POOL, RELAYER
    USERS = ["0x" + c * 40 for c in "def"]

    def __init__(self, pages):
        self.pages = pages  # list of (transfers, next_block)
        self.code_checks = []

    def token_transfers(self, token, start, end):
        return self.pages.pop(0)

    def is_contract_many(self, addresses):
        self.code_checks.append(sorted(addresses))
        return {a: a in (self.CURVE, self.ROUTER) for a in addresses}


def test_own_coin_stats_from_transfers(db, cfg):
    from synthmon import owncoin
    from synthmon.models import TokenTransfer
    from synthmon.util import ZERO_ADDRESS

    c = FakeTokenChain
    d, e, f = c.USERS

    def tr(n, frm, to, amt):
        return TokenTransfer(f"u{n}", COIN_A, n, None, f"0x{n}", frm, to, amt)

    page1 = [tr(1, ZERO_ADDRESS, c.CURVE, 1000), tr(2, c.CURVE, d, 100), tr(3, c.CURVE, c.ROUTER, 50)]
    page2 = [tr(4, c.ROUTER, e, 50), tr(5, c.CURVE, f, 10), tr(6, f, c.CURVE, 10), tr(7, c.CURVE, WALLET, 5)]
    chain = FallbackChain([c([(page1, 4), (page2, 101)])])
    assert owncoin.sync_transfers(db, chain, COIN_A, 100) is False  # partial history: no stats yet
    assert owncoin.sync_transfers(db, chain, COIN_A, 100) is True
    stats = owncoin.onchain_stats(db, chain, cfg, COIN_A)
    # buys: curve->d, router->e, curve->f, curve->bot wallet; sells: f->curve. Holders: d, e, bot (f sold out).
    assert (stats.buys, stats.sells, stats.holders) == (4, 1, 3)
