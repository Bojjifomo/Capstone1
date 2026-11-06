import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dlmm_terminal import settings


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "settings.json"
        self.p = mock.patch.object(settings, "PATH", self.tmp)
        self.p.start()
        self.base = {"position": {"defaultSizeSol": 8, "ladderSlices": 5}, "flags": {"nightEntryLocalHours": [0, 9]},
                     "gates": {"maxPositionPctOfTvl": None}}

    def tearDown(self):
        self.p.stop()

    def test_save_apply_roundtrip(self):
        ov = settings.save(self.base, {"position.ladderSlices": "3", "flags.nightEntryLocalHours.1": "7",
                                       "gates.maxPositionPctOfTvl": "", "position.defaultSizeSol": "8"})
        self.assertEqual(ov, {"position.ladderSlices": 3, "flags.nightEntryLocalHours.1": 7})   # unchanged values are not stored
        cfg = settings.apply(json.loads(json.dumps(self.base)))
        self.assertEqual(cfg["position"]["ladderSlices"], 3)
        self.assertEqual(cfg["flags"]["nightEntryLocalHours"], [0, 7])
        self.assertEqual(cfg["trade"]["maxLossPct"], 5)                                          # default for a key config.json lacks

    def test_rejects(self):
        with self.assertRaises(ValueError):
            settings.save(self.base, {"position.ladderSlices": "99"})
        with self.assertRaises(ValueError):
            settings.save(self.base, {"position.ladderSlices": "abc"})
        with self.assertRaises(ValueError):
            settings.save(self.base, {"exits.mode": "auto"})                                     # not in the schema

    def test_reset(self):
        settings.save(self.base, {"position.ladderSlices": "3"})
        settings.reset()
        self.assertEqual(settings.load(), {})


if __name__ == "__main__":
    unittest.main()
