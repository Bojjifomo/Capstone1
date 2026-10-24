"""The chain loader against a fake RPC: python3 -m unittest discover -s rh_lp_analyzer/tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chain as C  # noqa: E402
import config  # noqa: E402
import explorer  # noqa: E402
import fetch as F  # noqa: E402
import rpc as R  # noqa: E402

W = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
POOL = "0x" + "11" * 32
T0 = config.CHAIN_LAUNCH_TS  # block n is at T0 + n seconds
HEAD = 5030


def w(a):
    return "0x" + C.addr_word(a)


def transfer(block, tid, frm, to, tx):
    return {"address": config.POSITION_MANAGER, "blockNumber": hex(block), "logIndex": hex(1),
            "transactionHash": tx, "data": "0x", "topics": [C.TRANSFER, w(frm), w(to), "0x" + C.word(tid)]}


def modify(block, tid, delta, tx, idx=0):
    return {"address": config.POOL_MANAGER, "blockNumber": hex(block), "logIndex": hex(idx), "transactionHash": tx,
            "topics": [C.MODIFY_LIQUIDITY, POOL, w(config.POSITION_MANAGER)],
            "data": "0x" + C.word(-600) + C.word(600) + C.word(delta) + C.word(tid)}


class FakeRPC:
    def __init__(self):
        self.logs = [transfer(1000, 7, config.ZERO, W, "0xa"), modify(1000, 7, 10 ** 18, "0xa"),
                     modify(1500, 8, 10 ** 18, "0xc"),  # someone else's position in the same pool
                     modify(1800, 7, -(10 ** 17), "0xd"),  # a partial withdrawal in between
                     modify(2000, 7, -(9 * 10 ** 17), "0xb"), transfer(2000, 7, W, config.ZERO, "0xb"),
                     transfer(3000, 9, config.ZERO, W, "0xe"), modify(3000, 9, 10 ** 18, "0xe", 2),
                     transfer(3500, 10, config.ZERO, OTHER, "0xf")]
        self.log_calls = 0
        self.receipts = 0
        self.liquidity = {9: 10 ** 18}  # open positions' liquidity now

    def call(self, method, params):
        if method == "eth_blockNumber":
            return hex(HEAD + F.CONFIRMATIONS)
        raise AssertionError(method)

    def batch(self, calls, size=40):
        out = []
        for m, p in calls:
            if m == "eth_getBlockByNumber":
                out.append({"timestamp": hex(T0 + int(p[0], 16))})
            elif m == "eth_getTransactionReceipt":
                self.receipts += 1
                out.append({"logs": [lg for lg in self.logs if lg["transactionHash"] == p[0]]})
            else:
                raise AssertionError(m)
        return out

    def get_logs(self, flt, lo, hi, chunk, progress=None, ceiling=None, max_calls=None):
        self.log_calls += 1
        out = []
        for lg in self.logs:
            if lg["address"] != flt["address"] or not lo <= int(lg["blockNumber"], 16) <= hi:
                continue
            if all(t is None or (i < len(lg["topics"]) and lg["topics"][i] == t) for i, t in enumerate(flt["topics"])):
                out.append(lg)
        return out, chunk


class NoPricer:
    def __init__(self, *a, **k):
        pass

    def value_events(self, ids):
        return 0

    def value_open(self, ids, block):
        return {}

    def aggregate(self, ids, vals):
        return 0

    def refresh_tokens(self, pools):
        return None


def nft_items(rpc):
    """What the explorer would list for W: its NFT transfers and LP transactions."""
    trs, txs = [], {}
    for lg in rpc.logs:
        if lg["address"] == config.POSITION_MANAGER:
            d = C.decode_nft_transfer(lg)
            if W in (d["from"], d["to"]):
                trs.append({**d, "ts": T0 + d["block"]})
    for lg in rpc.logs:  # every LP transaction of W's own positions was sent by W
        if lg["address"] == config.POOL_MANAGER and int(C.words(lg["data"])[3], 16) in (7, 9):
            txs[lg["transactionHash"]] = {"tx": lg["transactionHash"], "block": int(lg["blockNumber"], 16),
                                          "ts": T0 + int(lg["blockNumber"], 16)}
    return trs, list(txs.values())


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.con = F.connect(Path(self.d.name) / "c.db")
        self.rpc = FakeRPC()
        self.saved = (F.Pricer, F.Loader.refresh_pools_tokens, F.multicall, explorer.nft_transfers,
                      explorer.lp_transactions)
        F.Pricer, F.Loader.refresh_pools_tokens = NoPricer, lambda self: None
        F.multicall = lambda rpc, calls, block: [hex(self.rpc.liquidity.get(int(cd[-64:], 16), 0)) for _, cd in calls]
        # only the sources a test sets up: no finder database, no Alchemy
        self.cfg = {**config.load_env(), "LOOKBACK_DAYS": "0", "WALLET_FINDER_DB": "0", "RPC_URL": "",
                    "CALL_RPC_URL": ""}

    def tearDown(self):
        (F.Pricer, F.Loader.refresh_pools_tokens, F.multicall, explorer.nft_transfers,
         explorer.lp_transactions) = self.saved
        self.con.close()
        self.d.cleanup()

    def load(self):
        return F.Loader(self.con, self.rpc, self.rpc, self.cfg).load([W], T0 + 10)

    def check_positions(self):
        pos = {r["token_id"]: dict(r) for r in self.con.execute("SELECT * FROM positions")}
        self.assertEqual(set(pos), {7, 9})
        self.assertEqual((pos[7]["owner"], pos[7]["open_block"], pos[7]["close_block"], pos[7]["n_events"]),
                         (W, 1000, 2000, 3))
        self.assertEqual(pos[7]["close_ts"], T0 + 2000)
        self.assertIsNone(pos[9]["close_ts"])
        self.assertEqual({r[0] for r in self.con.execute("SELECT DISTINCT token_id FROM events")}, {7, 9})

    def test_explorer_needs_no_log_scans(self):
        trs, txs = nft_items(self.rpc)
        explorer.nft_transfers = lambda addr, since: trs
        explorer.lp_transactions = lambda addr, since: txs
        res = self.load()
        self.check_positions()
        self.assertEqual((self.rpc.log_calls, res["scanned"]), (0, 0))
        n = self.rpc.receipts
        self.load()  # again: receipts are read once
        self.assertEqual(self.rpc.receipts, n)

    def test_rpc_fallback_scans_only_what_does_not_add_up(self):
        def down(*a):
            raise explorer.ExplorerError("explorer unavailable (HTML)")
        explorer.nft_transfers = explorer.lp_transactions = down
        res = self.load()
        self.check_positions()
        self.assertEqual(res["scanned"], 1)  # #7's partial withdrawal wasn't in its mint / burn receipts
        self.assertGreater(F.get_meta(self.con, "explorer_off_until"), 0)
        n = self.rpc.log_calls
        self.load()  # again at the same head: everything is cached, nothing read
        self.assertEqual(self.rpc.log_calls, n)

    def finder_db(self, fwd=HEAD):
        """A finder database with everything in rpc.logs, synced up to block fwd."""
        fpath = Path(self.d.name) / "wallets.db"
        f = __import__("sqlite3").connect(fpath)
        f.executescript("""
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE transfers (block INTEGER, log_index INTEGER, ts INTEGER, token_id INTEGER, from_addr TEXT,
                                    to_addr TEXT, PRIMARY KEY (block, log_index));
            CREATE TABLE events (block INTEGER, log_index INTEGER, tx TEXT, ts INTEGER, token_id INTEGER, pool TEXT,
              tl INTEGER, tu INTEGER, delta TEXT, v_state INTEGER DEFAULT 0, a0 REAL, a1 REAL, f0 REAL, f1 REAL,
              u0 REAL, u1 REAL, lp_fee INTEGER, PRIMARY KEY (block, log_index));
            CREATE TABLE pools (id TEXT PRIMARY KEY, c0 TEXT, c1 TEXT, fee INTEGER, spacing INTEGER, hooks TEXT);
            CREATE TABLE tokens (addr TEXT PRIMARY KEY, symbol TEXT, decimals INTEGER, supply REAL, price_usd REAL,
                                 mcap REAL, updated INTEGER);""")
        f.executemany("INSERT INTO meta VALUES(?,?)", [("back_block", "1"), ("fwd_block", str(fwd))])
        for lg in self.rpc.logs:
            b = int(lg["blockNumber"], 16)
            if b > fwd:
                continue
            if lg["address"] == config.POSITION_MANAGER:
                d = C.decode_nft_transfer(lg)
                f.execute("INSERT INTO transfers VALUES(?,?,?,?,?,?)", (b, d["log_index"], T0 + b, d["token_id"],
                                                                         d["from"], d["to"]))
            else:
                d = C.decode_modify(lg)
                f.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,1,1,1,0,0,1,1,3000)",
                          (b, d["log_index"], d["tx"], T0 + b, d["salt"], d["pool"], d["tl"], d["tu"], str(d["delta"])))
        f.commit()
        f.close()
        self.cfg["WALLET_FINDER_DB"] = str(fpath)

    def test_wallet_finder_database_needs_no_rpc_reads(self):
        self.finder_db()
        res = self.load()
        self.check_positions()
        self.assertEqual((self.rpc.log_calls, self.rpc.receipts, res["scanned"]), (0, 0, 0))
        self.assertEqual(res["sources"], ["RH Wallet Finder database"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM events WHERE v_state=1").fetchone()[0], 4)  # priced already

    def no_cheap_logs(self):
        """The explorer is down and the only RPC reads 10 blocks per eth_getLogs."""
        def down(*a):
            raise explorer.ExplorerError("explorer answered HTTP 403")
        explorer.nft_transfers = explorer.lp_transactions = down

        def tiny(flt, lo, hi, chunk, progress=None, ceiling=None, max_calls=None):
            raise R.LogsInterrupted(R.TooCostly("rpc allows only 10 blocks per eth_getLogs"), [], lo, chunk)
        self.rpc.get_logs = tiny

    def test_unreadable_stretch_is_left_out_not_fatal(self):
        self.finder_db(fwd=2500)  # the finder's sync stopped before #9 was opened
        self.no_cheap_logs()
        res = self.load()
        pos = {r["token_id"] for r in self.con.execute("SELECT token_id FROM positions")}
        self.assertEqual(pos, {7})  # what the finder had is still analysed
        self.assertEqual([(g["lo"], g["hi"]) for g in res["gaps"]], [(2501, HEAD)])
        self.assertIn("10 blocks", res["gaps"][0]["why"])
        self.assertIn("HTTP 403", res["diag"]["Blockscout explorer"])

    def test_no_source_at_all_says_why(self):
        self.no_cheap_logs()
        with self.assertRaises(F.NoSource) as cm:
            self.load()
        self.assertIn("RPC eth_getLogs", cm.exception.diag)
        self.assertEqual(cm.exception.diag["RH Wallet Finder database"], "switched off")

    def test_block_at(self):
        self.assertEqual(F.Loader(self.con, self.rpc, self.rpc, {}).block_at(T0 + 2345, HEAD), 2345)


class ExplorerTest(unittest.TestCase):
    def test_pages_until_since(self):
        pm = config.POSITION_MANAGER
        pages = [
            {"items": [{"block_number": 900, "log_index": 3, "transaction_hash": "0xAB", "timestamp":
                        "2026-10-20T08:00:00.000000Z", "from": {"hash": config.ZERO}, "to": {"hash": W.upper()},
                        "token": {"address_hash": pm}, "total": {"token_id": "77"}}],
             "next_page_params": {"block_number": 900, "index": 3}},
            {"items": [{"block_number": 800, "log_index": 1, "transaction_hash": "0xcd", "timestamp":
                        "2026-10-01T00:00:00Z", "from": {"hash": W}, "to": {"hash": config.ZERO},
                        "token": {"address_hash": pm}, "total": {"token_id": "5"}}],
             "next_page_params": {"block_number": 800, "index": 1}},
            {"items": [], "next_page_params": None}]
        asked = []

        def get(path, params=None, timeout=20):
            asked.append(dict(params))
            return pages[len(asked) - 1]
        saved, explorer.get = explorer.get, get
        try:
            got = explorer.nft_transfers(W, explorer.iso_ts("2026-10-10T00:00:00Z"))
        finally:
            explorer.get = saved
        self.assertEqual(got, [{"block": 900, "log_index": 3, "tx": "0xab", "ts": explorer.iso_ts(
            "2026-10-20T08:00:00Z"), "token_id": 77, "from": config.ZERO, "to": W.lower()}])
        self.assertEqual(asked[1]["block_number"], 900)  # the next page's cursor was sent
        self.assertEqual(len(asked), 2)  # stopped at the first item older than since


class RangesTest(unittest.TestCase):
    def test_missing_and_mark(self):
        with tempfile.TemporaryDirectory() as d:
            con = F.connect(Path(d) / "c.db")
            self.assertEqual(F.missing(con, "k", 1, 100), [(1, 100)])
            F.mark(con, "k", 10, 20)
            F.mark(con, "k", 40, 50)
            self.assertEqual(F.missing(con, "k", 1, 100), [(1, 9), (21, 39), (51, 100)])
            F.mark(con, "k", 21, 39)
            self.assertEqual([tuple(r) for r in con.execute("SELECT lo, hi FROM scans WHERE key='k'")], [(10, 50)])
            con.close()
        self.assertEqual(F.merge([(5, 9), (1, 3), (4, 4), (20, 30)]), [(1, 9), (20, 30)])


if __name__ == "__main__":
    unittest.main()
