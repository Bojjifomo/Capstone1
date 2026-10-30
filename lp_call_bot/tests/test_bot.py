"""Offline tests: python3 -m unittest discover -s lp_call_bot/tests"""
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot as B  # noqa: E402
import calls  # noqa: E402
from store import Store  # noqa: E402

CA = "0xaa5751ff3fb61e613993b2eb4eab1fce017081fc"
OWNER, FAN, CHANNEL = 111, 222, "@lpcall"


class ParseTest(unittest.TestCase):
    def test_open(self):
        call = calls.parse(f"{CA} hydx 2.5% -65")
        self.assertEqual(call, {"action": "open", "ca": CA, "coin": "hydx", "fee": "2.5", "range": ["-65"]})

    def test_open_two_ranges_and_default(self):
        self.assertEqual(calls.parse(f"{CA} dark 3% -60 -65")["range"], ["-60", "-65"])
        self.assertEqual(calls.parse(f"{CA} dark 3% -60/-65%")["range"], ["-60", "-65"])
        self.assertEqual(calls.parse(f"{CA} rebate 2%", default_range="-60")["range"], ["-60"])

    def test_open_errors(self):
        with self.assertRaises(calls.ParseError):
            calls.parse(f"{CA} 2% -65")
        with self.assertRaises(calls.ParseError):
            calls.parse(f"{CA} hydx -65")

    def test_close_reentry(self):
        self.assertEqual(calls.parse("Close worm boomer"), {"action": "close", "coins": ["worm", "boomer"]})
        self.assertEqual(calls.parse("reenter prior"), {"action": "reentry", "coins": ["prior"]})
        self.assertEqual(calls.parse("Reentry hydx vrax")["coins"], ["hydx", "vrax"])
        self.assertIsNone(calls.parse("gm"))


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.blocked = set()

    def send(self, chat_id, text):
        if chat_id in self.blocked:
            return False, 403
        self.sent.append((chat_id, text))
        return True, None

    def to(self, chat_id):
        return [t for c, t in self.sent if c == chat_id]


def msg(user, text):
    return {"chat": {"id": user, "type": "private"}, "from": {"id": user}, "text": text}


class BotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tg = FakeTelegram()
        self.store = Store(Path(self.tmp.name) / "state.json")
        self.bot = B.Bot(self.tg, self.store, [OWNER], [CHANNEL])
        patcher = mock.patch.object(B.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bot.handle(msg(FAN, "/start"))
        self.tg.sent.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_open_close_reentry_flow(self):
        self.bot.handle(msg(OWNER, f"{CA} hydx 2.5% -65"))
        self.assertIn("OPEN LP — HYDX", self.tg.to(FAN)[0])
        self.assertIn(f"<code>{CA}</code>", self.tg.to(CHANNEL)[0])
        self.assertIn("Terkirim ke 2", self.tg.to(OWNER)[-1])

        self.bot.handle(msg(OWNER, "close hydx"))
        self.assertIn("CLOSE LP — HYDX", self.tg.to(FAN)[-1])
        self.assertEqual(self.store.get("hydx")["status"], "closed")

        self.bot.handle(msg(OWNER, "Reentry hydx"))
        text = self.tg.to(FAN)[-1]
        self.assertIn("REENTRY — HYDX", text)
        self.assertIn("-65%", text)
        self.assertIn("2.5%", text)
        self.assertEqual(self.store.get("hydx")["status"], "open")

    def test_unknown_coin_not_blasted(self):
        self.bot.handle(msg(OWNER, f"{CA} pangea 3% -65"))
        self.tg.sent.clear()
        self.bot.handle(msg(OWNER, "close pangea kontokl"))
        self.assertEqual(len(self.tg.to(FAN)), 1)
        self.assertNotIn("KONTOKL", self.tg.to(FAN)[0])
        self.assertIn("kontokl", self.tg.to(OWNER)[0])

    def test_close_all(self):
        self.bot.handle(msg(OWNER, f"{CA} worm 4.88% -65"))
        self.bot.handle(msg(OWNER, f"0x127CDc74E44E3c60422b7E792eBE480c0f873ed5 boomer 5% -65"))
        self.bot.handle(msg(OWNER, "close all"))
        text = self.tg.to(FAN)[-1]
        self.assertIn("WORM", text)
        self.assertIn("BOOMER", text)

    def test_follower_cannot_call(self):
        self.bot.handle(msg(FAN, f"{CA} scam 1% -65"))
        self.assertIsNone(self.store.get("scam"))
        self.assertEqual(self.tg.to(CHANNEL), [])

    def test_blocked_subscriber_removed(self):
        self.tg.blocked.add(FAN)
        self.bot.handle(msg(OWNER, f"{CA} hydx 2.5% -65"))
        self.assertNotIn(FAN, self.store.subscribers())
        self.assertIn("1 gagal", self.tg.to(OWNER)[-1])

    def test_state_persists(self):
        self.bot.handle(msg(OWNER, f"{CA} hydx 2.5% -65"))
        again = Store(self.store.path)
        self.assertEqual(again.get("hydx")["ca"], CA)
        self.assertEqual(again.subscribers(), [FAN])


if __name__ == "__main__":
    unittest.main()
