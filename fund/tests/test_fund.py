import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fund  # noqa: E402
import lp  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731
ME = "0x" + "ab" * 20


def books(tmp):
    return store.Books(Path(tmp) / "fund.db", 16000)


class NetWorth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.b = books(self.tmp)

    def test_usd_converts_with_the_rate_of_its_day_and_debt_subtracts(self):
        b = self.b
        b.save_account({"name": "BCA", "kind": "bank", "sleeve": 0})
        b.save_account({"name": "BTC", "kind": "crypto", "sleeve": 2, "currency": "USD"})
        b.save_account({"name": "Paylater", "kind": "debt"})
        b.set_fx(15000, D(40))
        b.set_fx(16000, D(1))
        b.set_balance("BCA", 10_000_000, D(40))
        b.set_balance("btc", 1000, D(40))
        b.set_balance("BCA", 12_000_000)
        b.set_balance("BTC", 1000)
        b.set_balance("Paylater", 500_000)
        s = b.summary()
        self.assertEqual(s["net_worth_idr"], 12_000_000 + 16_000_000 - 500_000)
        self.assertEqual(s["change_30d_idr"], s["net_worth_idr"] - (10_000_000 + 15_000_000))
        self.assertEqual({x["sleeve"]: x["idr"] for x in s["sleeves"]}, {0: 12_000_000, 2: 16_000_000, 5: -500_000})
        self.assertEqual(b.net_worth_on(D(40)), 25_000_000)

    def test_unknown_account_and_bad_numbers_are_refused(self):
        with self.assertRaises(store.Invalid):
            self.b.set_balance("nope", 1)
        self.b.save_account({"name": "X"})
        with self.assertRaises(store.Invalid):
            self.b.set_balance("X", "abc")
        with self.assertRaises(store.Invalid):
            self.b.set_balance("X", 1, "2026-13-01")
        with self.assertRaises(store.Invalid):
            self.b.save_account({"name": "x"} | {"id": None, "name": "X"})  # duplicate name

    def test_auto_accounts_cannot_be_typed_over(self):
        acc = self.b.ensure_auto_account("lp", "LP (auto)", "lp", "USD", 4)
        self.assertEqual(acc, self.b.ensure_auto_account("lp", "LP (auto)", "lp", "USD", 4))
        with self.assertRaises(store.Invalid):
            self.b.set_balance(acc, 5)
        self.b.set_balance(acc, 5, allow_auto=True)

    def test_history_starts_at_first_balance(self):
        self.b.save_account({"name": "BCA"})
        self.assertEqual(len(self.b.summary()["history"]), 1)
        self.b.set_balance("BCA", 1, D(100))
        h = self.b.summary()["history"]
        self.assertGreaterEqual(len(h), 3)
        self.assertEqual(h[-1], [TODAY.isoformat(), 1])


class Flows(unittest.TestCase):
    def test_month_totals_budget_runway_and_saving_rate(self):
        b = books(tempfile.mkdtemp())
        b.save_account({"name": "RDPU", "kind": "rdpu", "sleeve": 0})
        b.set_balance("RDPU", 60_000_000)
        last = TODAY.replace(day=1) - timedelta(days=1)
        b.add_flow({"kind": "expense", "amount": 10_000_000, "category": "Makan", "day": last.isoformat()})
        b.add_flow({"kind": "income", "amount": 20_000_000, "category": "gaji", "day": last.isoformat()})
        b.add_flow({"kind": "expense", "amount": 50_000, "category": "makan", "day": TODAY.isoformat()})
        b.set_budget("makan", 3_000_000)
        s = b.summary()
        self.assertEqual(s["avg_monthly_spend"], 10_000_000)
        self.assertEqual(s["runway_months"], 6.0)
        self.assertEqual(s["savings_rate_pct"], 50.0)
        self.assertEqual(s["month"]["spent"], 50_000)
        self.assertEqual(s["month"]["categories"], [{"category": "makan", "spent": 50_000, "budget": 3_000_000}])
        b.set_budget("makan", 0)
        self.assertIsNone(b.summary()["month"]["budget_total"])
        with self.assertRaises(store.Invalid):
            b.add_flow({"amount": -5, "category": "x"})
        with self.assertRaises(store.Invalid):
            b.delete_flow(999)


class Notes(unittest.TestCase):
    def test_agents_propose_and_argue(self):
        b = books(tempfile.mkdtemp())
        rid = b.add_rec({"title": "RDPU"}, author="Thorne")
        b.add_note(rid, "Fee switching belum dihitung", "Sable", "challenge")
        b.add_note(rid, "Angka kurs udah 3 hari", "Sable", "question")
        r = b.recs()[0]
        self.assertEqual(r["author"], "Thorne")
        self.assertEqual([(n["author"], n["stance"]) for n in r["notes"]], [("Sable", "challenge"), ("Sable", "question")])
        with self.assertRaises(store.Invalid):
            b.add_note(rid, "x", "Sable", "maybe")
        with self.assertRaises(store.Invalid):
            b.add_note(999, "x")
        with self.assertRaises(store.Invalid):
            b.add_rec({"title": "x"}, author="<script>")


class Recs(unittest.TestCase):
    def test_lifecycle_and_actualization(self):
        b = books(tempfile.mkdtemp())
        rid = b.add_rec({"title": "Cash di atas runway ke RDPU", "sleeve": 0, "expected_idr_month": 150_000})
        self.assertEqual(b.recs()[0]["status"], "proposed")
        b.update_rec(rid, {"status": "accepted"})
        b.update_rec(rid, {"status": "done", "result": "Pindah 30jt", "actual_idr_month": 140_000})
        r = b.recs()[0]
        self.assertEqual((r["status"], r["done"], r["decided"]), ("done", TODAY.isoformat(), TODAY.isoformat()))
        s = b.summary()["recs"]
        self.assertEqual((s["done"], s["expected_idr_month_done"], s["actual_idr_month_done"]), (1, 150_000, 140_000))
        with self.assertRaises(store.Invalid):
            b.update_rec(rid, {"status": "maybe"})


def fake_wallet_finder(path, now):
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE positions (token_id INTEGER PRIMARY KEY, owner TEXT, close_ts INTEGER, valued INTEGER,
                              now_usd REAL, pnl_usd REAL, fee_usd REAL);
      CREATE TABLE events (block INTEGER, log_index INTEGER, ts INTEGER, token_id INTEGER, delta TEXT,
                           v_state INTEGER, a0 REAL, a1 REAL, f0 REAL, f1 REAL, u0 REAL, u1 REAL);
      CREATE TABLE wallets (addr TEXT PRIMARY KEY, usd_bal REAL, bal_ts INTEGER);""")
    con.executemany("INSERT INTO positions VALUES(?,?,?,?,?,?,?)", [
        (1, ME, None, 1, 300.0, None, None),          # open, priced
        (2, ME, now - 3600, 1, 0, 25.0, 10.0),         # closed an hour ago
        (3, ME, None, 0, None, None, None),            # open, not priced yet
        (4, "0x" + "cd" * 20, None, 1, 999.0, None, None),  # someone else
    ])
    con.executemany("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
        (1, 0, now - 7200, 1, "100", 1, 100, 1, 0, 0, 1.0, 200.0),   # deposit 300
        (2, 0, now - 7000, 2, "50", 1, 50, 0, 0, 0, 1.0, 1.0),        # deposit 50
        (3, 0, now - 3600, 2, "-50", 1, 65, 0, 10, 0, 1.0, 1.0),      # withdraw 65 + fee 10
        (4, 0, now - 3000, 3, "10", 0, None, None, None, None, None, None),  # not priced
        (5, 0, now - 40 * 86400, 1, "5", 1, 5, 0, 0, 0, 1.0, 1.0),    # outside 30 days
    ])
    con.execute("INSERT INTO wallets VALUES(?,?,?)", (ME, 120.0, now))
    con.commit()
    con.close()


class LP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "wallets.db"
        self.now = int(time.time())
        fake_wallet_finder(self.db, self.now)

    def test_snapshot_reads_only_my_wallets(self):
        s = lp.snapshot(self.db, [ME.upper().replace("0X", "0x")])
        self.assertEqual((s["open_positions"], s["unpriced_open"], s["open_usd"], s["idle_usd"], s["total_usd"]),
                         (2, 1, 300.0, 120.0, 420.0))

    def test_flows(self):
        f = lp.flows(self.db, [ME], 30, now=self.now)
        self.assertEqual((f["deposit_usd"], f["withdraw_usd"], f["fees_usd"], f["net_in_usd"]), (350.0, 65.0, 10.0, 285.0))
        self.assertEqual((f["closed_positions"], f["realized_pnl_usd"], f["unpriced_events"]), (1, 25.0, 1))

    def test_unavailable(self):
        with self.assertRaises(lp.Unavailable):
            lp.snapshot(Path(self.tmp) / "missing.db", [ME])
        with self.assertRaises(lp.Unavailable):
            lp.snapshot(self.db, [])

    def test_tracker_writes_the_auto_account(self):
        b = books(self.tmp)
        t = server.LPTracker(b, {"lp_wallets": [ME], "lp_db": str(self.db)})
        t.refresh()
        acc = b.summary()["accounts"]
        self.assertEqual([(a["name"], a["amount"], a["sleeve"]) for a in acc], [("LP (auto)", 420.0, 4)])
        self.assertEqual(b.summary()["net_worth_idr"], 420 * 16000)
        bad = server.LPTracker(b, {"lp_wallets": [ME], "lp_db": str(Path(self.tmp) / "nope.db")})
        self.assertIsNone(bad.refresh())
        self.assertTrue(bad.error)


class HTTP(unittest.TestCase):
    def start(self, token=""):
        tmp = tempfile.mkdtemp()
        b = books(tmp)
        cfg = dict(server.DEFAULT_CONFIG, token=token)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(b, server.LPTracker(b, cfg), cfg))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        return "http://127.0.0.1:%d" % httpd.server_address[1]

    def req(self, url, body=None, ctype="application/json", headers=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(url, data=data, headers={"Content-Type": ctype, **(headers or {})} if data else (headers or {}))
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_round_trip(self):
        u = self.start()
        self.assertEqual(self.req(u + "/")[0], 200)
        code, body = self.req(u + "/api/accounts", {"name": "BCA", "kind": "bank", "sleeve": 0})
        self.assertEqual(code, 200)
        self.assertEqual(self.req(u + "/api/balances", {"account": json.loads(body)["id"], "amount": 5_000_000})[0], 200)
        self.assertEqual(self.req(u + "/api/flows", {"kind": "expense", "amount": 25000, "category": "kopi"})[0], 200)
        self.assertEqual(self.req(u + "/api/recs", {"title": "ide gue"})[0], 200)
        self.assertEqual(self.req(u + "/api/recs/note", {"id": 1, "body": "kenapa?"})[0], 200)
        s = json.loads(self.req(u + "/api/summary")[1])
        self.assertEqual((s["net_worth_idr"], s["month"]["spent"], s["recs"]["open"]), (5_000_000, 25000, 1))
        recs = json.loads(self.req(u + "/api/recs")[1])["recs"]
        self.assertEqual(recs[0]["author"], "me")
        code, body = self.req(u + "/api/balances", {"account": "nope", "amount": 1})
        self.assertEqual((code, json.loads(body)["error"]), (400, "no account 'nope'"))
        self.assertEqual(self.req(u + "/api/flows", {"amount": 1}, ctype="text/plain")[0], 415)
        self.assertEqual(self.req(u + "/../server.py")[0], 404)
        self.assertEqual(json.loads(self.req(u + "/api/lp")[1]), {"enabled": False})

    def test_token(self):
        u = self.start("s3cret")
        self.assertEqual(self.req(u + "/api/summary")[0], 401)
        self.assertEqual(self.req(u + "/api/flows", {"amount": 1, "category": "x"})[0], 401)
        self.assertEqual(self.req(u + "/api/summary?t=s3cret")[0], 200)
        self.assertEqual(self.req(u + "/api/summary", headers={"Cookie": "fund_token=s3cret"})[0], 200)


class CLI(unittest.TestCase):
    def test_report_and_recs(self):
        tmp = tempfile.mkdtemp()
        cfgp = Path(tmp) / "config.json"
        cfgp.write_text(json.dumps({"db": str(Path(tmp) / "f.db")}))
        old = server.CONFIG_PATH
        server.load_config.__defaults__ = (cfgp,)
        self.addCleanup(lambda: setattr(server.load_config, "__defaults__", (old,)))

        def run(*a):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = fund.main(list(a))
            return code, buf.getvalue()

        self.assertEqual(run("account", "BCA", "--kind", "bank", "--sleeve", "0")[0], 0)
        self.assertEqual(run("balance", "BCA", "1000000")[0], 0)
        self.assertEqual(run("spend", "20000", "kopi")[0], 0)
        self.assertEqual(run("rec", "add", "Pindah cash ke RDPU", "--expected", "50000")[0], 0)
        code, out = run("report")
        r = json.loads(out)
        self.assertEqual((r["net_worth_idr"], r["month"]["spent"], len(r["recs_open"]), r["lp"]), (1_000_000, 20000, 1, {"enabled": False}))
        self.assertEqual(run("rec", "note", "1", "Biaya transfer?", "--stance", "question", "--by", "Sable")[0], 0)
        self.assertEqual(json.loads(run("report")[1])["recs_open"][0]["notes"][0]["author"], "Sable")
        self.assertEqual(run("rec", "set", "1", "--status", "done", "--actual", "45000")[0], 0)
        self.assertEqual(json.loads(run("report")[1])["recs"]["actual_idr_month_done"], 45000)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run("balance", "nope", "1")[0], 2)


if __name__ == "__main__":
    unittest.main()
