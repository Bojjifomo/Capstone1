import unittest

from synths_portfolio import analyze, build_ledger, calculate, parse_bag, resolve_bag

SYNTH = "0x5157000000000000000000000000000000000001"
OWNER = "0x0bad000000000000000000000000000000000002"
CURVE = "0xc0e0000000000000000000000000000000000003"
FEES = "0xfee0000000000000000000000000000000000004"
COIN_A = "0xaaaa000000000000000000000000000000000005"
COIN_B = "0xbbbb000000000000000000000000000000000006"


def _t(tx, block, category, frm, to, amount, token=None, symbol="ETH"):
    raw = int(round(amount * 10**18))
    return {
        "uniqueId": f"{tx}:{category}:{frm}:{to}:{token}",
        "hash": tx, "blockNum": hex(block), "category": category,
        "from": frm, "to": to, "asset": symbol, "value": amount,
        "rawContract": {"value": hex(raw), "address": token, "decimal": "0x12"},
    }


def eth_tx(tx, block, frm, to, amount, category="external"):
    return _t(tx, block, category, frm, to, amount)


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
    eth_tx("0x1", 1, OWNER, SYNTH, 0.02),                                   # fund
    eth_tx("0x2", 2, SYNTH, CURVE, 0.005),                                  # beli A
    coin_tx("0x2", 2, CURVE, SYNTH, 100, COIN_A, "A"),
    coin_tx("0x3", 3, SYNTH, CURVE, 50, COIN_A, "A"),                       # jual separuh A
    eth_tx("0x3", 3, CURVE, SYNTH, 0.003, category="internal"),
    eth_tx("0x4", 4, SYNTH, CURVE, 0.004),                                  # beli B
    coin_tx("0x4", 4, CURVE, SYNTH, 10, COIN_B, "B"),
    eth_tx("0x5", 5, FEES, SYNTH, 0.001, category="internal"),              # fee kreator
    eth_tx("0x6", 6, SYNTH, OWNER, 0.002),                                  # withdraw
]
GAS = 0.0001
ETH_BALANCE = 0.02 - 0.005 + 0.003 - 0.004 + 0.001 - 0.002 - GAS


class LedgerTest(unittest.TestCase):
    def test_trades_and_flows(self):
        led = build_ledger(SYNTH, TRANSFERS, owners=[OWNER])
        self.assertAlmostEqual(led.deposits, 0.02)
        self.assertAlmostEqual(led.withdrawals, 0.002)
        self.assertAlmostEqual(led.other_in, 0.001)
        a, b = led.positions[COIN_A], led.positions[COIN_B]
        self.assertAlmostEqual(a.qty_open, 50)
        self.assertAlmostEqual(a.cost_open, 0.0025)
        self.assertAlmostEqual(a.realized, 0.0005)
        self.assertAlmostEqual(b.cost_open, 0.004)

    def test_without_owner_every_plain_eth_flow_is_deposit_or_withdraw(self):
        led = build_ledger(SYNTH, TRANSFERS)
        self.assertAlmostEqual(led.deposits, 0.021)
        self.assertAlmostEqual(led.other_in, 0.0)

    def test_duplicate_transfers_are_ignored(self):
        led = build_ledger(SYNTH, TRANSFERS + TRANSFERS, owners=[OWNER])
        self.assertAlmostEqual(led.deposits, 0.02)

    def test_coin_to_coin_swap_moves_cost_basis(self):
        transfers = TRANSFERS[:3] + [
            coin_tx("0x9", 9, SYNTH, CURVE, 100, COIN_A, "A"),
            coin_tx("0x9", 9, CURVE, SYNTH, 7, COIN_B, "B"),
        ]
        led = build_ledger(SYNTH, transfers)
        self.assertAlmostEqual(led.positions[COIN_A].realized, 0.0)
        self.assertAlmostEqual(led.positions[COIN_B].cost_open, 0.005)

    def test_weth_counts_as_eth(self):
        weth = "0x4200000000000000000000000000000000000006"
        transfers = [
            _t("0x1", 1, "erc20", SYNTH, CURVE, 0.003, weth, "WETH"),
            coin_tx("0x1", 1, CURVE, SYNTH, 5, COIN_A, "A"),
        ]
        led = build_ledger(SYNTH, transfers, weth=weth)
        self.assertAlmostEqual(led.positions[COIN_A].cost_open, 0.003)
        self.assertNotIn(weth, led.positions)


class AnalyzeTest(unittest.TestCase):
    def test_portfolio_totals_reconcile(self):
        led = build_ledger(SYNTH, TRANSFERS, owners=[OWNER])
        eth_usd = 2000.0
        holdings = [
            holding(COIN_A, "A", 50, usd=0.004 * eth_usd / 50),   # A sekarang 0.004 ETH
            holding(COIN_B, "B", 10),                             # B nggak ada harga
        ]
        r = analyze(ETH_BALANCE, holdings, led, eth_usd, overrides={"b": 0.003})
        self.assertAlmostEqual(r["coins_value_eth"], 0.007)
        self.assertAlmostEqual(r["total_portfolio_eth"], ETH_BALANCE + 0.007)
        self.assertAlmostEqual(r["net_deposit_eth"], 0.018)
        self.assertAlmostEqual(r["total_pnl_eth"], 0.0019)
        self.assertAlmostEqual(r["realized_eth"], 0.0005)
        self.assertAlmostEqual(r["unrealized_eth"], 0.0005)
        self.assertAlmostEqual(r["other_eth"], 0.001)
        self.assertAlmostEqual(r["gas_and_fees_eth"], -GAS)

    def test_unpriced_bag_falls_back_to_cost(self):
        led = build_ledger(SYNTH, TRANSFERS, owners=[OWNER])
        r = analyze(ETH_BALANCE, [holding(COIN_B, "B", 10)], led)
        bag_b = next(c for c in r["bags"] if c["symbol"] == "B")
        self.assertAlmostEqual(bag_b["value_eth"], 0.004)
        self.assertTrue(bag_b["price_source"].startswith("=modal"))

    def test_airdrop_is_separate_and_optional(self):
        spam = "0x5555000000000000000000000000000000000007"
        led = build_ledger(SYNTH, TRANSFERS, owners=[OWNER])
        holdings = [holding(spam, "SPAM", 1000, usd=0.001)]
        with_free = analyze(0.0, holdings, led, 2000.0)
        without = analyze(0.0, holdings, led, 2000.0, only_traded=True)
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
