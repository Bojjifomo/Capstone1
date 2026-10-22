import os
import struct
import tempfile
import time
import unittest

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from vault_tracker import dlmm_tx
from vault_tracker.copier import Copier, follow_plan, load_keypair, save_keypair, settings_from_config, x_share

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
        row = {"address": "LEADERPOS", "lb_pair": "POOL", "kind": "position", "active_bin": 0,
               "has_liquidity": True, "value_usd": 100.0}
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


class Tok:
    def __init__(self, mint, symbol, decimals):
        self.mint, self.symbol, self.decimals = mint, symbol, decimals

    def ui(self, raw):
        return raw / 10 ** self.decimals


class Prices:
    def __init__(self, prices):
        self.prices = prices


class PoolLeader(FakeLeader):
    """Leader with one pool: X = TOKX (6 dp, $2), Y = SOL (9 dp, $100)."""
    X, Y = Tok("TOKX", "TOKX", 6), Tok("So11111111111111111111111111111111111111112", "SOL", 9)

    def pair_info(self, lb_pair):
        return None, self.X, self.Y


class FollowAndExits(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.prices = Prices({PoolLeader.X.mint: 2.0, PoolLeader.Y.mint: 100.0})

    def tearDown(self):
        self.dir.cleanup()

    def copier(self, run="dry", **settings):
        return Copier(FailingRpc(), None, self.prices, {"run": run, **settings}, "mirror", self.dir.name,
                      keypair=Keypair() if run == "live" else None, sim_wallet=None if run == "live" else WALLET)

    def test_follow_plan(self):
        self.assertEqual(follow_plan(100, 0, 300), ("add", 100, 200))
        self.assertEqual(follow_plan(0, 50, 150), ("remove", 0.25, 200))
        self.assertEqual(follow_plan(0, 195, 5), ("remove", 1.0, 200))    # >= 97% counts as everything
        self.assertEqual(follow_plan(0, 80, 0.1)[:2], ("remove", 1.0))
        self.assertEqual(follow_plan(30, 10, 220), ("add", 20, 200))      # net of both in one poll
        self.assertEqual(follow_plan(0, 0, 100)[0], None)

    def test_vault_weight_sizing(self):
        c = self.copier(size_mode="vault", follow_pct=100, max_usd=1000)
        c.equity_usd = lambda bal=None, values=None: 200.0
        leader = PoolLeader()
        leader.snapshot = {"total_usd": 1000.0, "positions": []}
        size, why = c._size({"value_usd": 250.0}, leader, {})
        self.assertAlmostEqual(size, 50.0)             # 25% of the vault -> 25% of the bot
        self.assertIn("25.0%", why)
        c.s["follow_pct"] = 50
        self.assertAlmostEqual(c._size({"value_usd": 250.0}, leader, {})[0], 25.0)
        c.s["max_usd"] = 10
        self.assertEqual(c._size({"value_usd": 250.0}, leader, {})[0], 10)

    def test_empty_open_waits_for_liquidity(self):
        c = self.copier()
        empty = {"address": "LEADERPOS", "lb_pair": "POOL", "kind": "position", "active_bin": 0,
                 "has_liquidity": False, "value_usd": 0.0}
        self.assertEqual(c.handle([_ev()], PoolLeader([empty])), [])
        self.assertIn("LEADERPOS", c.state["pending"])
        calls = []
        c.copy_open = lambda ev, leader, pending_since=None: calls.append(pending_since) or None
        c.handle([], PoolLeader([dict(empty, has_liquidity=True, value_usd=500.0)]))
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0])                 # re-tried as a pending open (no age check)
        c.state["pending"]["LEADERPOS"] = {"ev": _ev(), "since": time.time()}
        c.handle([{**_ev(), "type": "CLOSE"}], PoolLeader())
        self.assertNotIn("LEADERPOS", c.state["pending"])

    def test_dry_run_follows_add_remove_and_close(self):
        c = self.copier(min_action_usd=1)
        c.state["positions"]["dry-LEADERPOS"] = {"leader_position": "LEADERPOS", "pair": "TOKX-SOL", "lower": 0,
                                                 "upper": 10, "opened_at": 1, "usd": 20.0, "cost_usd": 20.0,
                                                 "ratio": 0.1, "virtual": True}
        row = {"address": "LEADERPOS", "lb_pair": "POOL", "kind": "position", "value_usd": 300.0}
        add = {**_ev(), "type": "ADD", "amount_x": 0, "amount_y": 1_000_000_000}   # 1 SOL = $100
        notes = c.handle([add], PoolLeader([row]))
        self.assertIn("TAMBAH", notes[0][0])
        self.assertAlmostEqual(c.state["positions"]["dry-LEADERPOS"]["cost_usd"], 30.0)  # +$100 x 0.1
        rem = {**_ev(), "type": "REMOVE", "amount_x": 0, "amount_y": 1_000_000_000}
        notes = c.handle([rem], PoolLeader([dict(row, value_usd=300.0)]))          # $100 of $400 = 25%
        self.assertIn("TARIK 25%", notes[0][0])
        self.assertAlmostEqual(c.state["positions"]["dry-LEADERPOS"]["cost_usd"], 22.5)
        notes = c.handle([{**_ev(), "type": "CLOSE"}], PoolLeader())
        self.assertIn("TUTUP", notes[0][0])
        self.assertEqual(c.state["positions"], {})
        self.assertIn("LEADERPOS", c.state["done"])    # never re-opened later

    def test_add_respects_cap_and_pause(self):
        c = self.copier(max_usd=25, min_action_usd=1)
        c.state["positions"]["dry-P"] = {"leader_position": "P", "pair": "A-B", "usd": 20.0, "cost_usd": 20.0,
                                         "ratio": 0.5, "virtual": True}
        c._follow_add("dry-P", 100, 200)
        self.assertAlmostEqual(c.state["positions"]["dry-P"]["cost_usd"], 25.0)   # capped at max_usd
        c.state["paused"] = True
        from vault_tracker.copier import Skip
        with self.assertRaises(Skip):
            c._follow_add("dry-P", 100, 200)

    def test_take_profit_needs_two_checks_and_stop_loss(self):
        c = self.copier(run="live", tp_pct=10, sl_pct=5)
        c.state["positions"]["REAL"] = {"leader_position": "LP", "pair": "A-B", "usd": 100.0, "cost_usd": 100.0}
        value = {"value": 112.0}
        c.position_values = lambda keys=None: {"REAL": {"value_usd": value["value"], "fees_usd": 1.0,
                                                        "in_range": True, "has_liquidity": True}}
        closed = []
        c._close_position = lambda key, reason: closed.append(reason) or ("closed", [])
        c.check()
        self.assertEqual(closed, [])                    # one reading past the target is not enough
        self.assertEqual(c.state["positions"]["REAL"]["pnl_pct"], 13.0)
        c.check()
        self.assertIn("take profit", closed[0])
        value["value"] = 90.0
        c.check(); c.check()
        self.assertIn("stop loss", closed[1])

    def test_out_of_range_timer(self):
        c = self.copier(run="live", oor_close_min=1)
        c.state["positions"]["REAL"] = {"leader_position": "LP", "pair": "A-B", "usd": 100.0, "cost_usd": 100.0}
        c.position_values = lambda keys=None: {"REAL": {"value_usd": 100.0, "fees_usd": 0.0,
                                                        "in_range": False, "has_liquidity": True}}
        closed = []
        c._close_position = lambda key, reason: closed.append(reason) or ("closed", [])
        c.check()
        self.assertEqual(closed, [])
        c.state["positions"]["REAL"]["oor_since"] -= 61
        c.check()
        self.assertIn("out of range", closed[0])

    def test_emergency_exit_closes_all_and_pauses(self):
        c = self.copier()
        for k in ("dry-A", "dry-B"):
            c.state["positions"][k] = {"leader_position": k[4:], "pair": k, "usd": 10.0, "cost_usd": 10.0, "virtual": True}
        notes = c.emergency_exit()
        self.assertTrue(c.state["paused"])
        self.assertEqual(c.state["positions"], {})
        self.assertIn("DARURAT", notes[0][0])
        self.assertEqual(len(notes), 3)

    def test_sol_only_deposits_sol_without_swaps(self):
        from unittest import mock

        from vault_tracker.copier import Skip
        c = self.copier(sol_only=True)
        self.prices.prices.pop(PoolLeader.X.mint)        # the pool token may have no price at all
        c.tokens = type("T", (), {"get": staticmethod(lambda m: PoolLeader.X if m == "TOKX" else PoolLeader.Y)})()
        pool = type("Pool", (), {"token_x_mint": "TOKX", "token_y_mint": PoolLeader.Y.mint, "active_id": 0})()
        bal = {"lamports": 10 * 10 ** 9}
        with mock.patch.object(dlmm_tx, "open_position_ixs", return_value=[]), \
                mock.patch.object(dlmm_tx, "simulate", return_value=(True, 1, [], None)):
            lines = []
            _, ax, ay, _, _ = c._deposit(pool, -50, 10, 200.0, lines, 0, bal)
        self.assertEqual(ax, 0)
        self.assertEqual(ay, 2 * 10 ** 9)                 # $200 at $100/SOL, all SOL
        self.assertFalse([l for l in lines if l.startswith("Swap")])
        with self.assertRaises(Skip):                     # range only above the price: SOL cannot fill it
            c._deposit(pool, 1, 20, 200.0, [], 0, bal)
        with self.assertRaises(ValueError):
            self.copier(sol_only=True, budget="USDC")

    def test_live_drops_dry_run_positions(self):
        c = self.copier()
        c.state["positions"]["dry-A"] = {"leader_position": "A", "pair": "A", "usd": 1, "virtual": True}
        c.save()
        kp = Keypair()
        c2 = Copier(FailingRpc(), None, self.prices, {"run": "live"}, "mirror", self.dir.name, keypair=kp)
        self.assertEqual(c2.state["positions"], {})


class TelegramCommands(unittest.TestCase):
    class FakeCopier:
        def __init__(self):
            self.calls = []
            self.ps = [{"position": "POS1", "pair": "DOPE-SOL", "usd": 20, "cost_usd": 20, "pnl_pct": 3.2,
                        "in_range": True, "opened_at": 1}]

        def positions_list(self):
            return self.ps

        def withdraw(self, key, pct):
            self.calls.append(("withdraw", key, pct))
            return "🤖 COPY TARIK", ["ok"]

        def add(self, key, usd):
            self.calls.append(("add", key, usd))
            return "🤖 COPY TAMBAH", ["ok"]

        def close_position(self, key, reason):
            self.calls.append(("close", key))
            return "🤖 COPY TUTUP", ["ok"]

        def emergency_exit(self, reason):
            self.calls.append(("emergency",))
            return [("🚨 DARURAT", [])]

        def pause(self, reason):
            self.calls.append(("pause",))

        def resume(self):
            self.calls.append(("resume",))

    def msg(self, text, chat=42, kind="private", age=0):
        return {"chat": {"id": chat, "type": kind}, "text": text, "date": int(time.time() - age)}

    def test_commands(self):
        from vault_tracker.tgcontrol import TelegramControl
        c = self.FakeCopier()
        tg = TelegramControl(c, "TOKEN", "42")
        self.assertIn("1. DOPE-SOL", tg.on_message(self.msg("/posisi")))
        tg.on_message(self.msg("/tarik 1 50"))
        tg.on_message(self.msg("/tambah 1 $15"))
        tg.on_message(self.msg("/tutup 1"))
        self.assertEqual(c.calls, [("withdraw", "POS1", 50.0), ("add", "POS1", 15.0), ("close", "POS1")])
        self.assertIn("/darurat YA", tg.on_message(self.msg("/darurat")))
        self.assertEqual(len(c.calls), 3)               # nothing happens without YA
        self.assertIn("DARURAT", tg.on_message(self.msg("/darurat ya")))
        self.assertIn("Nomor posisi", tg.on_message(self.msg("/tutup 9")))
        self.assertIn("Contoh", tg.on_message(self.msg("/tarik 1")))

    def test_only_fresh_messages_from_own_private_chat(self):
        from vault_tracker.tgcontrol import TelegramControl
        c = self.FakeCopier()
        tg = TelegramControl(c, "TOKEN", "42")
        self.assertIsNone(tg.on_message(self.msg("/darurat YA", chat=7)))
        self.assertIsNone(tg.on_message(self.msg("/darurat YA", age=600)))
        self.assertIn("chat pribadi", tg.on_message(self.msg("/darurat YA", kind="group")))
        self.assertIsNone(tg.on_message(self.msg("halo")))
        self.assertEqual(c.calls, [])


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
