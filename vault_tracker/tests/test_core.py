import unittest

from vault_tracker import copytrade, dlmm
from vault_tracker.alerts import Notifier
from vault_tracker.vault import _merge_same_tx

PAIR = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6"
OWNER = "3EBxNCT5qRLpCPueg9VDy81ep1W5myMupnUHTkMQZe6s"


def _put(buf, off, value, n, signed=False):
    buf[off:off + n] = value.to_bytes(n, "little", signed=signed)


def _position_bytes(lower, upper, shares):
    extra = max(0, (upper - lower + 1) - 70)
    buf = bytearray(dlmm.POSITION_BASE_SIZE + extra * dlmm.POSITION_BIN_DATA_SIZE)
    buf[0:8] = dlmm.DISC_POSITION_V2
    buf[8:40] = bytes(32)
    buf[40:72] = bytes(range(32))
    for i, s in enumerate(shares):
        if i < 70:
            _put(buf, 72 + i * 16, s, 16)
            _put(buf, 4552 + i * 48 + 32, 7, 8)  # fee_x_pending
        else:
            _put(buf, dlmm.POSITION_BASE_SIZE + (i - 70) * dlmm.POSITION_BIN_DATA_SIZE, s, 16)
    _put(buf, 7912, lower, 4, signed=True)
    _put(buf, 7916, upper, 4, signed=True)
    return bytes(buf)


def _row(lower, upper, active, step=10):
    return {"lb_pair": PAIR, "active_bin": active, "bin_step": step, "decimals_x": 9, "decimals_y": 6,
            "lower_bin": lower, "upper_bin": upper, "symbol_x": "SOL", "symbol_y": "USDC",
            "price": dlmm.bin_price(active, step, 9, 6), "has_liquidity": True, "in_range": True,
            "pair": "SOL-USDC"}


class PriceMath(unittest.TestCase):
    def test_bin_price_roundtrip(self):
        for bin_id in (-5573, -1, 0, 1, 4242):
            p = dlmm.bin_price(bin_id, 4, 9, 6)
            self.assertEqual(dlmm.price_to_bin_id(p * 1.0000001, 4, 9, 6), bin_id)

    def test_sol_price_magnitude(self):
        # SOL-USDC bin step 4 at bin -5573 is ~ $107.7
        self.assertAlmostEqual(dlmm.bin_price(-5573, 4, 9, 6), 107.7, delta=0.2)

    def test_bin_array_index_floors_negatives(self):
        cases = {0: 0, 69: 0, 70: 1, -1: -1, -70: -1, -71: -2}
        for bin_id, idx in cases.items():
            self.assertEqual(dlmm.bin_array_index(bin_id), idx, bin_id)


class Decoding(unittest.TestCase):
    def test_position_with_extension(self):
        shares = list(range(1, 81))  # 80 bins -> 10 bins in the extension
        pos = dlmm.decode_position("P", _position_bytes(-100, -21, shares))
        self.assertEqual((pos.lower_bin_id, pos.upper_bin_id, pos.width), (-100, -21, 80))
        self.assertEqual([b.share for b in pos.bins], shares)
        self.assertEqual(pos.bins[0].fee_x_pending, 7)
        self.assertEqual(pos.owner, dlmm._pk(bytes(range(32))))

    def test_rejects_wrong_discriminator(self):
        with self.assertRaises(ValueError):
            dlmm.decode_position("P", bytes(8120))

    def test_position_amounts(self):
        one = 1 << 64
        pos = dlmm.decode_position("P", _position_bytes(0, 1, [one, 3 * one]))
        bins = {
            0: dlmm.Bin(amount_x=1000, amount_y=0, liquidity_supply=4 * one, fee_x_per_token=2 * one, fee_y_per_token=0),
            1: dlmm.Bin(amount_x=0, amount_y=600, liquidity_supply=3 * one, fee_x_per_token=0, fee_y_per_token=one),
        }
        am = dlmm.position_amounts(pos, bins)
        self.assertEqual((am.amount_x, am.amount_y), (250, 600))
        # bin0: (1 * 2) + pending 7 ; bin1: pending 7
        self.assertEqual(am.fee_x, 2 + 7 + 7)
        self.assertEqual(am.fee_y, 3)

    def test_add_liquidity_event(self):
        payload = bytearray(dlmm.EVENT_IX_TAG + bytes([31, 94, 125, 90, 227, 52, 61, 186]))
        payload += bytes([1]) * 32 + bytes([2]) * 32 + bytes([3]) * 32
        payload += (5).to_bytes(8, "little") + (9).to_bytes(8, "little") + (-42).to_bytes(4, "little", signed=True)
        ev = dlmm.decode_event(bytes(payload))
        self.assertEqual(ev["event"], "AddLiquidity")
        self.assertEqual((ev["amount_x"], ev["amount_y"], ev["active_bin_id"]), (5, 9, -42))
        self.assertEqual(ev["position"], dlmm._pk(bytes([3]) * 32))

    def test_non_event_ignored(self):
        self.assertIsNone(dlmm.decode_event(bytes(40)))


class MergeEvents(unittest.TestCase):
    def test_merges_chunks_and_folds_into_open(self):
        evs = [
            {"type": "OPEN", "position": "A"},
            {"type": "ADD", "position": "A", "amount_x": 1, "amount_y": 2},
            {"type": "ADD", "position": "A", "amount_x": 10, "amount_y": 20},
            {"type": "ADD", "position": "B", "amount_x": 5, "amount_y": 5},
            {"type": "ADD", "position": "B", "amount_x": 1, "amount_y": 1},
        ]
        out = _merge_same_tx(evs)
        self.assertEqual([e["type"] for e in out], ["OPEN", "ADD"])
        self.assertEqual((out[0]["amount_x"], out[0]["amount_y"]), (11, 22))
        self.assertEqual((out[1]["position"], out[1]["amount_x"]), ("B", 6))


class CopyRange(unittest.TestCase):
    def test_mirror(self):
        s = copytrade.suggest_range(_row(-2240, -2220, -2230), "mirror")
        self.assertEqual((s["lower_bin"], s["upper_bin"], s["width"]), (-2240, -2220, 21))
        self.assertEqual(s["deposit"], "SOL + USDC")

    def test_pct(self):
        r = _row(-2240, -2220, -2230)
        s = copytrade.suggest_range(r, "pct:-10,5")
        self.assertAlmostEqual(s["pct_lower"], -10, delta=0.2)
        self.assertAlmostEqual(s["pct_upper"], 5, delta=0.2)
        self.assertLess(s["lower_bin"], -2230)
        self.assertGreater(s["upper_bin"], -2230)

    def test_scale_keeps_one_sided_shape(self):
        s = copytrade.suggest_range(_row(-2280, -2240, -2230), "scale:0.5")
        self.assertEqual((s["lower_bin"], s["upper_bin"]), (-2255, -2235))
        self.assertEqual(s["deposit"], "USDC")  # range below price -> quote token only

    def test_bad_modes(self):
        for m in ("pct:5,-5", "scale:0", "foo"):
            with self.assertRaises(ValueError):
                copytrade.parse_mode(m)

    def test_sync_status(self):
        lead = {"positions": [dict(_row(0, 10, 5), lb_pair="P1"), dict(_row(0, 10, 5), lb_pair="P2")]}
        foll = {"positions": [dict(_row(0, 10, 5), lb_pair="P2"), dict(_row(0, 10, 5), lb_pair="P3")]}
        status = {p["lb_pair"]: p["status"] for p in copytrade.sync_status(lead, foll)}
        self.assertEqual(status, {"P1": "BELUM COPY", "P2": "SYNC", "P3": "LEADER SUDAH CLOSE"})


class AlertFilter(unittest.TestCase):
    def test_min_usd_and_types(self):
        n = Notifier(min_usd=5, log_path=None)
        self.assertTrue(n.wants({"type": "OPEN", "usd": 0.1}))
        self.assertFalse(n.wants({"type": "ADD", "usd": 0.1}))
        self.assertTrue(n.wants({"type": "ADD", "usd": 50}))
        self.assertFalse(n.wants({"type": "CLAIM", "usd": 50}))
        self.assertFalse(n.wants({"type": "ADD", "usd": 50, "wallet": "follower"}, follower_label="follower"))


if __name__ == "__main__":
    unittest.main()


def _limit_order_bytes(bins):
    buf = bytearray(dlmm.LIMIT_ORDER_BASE_SIZE + len(bins) * dlmm.LIMIT_ORDER_BIN_DATA_SIZE)
    buf[0:8] = dlmm.DISC_LIMIT_ORDER
    buf[40:72] = bytes(range(32))
    _put(buf, 72, len(bins), 2)
    for i, (amount, age, bin_id, is_ask) in enumerate(bins):
        o = dlmm.LIMIT_ORDER_BASE_SIZE + i * dlmm.LIMIT_ORDER_BIN_DATA_SIZE
        _put(buf, o, amount, 8)
        _put(buf, o + 8, age, 4)
        _put(buf, o + 16, bin_id, 4, signed=True)
        buf[o + 20] = 1 if is_ask else 0
    return bytes(buf)


class LimitOrders(unittest.TestCase):
    ONE = 1 << 64

    def _bin(self, **kw):
        base = dict(amount_x=0, amount_y=0, liquidity_supply=0, fee_x_per_token=0, fee_y_per_token=0,
                    price=2 * self.ONE)
        base.update(kw)
        return dlmm.Bin(**base)

    def test_decode(self):
        lo = dlmm.decode_limit_order("L", _limit_order_bytes([(100, 3, -5, True), (50, 3, -4, True)]))
        self.assertEqual((lo.lower_bin_id, lo.upper_bin_id, lo.is_ask), (-5, -4, True))
        self.assertEqual([b.amount for b in lo.bins], [100, 50])
        self.assertEqual(lo.owner, dlmm._pk(bytes(range(32))))

    def test_fill_states_ask(self):
        lo = dlmm.decode_limit_order("L", _limit_order_bytes([(100, 3, 1, True), (100, 3, 2, True), (100, 3, 3, True)]))
        bins = {
            1: self._bin(order_age=3),                                          # not filled
            2: self._bin(order_age=4, open_order_amount=0, processed_order_remaining_amount=40,
                         total_processing_order_amount=100),                    # 60% filled
            3: self._bin(order_age=5, fulfilled_x=100, lo_fee_ask=10),          # fully filled
        }
        am = dlmm.limit_order_amounts(lo, bins)
        self.assertEqual(am.deposit_x, 300)
        self.assertEqual(am.unfilled_x, 100 + 40)
        self.assertEqual(am.swapped_y, (60 + 100) * 2)   # price 2 Y per X
        self.assertEqual(am.fee_y, 10)
        self.assertAlmostEqual(am.filled_fraction, 160 / 300)

    def test_bid_receives_x(self):
        lo = dlmm.decode_limit_order("L", _limit_order_bytes([(400, 0, 7, False)]))
        am = dlmm.limit_order_amounts(lo, {7: self._bin(order_age=2)})
        self.assertEqual((am.unfilled_y, am.swapped_x, am.total_x), (0, 200, 200))

    def test_place_event(self):
        p = bytearray(dlmm.EVENT_IX_TAG + bytes([43, 79, 27, 169, 244, 28, 225, 63]))
        p += bytes([1]) * 32 + bytes([2]) * 32 + bytes([3]) * 32 + bytes([4]) * 32
        p += (-10).to_bytes(4, "little", signed=True) + bytes([1]) + bytes(16)
        p += bytes([1]) + (-10).to_bytes(4, "little", signed=True) + (3).to_bytes(4, "little")
        p += (2).to_bytes(4, "little")
        p += (-8).to_bytes(4, "little", signed=True) + (500).to_bytes(8, "little")
        p += (-7).to_bytes(4, "little", signed=True) + (250).to_bytes(8, "little")
        ev = dlmm.decode_event(bytes(p))
        self.assertEqual(ev["event"], "PlaceLimitOrder")
        self.assertTrue(ev["is_ask"])
        self.assertEqual(ev["bins"], [(-8, 500), (-7, 250)])
        self.assertEqual(ev["position"], dlmm._pk(bytes([4]) * 32))
        self.assertEqual(ev["owner"], dlmm._pk(bytes([3]) * 32))

    def test_cancel_absorbs_close(self):
        out = _merge_same_tx([{"type": "ORDER_CANCEL", "position": "L", "amount_x": 1, "amount_y": 2},
                              {"type": "ORDER_CLOSE", "position": "L"}])
        self.assertEqual([e["type"] for e in out], ["ORDER_CANCEL"])
        self.assertTrue(out[0]["closed"])

    def test_order_copy_deposit(self):
        row = dict(_row(5, 6, 0), kind="order", side="ask")
        self.assertEqual(copytrade.suggest_range(row, "mirror")["deposit"], "SOL")
