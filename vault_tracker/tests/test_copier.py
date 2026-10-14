import os
import struct
import tempfile
import time
import unittest

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from vault_tracker import dlmm_tx
from vault_tracker.copier import Copier, load_keypair, save_keypair, settings_from_config, x_share

WALLET = "HAWK3BVnwptKRFYfVoVGhBc2TYxpyG9jmAbkHeW9tyKE"


class FailingRpc:
    """Any RPC use raises: copy_open must reject early cases before touching the network."""
    def call(self, *a, **k):
        raise RuntimeError("rpc down")

    def __getattr__(self, name):
        def f(*a, **k):
            raise RuntimeError("rpc down")
        return f


class FakeLeader:
    label = "leader"

    def __init__(self, rows=()):
        self.snapshot = {"positions": list(rows)}

    def pair_info(self, lb_pair):
        raise RuntimeError("no pool info")


def _ev(**kw):
    return {"type": "OPEN", "wallet": "leader", "position": "LEADERPOS", "lb_pair": "POOL", "ts": time.time(), **kw}


class CopierLogic(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def copier(self, run="dry", keypair=None, **settings):
        c = Copier(FailingRpc(), None, None, {"run": run, **settings}, "mirror", self.dir.name,
                   keypair=keypair, sim_wallet=None if keypair else WALLET)
        return c

    def test_x_share(self):
        self.assertEqual(x_share(-10, -1, 0), 0.0)   # range below price: only Y
        self.assertEqual(x_share(1, 10, 0), 1.0)     # range above price: only X
        self.assertAlmostEqual(x_share(-5, 5, 0), 5.5 / 11)

    def test_settings_defaults_and_parsing(self):
        s = settings_from_config({})
        self.assertEqual((s["run"], s["budget"], s["size_usd"], s["max_open"]), ("off", "SOL", 20.0, 3))
        s = settings_from_config({"COPY_RUN": "LIVE", "COPY_MAX_OPEN": "5", "COPY_EXIT_SWAP": "0", "COPY_BUDGET": "usdc"})
        self.assertEqual((s["run"], s["max_open"], s["exit_swap"], s["budget"]), ("live", 5, False, "USDC"))

    def test_mode_requirements(self):
        with self.assertRaises(ValueError):
            Copier(FailingRpc(), None, None, {"run": "live"}, "mirror", self.dir.name)
        with self.assertRaises(ValueError):
            Copier(FailingRpc(), None, None, {"run": "dry"}, "mirror", self.dir.name)
        with self.assertRaises(ValueError):
            self.copier(budget="BONK")

    def test_off_does_nothing(self):
        c = Copier(FailingRpc(), None, None, {"run": "off"}, "mirror", self.dir.name)
        self.assertEqual(c.handle([_ev()], FakeLeader()), [])

    def test_stale_and_full_are_skipped_without_rpc(self):
        c = self.copier(max_open=1)
        notes = c.handle([_ev(ts=time.time() - 3600)], FakeLeader())
        self.assertIn("DILEWATI", notes[0][0])
        self.assertIn("3600", notes[0][1][0])
        c.state["positions"]["MINE"] = {"leader_position": "OTHER", "pair": "A-B"}
        notes = c.handle([_ev()], FakeLeader())
        self.assertIn("maks 1", notes[0][1][0])

    def test_historical_and_follower_events_ignored(self):
        c = self.copier()
        self.assertEqual(c.handle([_ev(historical=True), _ev(wallet="follower")], FakeLeader()), [])

    def test_live_pauses_after_three_failures(self):
        row = {"address": "LEADERPOS", "lb_pair": "POOL", "kind": "position", "active_bin": 0}
        c = self.copier(run="live", keypair=Keypair())
        for i in range(3):
            notes = c.handle([_ev(position="LEADERPOS")], FakeLeader([row]))
        self.assertTrue(c.state["paused"])
        self.assertIn("DIJEDA", notes[-1][0])
        notes = c.handle([_ev(position="LEADERPOS")], FakeLeader([row]))
        self.assertIn("dijeda", notes[0][1][0])
        c.resume()
        self.assertFalse(c.state["paused"])

    def test_close_ignores_positions_not_opened_by_bot(self):
        c = self.copier()
        self.assertEqual(c.handle([{**_ev(), "type": "CLOSE"}], FakeLeader()), [])

    def test_keypair_roundtrip_and_no_overwrite(self):
        kp = Keypair()
        path = os.path.join(self.dir.name, "k", "bot.json")
        save_keypair(kp, path)
        self.assertEqual(load_keypair(path).pubkey(), kp.pubkey())
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with self.assertRaises(FileExistsError):
            save_keypair(Keypair(), path)


class InstructionLayout(unittest.TestCase):
    class Pool:
        address = "BGm1tav58oGcsQJehL9WXBFXF7D27vZsKefj4xJKD5Y"
        active_id = -2238
        token_x_mint = "So11111111111111111111111111111111111111112"
        token_y_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        reserve_x = reserve_y = "11111111111111111111111111111111"
        program_x = program_y = dlmm_tx.TOKEN

        def bitmap_meta(self, lo, hi):
            return dlmm_tx._m(dlmm_tx.DLMM)

    def test_add_liquidity_data(self):
        owner, pos = Keypair().pubkey(), Keypair().pubkey()
        ix = dlmm_tx.ix_add_liquidity_by_strategy(self.Pool(), pos, owner, 5, 7, -2243, -2233, "curve", True)
        d = bytes(ix.data)
        self.assertEqual(d[:8], dlmm_tx.D_ADD_BY_STRATEGY2)
        self.assertEqual(struct.unpack("<QQiiii", d[8:40]), (5, 7, -2238, 3, -2243, -2233))
        self.assertEqual(d[40], dlmm_tx.STRATEGY["curve"])
        self.assertEqual(d[41], 1)                       # favor X (single sided X)
        self.assertEqual(d[105:], struct.pack("<I", 2) + bytes([0, 0, 1, 0]))
        self.assertEqual(ix.accounts[9].pubkey, owner)   # sender signs
        self.assertTrue(ix.accounts[9].is_signer)

    def test_open_rejects_wide_ranges(self):
        with self.assertRaises(ValueError):
            dlmm_tx.open_position_ixs(FailingRpc(), type("P", (), {"hooks": []})(), Pubkey.default(),
                                      Pubkey.default(), 0, 100, 1, 1)


if __name__ == "__main__":
    unittest.main()
