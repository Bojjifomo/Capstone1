import os
import tempfile
import unittest

from vault_tracker import config
from vault_tracker.web import Controller, validate

VAULT = "5ZtJuVr6Ls8EkAEW5YyFoEuPyG2LbjgeUjj14A3V1gsQ"


class EnvFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, ".env")
        with open(self.path, "w") as f:
            f.write("# my notes\nVAULT_ADDRESS=old\nOTHER=keep\n")

    def tearDown(self):
        for k in ("VAULT_ADDRESS", "TELEGRAM_BOT_TOKEN", "COPY_MODE"):
            os.environ.pop(k, None)
        self.dir.cleanup()

    def test_save_keeps_other_lines(self):
        config.save({"VAULT_ADDRESS": VAULT, "COPY_MODE": "pct:-5,5"}, self.path)
        text = open(self.path).read()
        self.assertIn("# my notes\n", text)
        self.assertIn("OTHER=keep\n", text)
        self.assertIn(f"VAULT_ADDRESS={VAULT}\n", text)
        self.assertEqual(config.load(self.path)["COPY_MODE"], "pct:-5,5")

    def test_newlines_cannot_inject_keys(self):
        config.save({"TRACKER_NAME": "x\nTELEGRAM_BOT_TOKEN=evil"}, self.path)
        self.assertEqual(config.load(self.path)["TELEGRAM_BOT_TOKEN"], "")

    def test_masked_secret_is_not_overwritten(self):
        ctrl = Controller(self.path, os.path.join(self.dir.name, "state"))
        ctrl.save_config({"VAULT_ADDRESS": VAULT, "TELEGRAM_BOT_TOKEN": "123:real"})
        public = ctrl.public_config()
        self.assertEqual(public["TELEGRAM_BOT_TOKEN"], "tersimpan")
        # the page sends the box back empty (or with the mask): the real token stays
        ctrl.save_config({"VAULT_ADDRESS": VAULT, "TELEGRAM_BOT_TOKEN": ""})
        ctrl.save_config({"VAULT_ADDRESS": VAULT, "TELEGRAM_BOT_TOKEN": "tersimpan"})
        self.assertEqual(config.load(self.path)["TELEGRAM_BOT_TOKEN"], "123:real")

    def test_rejects_invalid_settings(self):
        ctrl = Controller(self.path, os.path.join(self.dir.name, "state"))
        r = ctrl.save_config({"VAULT_ADDRESS": "0xmegumii-9"})
        self.assertFalse(r["ok"])
        self.assertIn("VAULT_ADDRESS", r["errors"])
        self.assertEqual(config.load(self.path)["VAULT_ADDRESS"], "old")


class Validate(unittest.TestCase):
    def test_rules(self):
        ok = {"VAULT_ADDRESS": VAULT, "COPY_MODE": "mirror", "POLL_INTERVAL": "30", "HISTORY_LIMIT": "300",
              "MIN_ALERT_USD": "5", "ALERT_TYPES": "OPEN,CLOSE"}
        self.assertEqual(validate(ok), {})
        bad = dict(ok, COPY_MODE="pct:5,-5", POLL_INTERVAL="1", ALERT_TYPES="OPEN,NOPE", FOLLOWER_ADDRESS="abc")
        self.assertEqual(set(validate(bad)), {"COPY_MODE", "POLL_INTERVAL", "ALERT_TYPES", "FOLLOWER_ADDRESS"})


if __name__ == "__main__":
    unittest.main()
