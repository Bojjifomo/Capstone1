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
        with open(self.path) as f:
            text = f.read()
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


class RpcUrl(unittest.TestCase):
    def test_normalize(self):
        from vault_tracker.rpc import normalize_rpc_url
        key = "2b0485ff-b406-4b0e-aaf8-557f18a07173"
        self.assertEqual(normalize_rpc_url(key), f"https://mainnet.helius-rpc.com/?api-key={key}")
        self.assertEqual(normalize_rpc_url(" https://x.io/?k=1 "), "https://x.io/?k=1")
        self.assertEqual(normalize_rpc_url(""), "")
        self.assertEqual(normalize_rpc_url("not a url"), "not a url")

    def test_invalid_rejected_and_key_redacted(self):
        from vault_tracker.rpc import Rpc, RpcError
        with self.assertRaises(RpcError):
            Rpc("not a url")
        r = Rpc("https://mainnet.helius-rpc.com/?api-key=SECRET123")
        self.assertNotIn("SECRET123", r._redact("boom at https://mainnet.helius-rpc.com/?api-key=SECRET123"))

    def test_validate_rpc(self):
        base = {"VAULT_ADDRESS": VAULT}
        self.assertNotIn("SOLANA_RPC_URL", validate({**base, "SOLANA_RPC_URL": "2b0485ff-b406-4b0e-aaf8-557f18a07173"}))
        self.assertIn("SOLANA_RPC_URL", validate({**base, "SOLANA_RPC_URL": "abc"}))


class TelegramChatId(unittest.TestCase):
    def test_validate(self):
        base = {"VAULT_ADDRESS": VAULT}
        self.assertIn("TELEGRAM_CHAT_ID", validate({**base, "TELEGRAM_CHAT_ID": "pantauanvaultbot"}))
        for ok in ("123456789", "-1001234567890", "@my_channel"):
            self.assertNotIn("TELEGRAM_CHAT_ID", validate({**base, "TELEGRAM_CHAT_ID": ok}))

    def test_chats_from_updates(self):
        from unittest import mock

        from vault_tracker import alerts
        updates = {"ok": True, "result": [
            {"message": {"chat": {"id": 111, "first_name": "Andi", "username": "andi", "type": "private"}}},
            {"my_chat_member": {"chat": {"id": -100222, "title": "Grup Vault", "type": "supergroup"}}},
            {"message": {"chat": {"id": 111, "first_name": "Andi", "username": "andi", "type": "private"}}},
        ]}
        resp = mock.Mock(ok=True, status_code=200, json=lambda: updates)
        with mock.patch.object(alerts.requests, "get", return_value=resp):
            chats = alerts.telegram_chats("TOKEN")
        self.assertEqual([c["id"] for c in chats], ["111", "-100222"])  # newest first
        self.assertEqual(chats[0]["name"], "Andi (@andi)")
        bad = mock.Mock(ok=False, status_code=401, text='{"description":"Unauthorized"}')
        with mock.patch.object(alerts.requests, "get", return_value=bad):
            with self.assertRaisesRegex(RuntimeError, "token salah"):
                alerts.telegram_chats("TOKEN")


class ResolveVault(unittest.TestCase):
    class FakeRpc:
        def __init__(self, accounts):
            self.accounts = accounts

        def call(self, method, params):
            return {"value": self.accounts.get(params[0])}

    def mint(self, authority):
        return {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                "data": {"program": "spl-token", "parsed": {"type": "mint", "info": {"mintAuthority": authority}}}}

    def test_share_mint_maps_to_vault(self):
        from vault_tracker.vault import HEDGIN_PROGRAM, resolve_vault
        rpc = self.FakeRpc({"MINT": self.mint("VAULT"), "VAULT": {"owner": HEDGIN_PROGRAM, "data": ["", "base64"]}})
        addr, note = resolve_vault(rpc, "MINT")
        self.assertEqual(addr, "VAULT")
        self.assertIn("token share", note)

    def test_wallet_unchanged_and_other_mint_rejected(self):
        from vault_tracker.vault import resolve_vault
        rpc = self.FakeRpc({"W": {"owner": "11111111111111111111111111111111", "data": ["", "base64"]},
                            "USDC": self.mint("SOMEONE"), "SOMEONE": {"owner": "11111111111111111111111111111111"}})
        self.assertEqual(resolve_vault(rpc, "W"), ("W", None))
        self.assertEqual(resolve_vault(rpc, "NEW"), ("NEW", None))  # account not created yet
        with self.assertRaises(ValueError):
            resolve_vault(rpc, "USDC")


if __name__ == "__main__":
    unittest.main()
