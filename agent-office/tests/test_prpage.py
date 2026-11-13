"""PR page: sealed merge, CI and verdict re-check, local-only guard, request changes. No real gh calls."""
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
import guard  # noqa: E402
import prpage  # noqa: E402
import server  # noqa: E402

REAL_GH_MERGE = prpage.gh_merge  # setUp replaces the module attribute
REPO = "Bojjifomo/Capstone1"
URL = "https://github.com/Bojjifomo/Capstone1/pull/7"
GREEN = [{"name": "test", "bucket": "pass", "state": "SUCCESS"}, {"name": "lint", "bucket": "skipping", "state": "SKIPPED"}]
RED = [{"name": "test", "bucket": "fail", "state": "FAILURE"}]


class FakeChain:
    def __init__(self, outcome="done", by="Orin", url=URL):
        self.lock = threading.Lock()
        self.reviews = [{"url": url, "by": by, "reviewer": "Thorne", "started": time.time(), "ended": time.time(),
                         "outcome": outcome, "note": "looks fine"}] if outcome else []


class FakeTeam:
    def __init__(self):
        self.asked = []

    def ask(self, key, brief, **kw):
        self.asked.append((key, brief))
        return {"name": key, "sid": "s1"}


class FakeLedger:
    def __init__(self):
        self.lines = []

    def add(self, kind, text, **extra):
        self.lines.append((kind, text))


class FakeOffice:
    def __init__(self, chain):
        self.chain = chain
        self.team = FakeTeam()
        self.ledger = FakeLedger()
        self.sent = []
        self.codes = guard.Codes(lambda title, body: self.sent.append(title) or True)


class FakeApp:
    def __init__(self, chain):
        self.office = FakeOffice(chain)


def req(body, external=False, query=None):
    return server.Request(None, query or {}, body, external=external)


class PrPage(unittest.TestCase):
    def setUp(self):
        self.checks = GREEN
        self.info = {"draft": False, "head": "abc", "newest_commit": time.time() - 3600}
        self.merge = mock.Mock(return_value="merged")
        self.comment = mock.Mock()
        for name, fn in (("gh_checks", lambda repo, n: self.checks), ("gh_merge", self.merge), ("gh_pr_info", lambda repo, n: self.info), ("gh_comment", self.comment)):
            p = mock.patch.object(prpage, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def app(self, chain=None):
        app = FakeApp(chain if chain is not None else FakeChain())
        p = mock.patch.object(server, "APP", app)
        p.start()
        self.addCleanup(p.stop)
        return app

    def sealed_body(self, app, code="123456"):
        with mock.patch.object(guard.secrets, "randbelow", return_value=int(code)):
            cid = app.office.codes.request("merge:%s#7" % REPO, "merge")
        return {"repo": REPO, "number": 7, "code_id": cid, "code": code}

    def test_no_seal_is_rejected(self):
        app = self.app()
        with self.assertRaises(core.Refused):
            server.api_prs_merge(req({"repo": REPO, "number": 7}), None)
        self.merge.assert_not_called()

    def test_wrong_seal_is_rejected(self):
        app = self.app()
        body = self.sealed_body(app, "123456")
        body["code"] = "000000"
        with self.assertRaises(core.Refused):
            server.api_prs_merge(req(body), None)
        self.merge.assert_not_called()

    def test_seal_for_another_pr_is_rejected(self):
        app = self.app()
        body = self.sealed_body(app)
        body["number"] = 8
        with self.assertRaises(core.Refused):
            server.api_prs_merge(req(body), None)
        self.merge.assert_not_called()

    def test_red_ci_is_rejected(self):
        app = self.app()
        self.checks = RED
        with self.assertRaises(server.HttpError) as cm:
            server.api_prs_merge(req(self.sealed_body(app)), None)
        self.assertEqual(cm.exception.status, 409)
        self.merge.assert_not_called()

    def test_no_ci_checks_is_rejected(self):
        app = self.app()
        self.checks = []
        with self.assertRaises(server.HttpError):
            server.api_prs_merge(req(self.sealed_body(app)), None)
        self.merge.assert_not_called()

    def test_verdict_not_approve_is_rejected(self):
        for outcome in ("blocked", "needs-you", "failed", None):
            app = self.app(FakeChain(outcome))
            with self.assertRaises(server.HttpError, msg=outcome) as cm:
                server.api_prs_merge(req(self.sealed_body(app)), None)
            self.assertEqual(cm.exception.status, 409)
        self.merge.assert_not_called()

    def test_review_still_running_is_rejected(self):
        chain = FakeChain("done")
        chain.reviews[0]["ended"] = None
        app = self.app(chain)
        with self.assertRaises(server.HttpError):
            server.api_prs_merge(req(self.sealed_body(app)), None)
        self.merge.assert_not_called()

    def test_approve_on_an_old_head_is_rejected(self):
        app = self.app()
        self.info["newest_commit"] = time.time() + 60  # pushed after the review began
        with self.assertRaises(server.HttpError) as cm:
            server.api_prs_merge(req(self.sealed_body(app)), None)
        self.assertEqual(cm.exception.status, 409)
        self.merge.assert_not_called()

    def test_draft_is_rejected(self):
        app = self.app()
        self.info["draft"] = True
        with self.assertRaises(server.HttpError) as cm:
            server.api_prs_merge(req(self.sealed_body(app)), None)
        self.assertEqual(cm.exception.status, 409)
        self.merge.assert_not_called()

    def test_list_shows_stale_and_disables_merge(self):
        listing = [{"number": 7, "title": "t", "url": URL, "repository": {"nameWithOwner": REPO}, "author": {"login": "a"},
                    "createdAt": "2026-01-01T00:00:00Z", "isDraft": False}]
        self.info["newest_commit"] = time.time() + 60
        with mock.patch.object(prpage, "gh_list_open", return_value=listing):
            row = prpage.list_open(FakeChain())[0]
        self.assertEqual((row["verdict"], row["can_merge"]), ("stale", False))

    def test_external_caller_is_rejected(self):
        app = self.app()
        body = self.sealed_body(app)
        with self.assertRaises(server.HttpError) as cm:
            server.api_prs_merge(req(body, external=True), None)
        self.assertEqual(cm.exception.status, 403)
        self.assertFalse(server.token_route_allowed("POST", "/api/prs/merge"))
        self.assertFalse(server.token_route_allowed("POST", "/api/prs/changes"))
        self.assertFalse(server.token_route_allowed("GET", "/api/prs"))
        self.merge.assert_not_called()

    def test_other_owner_is_rejected(self):
        app = self.app()
        with self.assertRaises(server.HttpError) as cm:
            server.api_prs_merge(req({"repo": "someone/else", "number": 7}), None)
        self.assertEqual(cm.exception.status, 400)
        self.merge.assert_not_called()

    def test_happy_path_merges_with_squash_and_delete_branch(self):
        app = self.app()
        out = server.api_prs_merge(req(self.sealed_body(app)), None)
        self.assertEqual(out["merged"], True)
        self.merge.assert_called_once_with(REPO, 7)

    def test_seal_is_single_use(self):
        app = self.app()
        body = self.sealed_body(app)
        server.api_prs_merge(req(body), None)
        with self.assertRaises(core.Refused):
            server.api_prs_merge(req(body), None)
        self.assertEqual(self.merge.call_count, 1)

    def test_gh_merge_uses_squash_and_delete_branch(self):
        with mock.patch.object(prpage, "_gh", return_value=(0, "ok", "")) as gh:
            REAL_GH_MERGE(REPO, 7)
        self.assertEqual(gh.call_args[0][0], ["pr", "merge", "7", "-R", REPO, "--squash", "--delete-branch"])

    def test_request_changes_posts_comment_and_routes_to_author(self):
        app = self.app()
        out = server.api_prs_changes(req({"repo": REPO, "number": 7, "text": "add a test"}), None)
        self.comment.assert_called_once()
        self.assertIn("add a test", self.comment.call_args[0][2])
        self.assertEqual(out["sent_to"], "Orin")
        key, brief = app.office.team.asked[0]
        self.assertEqual(key, "Orin")
        self.assertIn(URL, brief)
        self.assertIn("add a test", brief)

    def test_request_changes_without_known_author_still_comments(self):
        app = self.app(FakeChain(None))
        out = server.api_prs_changes(req({"repo": REPO, "number": 7, "text": "rename it"}), None)
        self.comment.assert_called_once()
        self.assertEqual(out["sent_to"], "")
        self.assertEqual(app.office.team.asked, [])

    def test_request_changes_needs_text_and_is_local_only(self):
        self.app()
        with self.assertRaises(server.HttpError):
            server.api_prs_changes(req({"repo": REPO, "number": 7, "text": "  "}), None)
        with self.assertRaises(server.HttpError) as cm:
            server.api_prs_changes(req({"repo": REPO, "number": 7, "text": "x"}, external=True), None)
        self.assertEqual(cm.exception.status, 403)
        self.comment.assert_not_called()

    def test_list_carries_ci_verdict_and_lp_bot_flag(self):
        listing = [{"number": 7, "title": "t", "url": URL, "repository": {"nameWithOwner": REPO}, "author": {"login": "a"},
                    "createdAt": "2026-01-01T00:00:00Z", "isDraft": False},
                   {"number": 3, "title": "u", "url": "https://github.com/Bojjifomo/lp_call_bot/pull/3",
                    "repository": {"nameWithOwner": "Bojjifomo/lp_call_bot"}, "author": {"login": "a"},
                    "createdAt": "2026-01-01T00:00:00Z", "isDraft": False}]
        with mock.patch.object(prpage, "gh_list_open", return_value=listing):
            rows = prpage.list_open(FakeChain())
        self.assertEqual((rows[0]["ci"], rows[0]["verdict"], rows[0]["can_merge"], rows[0]["lp_bot"]), ("green", "approve", True, False))
        self.assertTrue(rows[1]["lp_bot"])
        self.assertFalse(rows[1]["can_merge"], "no stored review for that PR")
        self.assertTrue(prpage.is_lp_bot("Bojjifomo/LP-Call-Bot"))


if __name__ == "__main__":
    unittest.main()
