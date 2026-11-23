import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import demo  # noqa: E402
import intervals  # noqa: E402
import server  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402


def raw(i, day, km=10.0, pace=330, **kw):
    a = {"id": i, "start_date_local": day + "T05:30:00", "type": "Run", "name": "Run",
         "distance": km * 1000, "moving_time": km * pace, "elapsed_time": km * pace,
         "total_elevation_gain": 20, "source": "GARMIN_CONNECT", "device_name": "Garmin Forerunner 265"}
    a.update(kw)
    return a


class RulesTest(unittest.TestCase):
    def test_normal_run_counts(self):
        r = intervals.normalize(raw("a1", "2026-11-02"))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["start_local"], "2026-11-02T05:30:00")

    def test_rides_are_ignored(self):
        self.assertIsNone(intervals.normalize(raw("a1", "2026-11-02", type="Ride")))

    def test_too_fast_is_flagged(self):
        r = intervals.normalize(raw("a1", "2026-11-02", km=21, pace=140))
        self.assertEqual(r["status"], "flagged")
        self.assertIn("2:20", r["reason"])

    def test_manual_is_flagged(self):
        self.assertEqual(intervals.normalize(raw("a1", "2026-11-02", source="MANUAL"))["status"], "flagged")

    def test_short_is_skipped(self):
        self.assertEqual(intervals.normalize(raw("a1", "2026-11-02", km=0.3))["status"], "skipped")

    def test_strava_stub_is_skipped_with_a_hint(self):
        r = intervals.normalize({"id": "s1", "start_date_local": "2026-11-02T06:00:00", "type": "Run",
                                 "source": "STRAVA"})
        self.assertEqual(r["status"], "skipped")
        self.assertIn("Strava", r["reason"])

    def test_pkce(self):
        v, c = intervals.pkce_pair()
        self.assertGreater(len(v), 43)
        self.assertNotIn("=", c)
        url = intervals.authorize_url("42", "https://x/auth/callback", "st", c)
        self.assertIn("scope=ACTIVITY%3AREAD", url)
        self.assertIn("code_challenge_method=S256", url)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.club = store.Club(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_upsert_keeps_profile(self):
        mid = self.club.upsert_member("i1", "Rina", "tok")
        self.club.update_profile(mid, {"name": "Rina K", "gender": "F"})
        self.assertEqual(self.club.upsert_member("i1", "Other name", "tok2"), mid)
        m = self.club.member(mid)
        self.assertEqual((m["name"], m["gender"], m["token"]), ("Rina K", "F", "tok2"))

    def test_bad_gender(self):
        mid = self.club.upsert_member("i1", "Rina", "tok")
        with self.assertRaises(store.Invalid):
            self.club.update_profile(mid, {"gender": "X"})

    def test_review_survives_resync(self):
        mid = self.club.upsert_member("i1", "Rina", "tok")
        row = intervals.normalize(raw("a1", "2026-11-02", km=21, pace=140))
        self.club.save_activities(mid, [row])
        self.club.review("a1", "approved")
        self.club.save_activities(mid, [row])          # sync sees it again
        self.assertEqual(self.club.activities(mid)[0]["status"], "approved")

    def test_deleted_on_intervals_disappears(self):
        mid = self.club.upsert_member("i1", "Rina", "tok")
        self.club.save_activities(mid, [intervals.normalize(raw("a1", "2026-11-02")),
                                        intervals.normalize(raw("a2", "2026-11-03"))])
        self.assertEqual(self.club.forget_missing(mid, "2026-11-01", "2026-11-30", {"a2"}), 1)
        self.assertEqual([a["id"] for a in self.club.activities(mid)], ["a2"])

    def test_sessions_and_state(self):
        mid = self.club.upsert_member("i1", "Rina", "tok")
        tok = self.club.new_session(mid)
        self.assertEqual(self.club.session_member(tok)["id"], mid)
        self.assertIsNone(self.club.session_member("nope"))
        st = self.club.new_state("verifier")
        self.assertEqual(self.club.take_state(st), "verifier")
        self.assertIsNone(self.club.take_state(st))   # one use

    def test_delete_member_cascades(self):
        mid = self.club.upsert_member("i1", "Rina", "tok")
        self.club.save_activities(mid, [intervals.normalize(raw("a1", "2026-11-02"))])
        self.club.delete_member(mid)
        self.assertEqual(self.club.activities(), [])


class StatsTest(unittest.TestCase):
    REF = date(2026, 11, 23)  # a Monday

    def acts(self, mid, days_km):
        return [dict(intervals.normalize(raw("%d-%s" % (mid, d), d, km)), member_id=mid, day=d)
                for d, km in days_km]

    def test_periods(self):
        self.assertEqual(stats.period_range("week", self.REF)[:2], ("2026-11-23", "2026-11-30"))
        self.assertEqual(stats.period_range("month", self.REF)[:2], ("2026-11-01", "2026-12-01"))
        self.assertEqual(stats.period_range("month", date(2026, 12, 5))[:2], ("2026-12-01", "2027-01-01"))

    def test_leaderboard_order_filters_and_idle(self):
        members = [{"id": 1, "name": "A", "gender": "F", "public": 1},
                   {"id": 2, "name": "B", "gender": "M", "public": 1},
                   {"id": 3, "name": "C", "gender": "M", "public": 1},
                   {"id": 4, "name": "Hidden", "gender": "M", "public": 0}]
        acts = (self.acts(1, [("2026-11-02", 10), ("2026-11-03", 10)]) +
                self.acts(2, [("2026-11-05", 25)]) + self.acts(3, [("2026-10-05", 50)]) +
                self.acts(4, [("2026-11-05", 99)]))
        b = stats.leaderboard(members, acts, "month", ref=self.REF)
        self.assertEqual([r["name"] for r in b["rows"]], ["B", "A"])
        self.assertEqual(b["idle"], 1)                       # C ran only in October
        b = stats.leaderboard(members, acts, "month", gender="F", ref=self.REF)
        self.assertEqual([r["name"] for r in b["rows"]], ["A"])
        b = stats.leaderboard(members, acts, "month", metric="days", ref=self.REF)
        self.assertEqual(b["rows"][0]["name"], "A")          # 2 days beats 1 long run

    def test_xp_rewards_consistency(self):
        one_long = self.acts(1, [("2026-11-02", 30)])
        three_short = self.acts(1, [("2026-11-02", 10), ("2026-11-04", 10), ("2026-11-06", 10)])
        self.assertGreater(stats.xp(three_short)["xp"], stats.xp(one_long)["xp"])

    def test_dashboard_load_and_streak(self):
        acts = self.acts(1, [("2026-11-%02d" % d, 8) for d in range(1, 23, 2)] + [("2026-11-23", 30)])
        d = stats.dashboard({"id": 1, "public": 1}, acts, acts, "month", ref=self.REF)
        self.assertEqual(d["streak"], 5)   # 1 Nov is a Sunday: weeks of 26 Oct … 23 Nov
        self.assertEqual(d["load"]["state"], "high")
        self.assertEqual(d["rank"], {"rank": 1, "of": 1})
        self.assertEqual(d["longest"]["km"], 30)
        self.assertEqual(len(d["weeks"]), 16)


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.club = store.Club(Path(cls.tmp.name) / "h.db")
        demo.seed(cls.club, days=200)
        cfg = dict(server.DEFAULT_CONFIG, demo=True, port=0)
        syncer = server.Syncer(cls.club, cfg)
        syncer.sync_member = lambda m, full=False: {}     # tests never call intervals.icu
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.club, syncer, cfg))
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def get(self, path, cookie=None):
        req = urllib.request.Request(self.base + path, headers={"Cookie": cookie} if cookie else {})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def login(self, member=1):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a):
                return None
        op = urllib.request.build_opener(NoRedirect)
        try:
            op.open(self.base + "/auth/demo?member=%d" % member)
        except urllib.error.HTTPError as e:
            return e.headers["Set-Cookie"].split(";")[0]

    def test_public_leaderboard(self):
        b = self.get("/api/leaderboard?period=month")
        self.assertTrue(b["rows"])
        self.assertEqual(b["rows"][0]["rank"], 1)

    def test_dashboard_needs_login(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/api/dashboard")
        self.assertEqual(e.exception.code, 401)

    def test_dashboard_and_admin(self):
        c = self.login(1)
        d = self.get("/api/dashboard", c)
        self.assertTrue(d["member"]["own"])
        a = self.get("/api/admin", c)
        self.assertTrue(a["flagged"])

    def test_non_admin_blocked(self):
        c = self.login(2)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/api/admin", c)
        self.assertEqual(e.exception.code, 403)

    def test_post_needs_json(self):
        c = self.login(2)
        req = urllib.request.Request(self.base + "/api/profile", data=b"name=x", method="POST",
                                     headers={"Cookie": c, "Content-Type": "application/x-www-form-urlencoded"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 415)

    def callback_location(self, exc=None, answer=None):
        st = self.club.new_state("v")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a):
                return None
        kw = {"side_effect": exc} if exc else {"return_value": answer}
        with mock.patch.object(intervals, "exchange_code", **kw) as ex:
            try:
                urllib.request.build_opener(NoRedirect).open(self.base + "/auth/callback?code=c&state=" + st)
            except urllib.error.HTTPError as e:
                return e.headers["Location"], ex

    def test_callback_failure_reports_why(self):
        loc, ex = self.callback_location(exc=intervals.ApiError("boom", "http-400"))
        self.assertEqual(loc, "/#/?login=failed&why=http-400")
        self.assertTrue(ex.call_args.args[4].endswith("/auth/callback"))   # redirect_uri sent
        loc, _ = self.callback_location(exc=intervals.ApiError("Client and/or secret not found", "http-404"))
        self.assertEqual(loc, "/#/?login=failed&why=secret")
        loc, _ = self.callback_location(exc=intervals.AuthError("no"))
        self.assertIn("why=", loc)
        loc, _ = self.callback_location(answer={"access_token": "t"})
        self.assertEqual(loc, "/#/?login=failed&why=no-athlete")

    def test_callback_success_logs_in(self):
        loc, _ = self.callback_location(answer={"access_token": "t", "athlete": {"id": "i777", "name": "Baru"}})
        self.assertEqual(loc, "/#/me?welcome=1")
        self.assertTrue(any(m["athlete_id"] == "i777" for m in self.club.members()))

    def test_privacy_page(self):
        with urllib.request.urlopen(self.base + "/privacy") as r:
            self.assertIn(b"ACTIVITY:READ", r.read())

    def test_static_no_traversal(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/../server.py")
        self.assertEqual(e.exception.code, 404)


class ConfigTest(unittest.TestCase):
    def test_id_and_secret_are_trimmed(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "c.json"
            p.write_text(json.dumps({"intervals_client_id": 1999, "intervals_client_secret": " abc \n"}))
            with mock.patch.dict("os.environ"):
                os.environ.pop("HG_INTERVALS_SECRET", None)
                cfg = server.load_config(p)
            self.assertEqual((cfg["intervals_client_id"], cfg["intervals_client_secret"]), ("1999", "abc"))


class SyncTest(unittest.TestCase):
    def test_sync_saves_and_drops_revoked_token(self):
        with tempfile.TemporaryDirectory() as t:
            club = store.Club(Path(t) / "s.db")
            mid = club.upsert_member("i1", "Rina", "tok")
            syncer = server.Syncer(club, dict(server.DEFAULT_CONFIG))
            with mock.patch.object(intervals, "list_activities", return_value=[raw("a1", "2026-11-02")]):
                out = syncer.sync_member(club.member(mid))
            self.assertEqual(out["new"], 1)
            with mock.patch.object(intervals, "list_activities", side_effect=intervals.AuthError("x")):
                syncer.sync_member(club.member(mid))
            m = club.member(mid)
            self.assertIsNone(m["token"])
            self.assertEqual(len(club.activities(mid)), 1)   # history kept


if __name__ == "__main__":
    unittest.main()
