import tempfile
import time
import unittest

from vault_tracker.compare import Comparator, match_actions, range_overlap

SOL = "So11111111111111111111111111111111111111112"


class Tok:
    def __init__(self, symbol):
        self.symbol = symbol


class Pair:
    def __init__(self, x, y):
        self.token_x_mint, self.token_y_mint = x, y


class FakeTracker:
    POOLS = {"DOPE": Pair("DOPEMINT", SOL), "BORDR": Pair("BORDRMINT", "JUPUSD")}

    def __init__(self, display):
        self.display, self.address = display, display + "ADDR"
        self.state = {"events": [], "positions": {}}
        self.snapshot = {"positions": []}

    def pair_info(self, lb):
        p = self.POOLS[lb]
        return p, Tok(lb), Tok("SOL" if p.token_y_mint == SOL else "JupUSD")

    def open(self, pos, lb, ts, rng=(0, 10)):
        self.state["events"].append({"type": "OPEN", "position": pos, "lb_pair": lb, "ts": ts, "range": list(rng)})
        self.state["positions"][pos] = {"lb_pair": lb, "opened_at": ts, "closed_at": None, "lower": rng[0], "upper": rng[1]}

    def close(self, pos, ts):
        p = self.state["positions"][pos]
        self.state["events"].append({"type": "CLOSE", "position": pos, "lb_pair": p["lb_pair"], "ts": ts})
        p["closed_at"] = ts

    def hold(self, *pools):
        self.snapshot = {"positions": [{"kind": "position", "lb_pair": lb, "has_liquidity": True, "lower_bin": 0,
                                        "upper_bin": 10, "value_usd": 100.0, "in_range": True} for lb in pools]}


class Compare(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.a, self.b = FakeTracker("VAULT"), FakeTracker("COPY")

    def tearDown(self):
        self.dir.cleanup()

    def cmp(self, **kw):
        return Comparator(self.a, self.b, 600, self.dir.name, **kw)

    def test_overlap_and_matching(self):
        self.assertEqual(range_overlap((0, 9), (0, 9)), 1.0)
        self.assertEqual(range_overlap((0, 9), (5, 14)), 0.5)
        self.assertIsNone(range_overlap(None, (0, 1)))
        a = [{"key": "a1", "type": "OPEN", "lb_pair": "P", "pairkey": "X", "ts": 100}]
        b = [{"key": "b1", "type": "OPEN", "lb_pair": "P", "pairkey": "X", "ts": 900},
             {"key": "b2", "type": "OPEN", "lb_pair": "Q", "pairkey": "X", "ts": 160}]
        pairs, al, bl = match_actions(a, b, 600)
        self.assertEqual(pairs[0][1]["key"], "b2")     # same token pair in another pool, within the window
        self.assertEqual([e["key"] for e in bl], ["b1"])

    def test_same_actions_are_sama(self):
        t = self.now - 3600
        self.a.open("A1", "DOPE", t)
        self.b.open("B1", "DOPE", t - 60)
        self.a.hold("DOPE"); self.b.hold("DOPE")
        c = self.cmp()
        notes = c.update(self.now)
        self.assertIn("Pembanding aktif", notes[0][0])  # first run: one summary, no flood
        self.assertEqual(c.view["verdict"], "SAMA")
        self.assertEqual(c.view["stats"]["a_matched"], 1)
        self.b.open("B2", "DOPE", self.now + 10)       # a new action later: alerted once it has a match
        self.a.open("A2", "DOPE", self.now + 40)
        notes = c.update(self.now + 60)
        self.assertTrue(any("SAMA: keduanya BUKA" in n[0] for n in notes))
        self.assertEqual(c.update(self.now + 70), [])

    def test_copied_wallet_exits_alone_is_beda_then_sama_again(self):
        t = self.now - 7200
        self.a.open("A1", "BORDR", t); self.b.open("B1", "BORDR", t + 30)
        self.a.hold("BORDR"); self.b.hold("BORDR")
        c = self.cmp()
        c.update(self.now)
        self.b.close("B1", self.now + 10)
        self.b.hold()
        self.assertEqual(c.update(self.now + 60), [])  # within the grace period
        self.assertEqual(c.view["verdict"], "MENUNGGU")
        notes = c.update(self.now + 700)
        self.assertEqual(c.view["verdict"], "BEDA")
        self.assertEqual(len(notes), 1)                 # the action alert; the holdings alert is not repeated
        self.assertIn("BEDA: COPY TUTUP BORDR", notes[0][0])
        self.assertEqual(c.update(self.now + 2000), [])
        self.a.close("A1", self.now + 2100)
        self.a.hold()
        notes = c.update(self.now + 2200)
        self.assertTrue(any("SAMA LAGI" in n[0] for n in notes))

    def test_moving_a_range_is_not_beda(self):
        t = self.now - 7200
        self.a.open("A1", "DOPE", t); self.b.open("B1", "DOPE", t + 20)
        self.b.close("B1", t + 300); self.b.open("B2", "DOPE", t + 320, (2, 12))   # B moves its range
        self.a.hold("DOPE"); self.b.hold("DOPE")
        c = self.cmp()
        c.update(self.now)
        self.assertEqual(c.view["verdict"], "SAMA")
        self.assertEqual(c.view["stats"]["moved"], 2)

    def test_new_pool_only_on_copied_wallet(self):
        c = self.cmp()
        c.update(self.now)
        self.b.open("B9", "DOPE", self.now + 5)
        self.b.hold("DOPE")
        c.update(self.now + 10)
        notes = c.update(self.now + 700)
        self.assertEqual(c.view["verdict"], "BEDA")
        self.assertTrue(any("COPY BUKA DOPE-SOL, VAULT tidak" in n[0] for n in notes))

    def test_sol_only_ignores_other_pools(self):
        c = self.cmp(sol_only=True)
        c.update(self.now)
        self.b.open("B9", "BORDR", self.now + 5)
        self.b.hold("BORDR")
        self.assertEqual(c.update(self.now + 700), [])
        self.assertEqual(c.view["verdict"], "SAMA")


if __name__ == "__main__":
    unittest.main()
