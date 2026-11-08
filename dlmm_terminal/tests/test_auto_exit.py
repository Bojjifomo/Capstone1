import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dlmm_terminal import web


class AutoExitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = [mock.patch.object(web, "_auto_file", lambda: self.tmp / "auto_exit.json"),
                  mock.patch("dlmm_terminal.cli.append", lambda *a, **k: None),
                  mock.patch("dlmm_terminal.cli.notify", lambda *a, **k: None)]
        for x in self.p:
            x.start()
        self.cfg = {"trade": {"maxLossPct": 5}}

    def tearDown(self):
        for x in self.p:
            x.stop()

    def ladders(self, pct):
        return {"POOL": {"symbol": "X", "positions": ["A", "B"], "pnlSol": 0.1, "depositSol": 3, "pnlPct": pct}}

    def test_below_target_stays_armed(self):
        web.auto_set("POOL", "X", 5)
        with mock.patch.object(web, "_ladders", lambda w: self.ladders(4.9)), mock.patch.object(web.node, "run") as run:
            self.assertEqual(web.auto_exit_once(self.cfg, "W"), [])
            run.assert_not_called()
        self.assertEqual(web.auto_load()["POOL"]["status"], "armed")

    def test_fires_close_swap_once(self):
        web.auto_set("POOL", "X", 3)
        with mock.patch.object(web, "_ladders", lambda w: self.ladders(3.2)), \
                mock.patch.object(web.node, "run", return_value={"ok": True, "solBack": 3.1}) as run:
            fired = web.auto_exit_once(self.cfg, "W")
            self.assertEqual(len(fired), 1)
            args = run.call_args[0][1]
            self.assertEqual(run.call_args[0][0], "reenter.mjs")
            self.assertIn("A,B", args)
            self.assertNotIn("--reopen", args)                 # exit = close + swap, never re-open
            self.assertEqual(web.auto_exit_once(self.cfg, "W"), [])   # done: does not fire again
        self.assertEqual(web.auto_load()["POOL"]["status"], "done")

    def test_token_not_sold_is_retried(self):
        web.auto_set("POOL", "X", 3)
        fail = {"ok": False, "step": "swap", "error": "HTTP 429", "leftover": {"mint": "MINT", "pool": "POOL", "raw": "5"}}
        with mock.patch.object(web, "_ladders", lambda w: self.ladders(3.5)), \
                mock.patch.object(web.node, "run", return_value=fail):
            web.auto_exit_once(self.cfg, "W")
        self.assertEqual(web.auto_load()["POOL"]["status"], "selling")
        # next check: the sale is retried with sell.mjs and lands
        with mock.patch.object(web, "_ladders", lambda w: {}), \
                mock.patch.object(web.node, "run", return_value={"ok": True, "sol": 0.4, "via": "dlmm", "signature": "s"}) as run:
            web.auto_exit_once(self.cfg, "W")
            self.assertEqual(run.call_args[0][0], "sell.mjs")
            self.assertIn("MINT", run.call_args[0][1])
        self.assertEqual(web.auto_load()["POOL"]["status"], "done")

    def test_sale_gives_up_after_tries(self):
        web.auto_set("POOL", "X", 3)
        with web._auto_lock:
            d = web.auto_load()
            d["POOL"].update(status="selling", leftover={"mint": "MINT", "pool": "POOL"}, sellTries=0)
            web.auto_save(d)
        with mock.patch.object(web, "_ladders", lambda w: {}), \
                mock.patch.object(web.node, "run", return_value={"ok": False, "error": "no route"}):
            for _ in range(web.SELL_TRIES):
                web.auto_exit_once(self.cfg, "W")
        self.assertEqual(web.auto_load()["POOL"]["status"], "failed")

    def test_off_and_gone(self):
        web.auto_set("POOL", "X", 10)
        web.auto_set("POOL", "X", None)
        self.assertEqual(web.auto_load(), {})
        web.auto_set("POOL", "X", 10)
        with mock.patch.object(web, "_ladders", lambda w: {}):
            web.auto_exit_once(self.cfg, "W")
        self.assertEqual(web.auto_load()["POOL"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
