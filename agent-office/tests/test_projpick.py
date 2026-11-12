"""Project auto-pick for the New session sheet: Haiku stubbed, keyword fallback, validation, endpoint shape."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import projpick  # noqa: E402
import test_office  # noqa: E402

PROJECTS = [
    {"id": "aaa111", "path": "/Users/me/Projects/webapp", "label": "~/Projects/webapp", "recent": 3},
    {"id": "bbb222", "path": "/Users/me/Projects/lp_call_bot", "label": "~/Projects/lp_call_bot", "recent": 2},
    {"id": "ccc333", "path": "/Users/me/Agents/brynn", "label": "~/Agents/brynn", "recent": 1},
]


def broken(*a, **k):
    raise RuntimeError("haiku is down")


class Pick(unittest.TestCase):
    def pick(self, task, ask, **kw):
        with mock.patch("projpick.haiku.ask", ask):
            return projpick.suggest_project({}, task, PROJECTS, **kw)

    def test_haiku_answer_is_used(self):
        r = self.pick("fix the login page", lambda *a, **k: 'sure {"id": "aaa111"}')
        self.assertEqual(r, {"id": "aaa111", "label": "~/Projects/webapp", "source": "ai"})

    def test_keyword_fallback_picks_by_folder_name(self):
        r = self.pick("the call bot crashes on startup", broken)
        self.assertEqual((r["id"], r["source"]), ("bbb222", "keywords"))

    def test_bad_haiku_id_is_rejected_then_keywords_decide(self):
        r = self.pick("add a filter to the webapp table", lambda *a, **k: '{"id": "zzz999"}')
        self.assertEqual((r["id"], r["source"]), ("aaa111", "keywords"))

    def test_nothing_matches_gives_null(self):
        r = self.pick("water the plants tomorrow", lambda *a, **k: '{"id": null}')
        self.assertIsNone(r["id"])
        self.assertIsNone(self.pick("", broken)["id"])

    def test_hints_from_interns_and_teammates(self):
        interns = [{"name": "Ledger", "project": "/Users/me/Projects/webapp/api"}]
        staff = [{"name": "Brynn", "folder": "/Users/me/Agents/brynn"}]
        self.assertEqual(self.pick("check the ledger numbers", broken, interns=interns)["id"], "aaa111")
        self.assertEqual(self.pick("ask brynn for help", broken, staff=staff)["id"], "ccc333")


class Endpoint(test_office.ServerCase):
    def test_shape(self):
        with mock.patch("projpick.haiku.ask", broken):
            status, r = self.post("/api/project/suggest", {"text": "fix something in the webapp"})
            self.assertEqual(status, 200, r)
            self.assertEqual(set(r), {"id", "label", "source"})
            self.assertEqual(r["source"], "keywords")
            self.assertTrue(r["label"].endswith("webapp"))
            ids = [p["id"] for p in self.request("GET", "/api/projects")[1]["projects"]]
            self.assertIn(r["id"], ids)
            none = self.post("/api/project/suggest", {"text": "qqq zzz"})[1]
            self.assertIsNone(none["id"])


if __name__ == "__main__":
    unittest.main()
