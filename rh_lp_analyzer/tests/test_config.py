"""Settings: python3 -m unittest discover -s rh_lp_analyzer/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402


class KeysTest(unittest.TestCase):
    def test_alchemy_key_from_whatever_is_pasted(self):
        self.assertEqual(config.alchemy_key("  AbCdEf0123456789xyz_- "), "AbCdEf0123456789xyz_-")
        self.assertEqual(config.alchemy_key("https://robinhood-mainnet.g.alchemy.com/v2/AbCdEf0123456789"),
                         "AbCdEf0123456789")
        self.assertIsNone(config.alchemy_key("not a key!"))
        self.assertIsNone(config.alchemy_key(""))

    def test_byo_mode(self):
        self.assertTrue(config.byo_keys({"BYO_KEYS": "1"}))
        self.assertFalse(config.byo_keys({"BYO_KEYS": "0"}))
        self.assertEqual(config.byo_keys({"BYO_KEYS": "auto"}), Path("/mount/src").exists())


if __name__ == "__main__":
    unittest.main()
