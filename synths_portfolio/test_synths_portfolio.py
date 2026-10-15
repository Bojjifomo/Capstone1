import unittest

from synths_portfolio import (
    SYNTH_TRADE_TOPIC, ZERO_ADDRESS, Alchemy, analyze, block_peers, build_ledger, calculate,
    detect_owner, parse_bag, resolve_bag, trade_price_from_receipts,
)

# Model wallet synth di Robinhood Chain: wallet-nya smart contract, tiap trade dikirim
# operator (tx bernilai 0 ke wallet), dan ETH-nya lewat transfer internal yang nggak
# ada di riwayat, jadi jumlah ETH diambil dari selisih saldo per block (ETH_DELTAS).
SYNTH = "0x5157000000000000000000000000000000000001"
OWNER = "0x0bad000000000000000000000000000000000002"
OPERATOR = "0x0e7a000000000000000000000000000000000003"
SETUP = "0x5e70000000000000000000000000000000000004"
POOL = "0x9001000000000000000000000000000000000005"
COIN_A = "0xaaaa000000000000000000000000000000000006"
COIN_B = "0xbbbb000000000000000000000000000000000007"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"


def _t(tx, block, category, frm, to, amount, token=None, symbol="ETH"):
    raw = int(round(amount * 10**18))
    return {
        "uniqueId": f"{tx}:{category}:{frm}:{to}:{token}",
        "hash": tx, "blockNum": hex(block), "category": category,
        "from": frm, "to": to, "asset": symbol, "value": amount,
        "rawContract": {"value": hex(raw), "address": token, "decimal": "0x12"},
    }


def call(tx, block, frm, value=0.0):
    """Tx ke wallet synth (fund kalau ada value, selain itu call operator/owner)."""
    return _t(tx, block, "external", frm, SYNTH, value)


def coin_tx(tx, block, frm, to, qty, token, symbol):
    return _t(tx, block, "erc20", frm, to, qty, token, symbol)


def holding(token, symbol, qty, usd=None):
    return {
        "tokenAddress": token,
        "tokenBalance": hex(int(qty * 10**18)),
        "tokenMetadata": {"symbol": symbol, "decimals": 18},
        "tokenPrices": [{"currency": "usd", "value": str(usd)}] if usd is not None else [],
    }


TRANSFERS = [
    call("0x1", 1, OWNER, 0.02),                              # fund
    call("0x2", 2, SETUP),                                    # fee setup
    call("0x3", 3, OPERATOR),                                 # beli A
    coin_tx("0x3", 3, POOL, SYNTH, 100, COIN_A, "A"),
    call("0x4", 4, OPERATOR),                                 # jual separuh A
    coin_tx("0x4", 4, SYNTH, POOL, 50, COIN_A, "A"),
    call("0x5", 5, OPERATOR),                                 # beli B
    coin_tx("0x5", 5, POOL, SYNTH, 10, COIN_B, "B"),
    call("0x7", 7, OWNER),                                    # withdraw
]
ETH_DELTAS = {1: 0.02, 2: -0.002, 3: -0.005, 4: 0.003, 5: -0.004, 6: 0.001, 7: -0.002}
EXTRA_PEERS = {6: {OPERATOR}}  # reward platform, nggak ada di riwayat transfer
ETH_BALANCE = sum(ETH_DELTAS.values())


def ledger(**kw):
    kw.setdefault("owners", [OWNER])
    kw.setdefault("extra_peers", EXTRA_PEERS)
    return build_ledger(SYNTH, kw.pop("transfers", TRANSFERS), kw.pop("deltas", ETH_DELTAS), WETH, **kw)


class LedgerTest(unittest.TestCase):
    def test_trades_and_flows(self):
        led = ledger()
        self.assertAlmostEqual(led.deposits, 0.02)
        self.assertAlmostEqual(led.withdrawals, 0.002)
        self.assertAlmostEqual(led.other_in, 0.001)
        self.assertAlmostEqual(led.other_out, 0.002)
        a, b = led.positions[COIN_A], led.positions[COIN_B]
        self.assertAlmostEqual(a.qty_open, 50)
        self.assertAlmostEqual(a.cost_open, 0.0025)
        self.assertAlmostEqual(a.realized, 0.0005)
        self.assertAlmostEqual(b.cost_open, 0.004)
        self.assertEqual(led.blocks, 7)

    def test_without_owner_every_plain_eth_flow_is_deposit_or_withdraw(self):
        led = ledger(owners=None)
        self.assertAlmostEqual(led.deposits, 0.021)
        self.assertAlmostEqual(led.withdrawals, 0.004)
        self.assertAlmostEqual(led.other_in, 0.0)

    def test_duplicate_transfers_are_ignored(self):
        led = ledger(transfers=TRANSFERS + TRANSFERS)
        self.assertAlmostEqual(led.positions[COIN_A].qty_open, 50)

    def test_coin_to_coin_swap_moves_cost_basis(self):
        transfers = TRANSFERS[:4] + [
            call("0x9", 9, OPERATOR),
            coin_tx("0x9", 9, SYNTH, POOL, 100, COIN_A, "A"),
            coin_tx("0x9", 9, POOL, SYNTH, 7, COIN_B, "B"),
        ]
        led = ledger(transfers=transfers, deltas={1: 0.02, 3: -0.005, 9: 0.0})
        self.assertAlmostEqual(led.positions[COIN_A].realized, 0.0)
        self.assertAlmostEqual(led.positions[COIN_B].cost_open, 0.005)

    def test_weth_counts_as_eth(self):
        transfers = [
            call("0x1", 1, OPERATOR),
            coin_tx("0x1", 1, SYNTH, POOL, 0.003, WETH, "WETH"),
            coin_tx("0x1", 1, POOL, SYNTH, 5, COIN_A, "A"),
        ]
        led = ledger(transfers=transfers, deltas={1: 0.0})
        self.assertAlmostEqual(led.positions[COIN_A].cost_open, 0.003)
        self.assertNotIn(WETH, led.positions)

    def test_detect_owner_is_first_funder(self):
        self.assertEqual(detect_owner(SYNTH, TRANSFERS, ETH_DELTAS, EXTRA_PEERS), OWNER)

    def test_detect_owner_from_factory_creation(self):
        creator = "0xc0ea000000000000000000000000000000000008"
        # Fund awal masuk lewat factory waktu wallet dibikin: nggak ada di riwayat transfer.
        deltas = {1: 0.05, **{b + 10: d for b, d in ETH_DELTAS.items()}}
        self.assertEqual(detect_owner(SYNTH, [], deltas, {1: {creator}}), creator)

    def test_block_peers_uses_recipient_when_wallet_sends(self):
        transfers = [_t("0x1", 1, "external", SYNTH, OWNER, 0.01)]
        self.assertEqual(block_peers(SYNTH, transfers)[1], {OWNER})


class FakeChain(Alchemy):
    """Alchemy palsu: saldo wallet naik/turun di block tertentu."""

    def __init__(self, changes):
        super().__init__("key", "robinhood-mainnet")
        self.changes = changes

    def balances(self, address, blocks):
        return {b: sum(d for cb, d in self.changes.items() if cb <= b) for b in blocks}


class EthHistoryTest(unittest.TestCase):
    def test_finds_untracked_balance_changes(self):
        chain = FakeChain({100: 5, 2500: -2, 7777: 1, 50000: 3, 50001: -1})
        deltas, found = chain.eth_history(SYNTH, {2500, 50001}, head=100000)
        self.assertEqual(deltas, {100: 5, 2500: -2, 7777: 1, 50000: 3, 50001: -1})
        self.assertEqual(found, {100, 7777, 50000})

    def test_nothing_untracked(self):
        chain = FakeChain({10: 4, 20: -1})
        deltas, found = chain.eth_history(SYNTH, {10, 20}, head=30)
        self.assertEqual(deltas, {10: 4, 20: -1})
        self.assertEqual(found, set())


def trade_log(token_in, token_out, amount_in, amount_out):
    pad = lambda a: "0x" + "0" * 24 + a[2:]
    return {"topics": [SYNTH_TRADE_TOPIC, pad(token_in), pad(token_out)],
            "data": "0x" + f"{amount_in:064x}" + f"{amount_out:064x}"}


class TradePriceTest(unittest.TestCase):
    def test_prefers_latest_sell_over_newer_buy(self):
        receipts = [
            {"logs": [trade_log(ZERO_ADDRESS, COIN_A, 3 * 10**15, 10**24)]},   # beli, paling baru
            {"logs": [trade_log(COIN_A, ZERO_ADDRESS, 10**24, 2 * 10**15)]},   # jual
        ]
        self.assertAlmostEqual(trade_price_from_receipts(receipts, COIN_A), 2e-9)

    def test_falls_back_to_buy_and_respects_decimals(self):
        receipts = [{"logs": [trade_log(ZERO_ADDRESS, COIN_A, 10**15, 500 * 10**6)]}]
        self.assertAlmostEqual(trade_price_from_receipts(receipts, COIN_A, decimals=6), 2e-6)

    def test_other_tokens_are_ignored(self):
        receipts = [{"logs": [trade_log(COIN_B, ZERO_ADDRESS, 10**18, 10**15)]}]
        self.assertIsNone(trade_price_from_receipts(receipts, COIN_A))


class AnalyzeTest(unittest.TestCase):
    def test_portfolio_totals_reconcile(self):
        eth_usd = 2000.0
        holdings = [
            holding(COIN_A, "A", 50, usd=0.004 * eth_usd / 50),   # A sekarang 0.004 ETH
            holding(COIN_B, "B", 10),                             # B nggak ada harga di Alchemy
        ]
        r = analyze(ETH_BALANCE, holdings, ledger(), eth_usd, overrides={"b": 0.003})
        self.assertAlmostEqual(r["coins_value_eth"], 0.007)
        self.assertAlmostEqual(r["total_portfolio_eth"], ETH_BALANCE + 0.007)
        self.assertAlmostEqual(r["net_deposit_eth"], 0.018)
        self.assertAlmostEqual(r["total_pnl_eth"], 0.0)
        self.assertAlmostEqual(r["realized_eth"], 0.0005)
        self.assertAlmostEqual(r["unrealized_eth"], 0.0005)
        self.assertAlmostEqual(r["other_eth"], -0.001)
        self.assertAlmostEqual(r["untracked_eth"], 0.0)

    def test_trade_price_is_used_when_alchemy_has_none(self):
        r = analyze(ETH_BALANCE, [holding(COIN_B, "B", 10)], ledger(), trade_prices={COIN_B: 0.0005})
        bag_b = next(c for c in r["bags"] if c["symbol"] == "B")
        self.assertAlmostEqual(bag_b["value_eth"], 0.005)
        self.assertEqual(bag_b["price_source"], "trade terakhir")

    def test_unpriced_bag_falls_back_to_cost(self):
        r = analyze(ETH_BALANCE, [holding(COIN_B, "B", 10)], ledger())
        bag_b = next(c for c in r["bags"] if c["symbol"] == "B")
        self.assertAlmostEqual(bag_b["value_eth"], 0.004)
        self.assertTrue(bag_b["price_source"].startswith("=modal"))

    def test_airdrop_is_separate_and_optional(self):
        spam = "0x5555000000000000000000000000000000000009"
        holdings = [holding(spam, "SPAM", 1000, usd=0.001)]
        with_free = analyze(0.0, holdings, ledger(), 2000.0)
        without = analyze(0.0, holdings, ledger(), 2000.0, only_traded=True)
        self.assertEqual([c["symbol"] for c in with_free["free"]], ["SPAM"])
        self.assertAlmostEqual(with_free["free_coins_value_eth"], 0.0005)
        self.assertAlmostEqual(without["free_coins_value_eth"], 0.0)


class CalcTest(unittest.TestCase):
    def test_resolve_bag_from_any_two_numbers(self):
        for bag in (
            {"cost_eth": 0.002, "value_eth": 0.0021},
            {"cost_eth": 0.002, "upnl_eth": 0.0001},
            {"cost_eth": 0.002, "pct": 5},
            {"value_eth": 0.0021, "upnl_eth": 0.0001},
            {"value_eth": 0.0021, "pct": 5},
            {"upnl_eth": 0.0001, "pct": 5},
        ):
            b = resolve_bag(bag)
            self.assertAlmostEqual(b["cost_eth"], 0.002)
            self.assertAlmostEqual(b["value_eth"], 0.0021)
            self.assertAlmostEqual(b["pct"], 0.05)

    def test_resolve_bag_needs_two_numbers(self):
        with self.assertRaises(ValueError):
            resolve_bag({"coin": "X", "value_eth": 0.001})

    def test_parse_bag(self):
        self.assertEqual(parse_bag("$KELV:0.0019:-0.000051456"),
                         {"coin": "KELV", "value_eth": 0.0019, "upnl_eth": -0.000051456})
        self.assertEqual(parse_bag("MOTHMAN:0.0017:2.1%"),
                         {"coin": "MOTHMAN", "value_eth": 0.0017, "pct": 2.1})

    def test_kinbit_example(self):
        r = calculate({
            "wallet_eth": 0.014, "total_pnl_eth": 0.000046379, "funded_eth": 0.02,
            "take_profit_pct": 6, "stop_loss_pct": 6,
            "bags": [
                {"coin": "MOTHMAN", "value_eth": 0.0017, "upnl_eth": 0.000035698},
                {"coin": "KELV", "value_eth": 0.0019, "upnl_eth": -0.000051456},
                {"coin": "GRONAUT", "value_eth": 0.0023, "upnl_eth": 0.000062137},
            ],
        })
        self.assertAlmostEqual(r["coins_value_eth"], 0.0059)
        self.assertAlmostEqual(r["total_portfolio_eth"], 0.0199)
        self.assertAlmostEqual(r["unrealized_eth"], 0.000046379)
        self.assertAlmostEqual(r["realized_eth"], 0.0)
        self.assertAlmostEqual(r["profit_eth"], -0.0001)
        self.assertAlmostEqual(r["take_profit"]["bags_pnl_eth"], 0.005853621 * 0.06)
        self.assertAlmostEqual(r["stop_loss"]["bags_pnl_eth"], -0.005853621 * 0.06)


if __name__ == "__main__":
    unittest.main()
