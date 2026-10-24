"""RPC error handling: python3 -m unittest discover -s rh_lp_analyzer/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rpc as R  # noqa: E402


class ResultTest(unittest.TestCase):
    def test_spent_quota_on_getlogs_is_not_a_range_error(self):
        with self.assertRaises(R.QuotaError):
            R._result({"error": {"code": -32000, "message": "Daily response quota exceeded"}}, "eth_getLogs", "x")

    def test_range_errors_stay_range_errors(self):
        with self.assertRaises(R.RangeError):
            R._result({"error": {"message": "query exceeds max block range 10000"}}, "eth_getLogs")
        with self.assertRaises(R.RangeError):  # Alchemy free tier: "Upgrade", but not a spent quota
            R._result({"error": {"message": "Under the Free tier plan, you can make eth_getLogs requests with up "
                                            "to a 10 block range. Upgrade to PAYG for expanded block range."}},
                      "eth_getLogs")

    def test_pool_moves_logs_to_the_other_rpc_when_one_is_spent(self):
        a, b = R.RPC("https://a.example"), R.RPC("https://b.example")

        def spent(flt, lo, hi, chunk, progress=None, ceiling=None, max_calls=None):
            raise R.LogsInterrupted(R.QuotaError("a.example: plan limit reached"), [], lo, chunk)
        a.get_logs = spent
        b.get_logs = lambda flt, lo, hi, chunk, progress=None, ceiling=None, max_calls=None: ([{"ok": hi}], chunk)
        pool = R.RPCPool([a, b])
        logs, _ = pool.get_logs({}, 1, 100, 10)
        self.assertTrue(logs)
        self.assertGreater(a.spent_until, 0)

    def test_tiny_log_range_stops_instead_of_spending_the_quota(self):
        r = R.RPC("https://alchemy.example")
        r.workers = 1
        r.call = lambda m, p: (_ for _ in ()).throw(R.RangeError("up to a 10 block range", 10))
        with self.assertRaises(R.LogsInterrupted) as cm:
            r.get_logs({}, 1, 1_000_000, 1000, max_calls=3000)
        self.assertIsInstance(cm.exception.cause, R.TooCostly)


if __name__ == "__main__":
    unittest.main()
