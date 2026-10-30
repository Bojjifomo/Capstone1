"""LP Call Bot: owner ketik call di DM bot, bot nge-blast ke channel + semua subscriber.

Jalanin: python3 lp_call_bot/bot.py   (config di lp_call_bot/.env, lihat .env.example)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import calls  # noqa: E402
from store import Store  # noqa: E402

HERE = Path(__file__).resolve().parent

HELP_OWNER = """<b>Format call</b>

Open LP:
<code>0x… hydx 2.5% -65</code>
<code>0x… dark 3% -60 -65</code>   (range kosong = default {default_range}%)

Close:
<code>close dark</code>
<code>close worm boomer</code>   /   <code>close all</code>

Reentry (range &amp; fee sama kayak open terakhir):
<code>reentry hydx vrax</code>

Command:
/posisi: LP yang lagi kebuka
/stats: jumlah penerima
/blast &lt;teks&gt;: kirim pesan bebas
/hapus &lt;koin&gt;: hapus koin dari daftar (salah ketik)"""

HELP_FOLLOWER = """Halo! Lu bakal dapet notif tiap ada LP dibuka, ditutup, atau reentry.

/posisi: LP yang lagi kebuka
/stop: berhenti langganan"""


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def id_list(value):
    return [int(v) if v.strip().lstrip("-").isdigit() else v.strip() for v in value.split(",") if v.strip()]


class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method, http_timeout=40, **params):
        req = urllib.request.Request(
            self.base + method,
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            return json.load(e)
        except OSError as e:  # internet putus / timeout: coba lagi nanti
            return {"ok": False, "description": repr(e)}

    def send(self, chat_id, text):
        """Kirim HTML, retry kalau kena rate limit. Return (ok, error_code)."""
        for _ in range(3):
            res = self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                            disable_web_page_preview=True)
            if res.get("ok"):
                return True, None
            retry = res.get("parameters", {}).get("retry_after")
            if res.get("error_code") == 429 and retry:
                time.sleep(retry + 1)
                continue
            return False, res.get("error_code")
        return False, 429


class Bot:
    def __init__(self, tg, store, owners, channels, default_range="-65", heartbeat=None):
        self.tg = tg
        self.store = store
        self.owners = set(owners)
        self.channels = channels
        self.default_range = default_range
        self.heartbeat = Path(heartbeat) if heartbeat else None

    # kirim
    def reply(self, chat_id, text):
        self.tg.send(chat_id, text)

    def blast(self, text):
        sent = failed = 0
        for chat_id in self.channels:
            ok, _ = self.tg.send(chat_id, text)
            sent, failed = sent + ok, failed + (not ok)
        for chat_id in self.store.subscribers():
            ok, code = self.tg.send(chat_id, text)
            sent, failed = sent + ok, failed + (not ok)
            if code in (400, 403):  # user blokir bot / hapus akun
                self.store.unsubscribe(chat_id)
            time.sleep(0.05)  # di bawah limit ~30 pesan/detik
        return sent, failed

    def blast_and_report(self, owner_chat, text):
        sent, failed = self.blast(text)
        note = f"✅ Terkirim ke {sent} penerima" + (f", {failed} gagal" if failed else "")
        self.reply(owner_chat, note)

    # terima
    def handle(self, msg):
        chat_id = msg["chat"]["id"]
        user_id = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()
        if not text or msg["chat"].get("type") != "private":
            return
        is_owner = user_id in self.owners
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()

        if cmd == "/start":
            if is_owner:
                self.reply(chat_id, HELP_OWNER.format(default_range=self.default_range))
            else:
                self.store.subscribe(chat_id)
                self.reply(chat_id, HELP_FOLLOWER)
        elif cmd == "/stop":
            self.store.unsubscribe(chat_id)
            self.reply(chat_id, "Oke, udah berhenti. Ketik /start buat langganan lagi.")
        elif cmd == "/posisi":
            self.reply(chat_id, calls.render_list(self.store.positions()))
        elif cmd == "/id":
            self.reply(chat_id, f"User ID lu: <code>{user_id}</code>")
        elif not is_owner:
            self.reply(chat_id, HELP_FOLLOWER)
        elif cmd == "/help":
            self.reply(chat_id, HELP_OWNER.format(default_range=self.default_range))
        elif cmd == "/stats":
            self.reply(chat_id, f"Subscriber: {len(self.store.subscribers())}\nChannel: {len(self.channels)}")
        elif cmd == "/blast":
            if arg.strip():
                self.blast_and_report(chat_id, arg.strip())
            else:
                self.reply(chat_id, "Isi pesannya: /blast &lt;teks&gt;")
        elif cmd == "/hapus":
            removed = self.store.delete(arg.strip()) if arg.strip() else None
            self.reply(chat_id, f"🗑 {arg.strip()} dihapus." if removed else "Koin itu nggak ada di daftar.")
        else:
            self.handle_call(chat_id, text)

    def handle_call(self, chat_id, text):
        try:
            call = calls.parse(text, self.default_range)
        except calls.ParseError as e:
            self.reply(chat_id, f"⚠️ {e}")
            return
        if call is None:
            self.reply(chat_id, "Nggak kebaca sebagai call. Ketik /help buat formatnya.")
            return

        if call["action"] == "open":
            pos = self.store.open_position(call)
            self.blast_and_report(chat_id, calls.render_open(pos))
            return

        if call["action"] == "close" and call["coins"] == ["all"]:
            coins = [p["coin"] for p in self.store.positions() if p["status"] == "open"]
            if not coins:
                self.reply(chat_id, "Nggak ada LP yang kebuka.")
                return
        else:
            coins = call["coins"]

        known = [c for c in coins if self.store.get(c)]
        unknown = [c for c in coins if not self.store.get(c)]
        if unknown:
            self.reply(chat_id, "⚠️ Nggak ada di daftar, dilewatin: " + ", ".join(unknown)
                       + "\nOpen dulu pakai format <code>0x… koin fee% range</code>.")
        if not known:
            return

        if call["action"] == "close":
            positions = [self.store.set_status(c, "closed") for c in known]
            self.blast_and_report(chat_id, calls.render_close(positions))
        else:
            positions = [self.store.set_status(c, "open") for c in known]
            self.blast_and_report(chat_id, calls.render_reentry(positions))

    def beat(self):
        """Sentuh file heartbeat tiap kali Telegram berhasil dihubungi. Agent Office ngecek umurnya."""
        if not self.heartbeat:
            return
        try:
            self.heartbeat.touch()
        except OSError as e:
            print("heartbeat gagal:", repr(e), flush=True)

    def poll_once(self):
        res = self.tg.call("getUpdates", http_timeout=60, timeout=50, offset=self.store.offset,
                           allowed_updates=["message"])
        if not res.get("ok"):
            print("getUpdates gagal:", res, flush=True)
            time.sleep(5)
            return False
        self.beat()
        for update in res["result"]:
            self.store.offset = update["update_id"] + 1
            if "message" in update:
                try:
                    self.handle(update["message"])
                except Exception as e:  # satu pesan error jangan bikin bot mati
                    print("error:", repr(e), flush=True)
        return True

    def run(self):
        print(f"LP Call Bot jalan. Owner: {sorted(self.owners)}  Channel: {self.channels}", flush=True)
        while True:
            self.poll_once()


def main():
    load_env(HERE / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN belum diisi (copy .env.example ke .env).")
    owners = id_list(os.environ.get("OWNER_IDS", ""))
    if not owners:
        print("⚠️ OWNER_IDS kosong: kirim /id ke bot buat liat user ID lu, terus isi di .env.")
    bot = Bot(
        Telegram(token),
        Store(os.environ.get("STATE_FILE", HERE / "state.json")),
        owners,
        id_list(os.environ.get("CHANNEL_IDS", "")),
        os.environ.get("DEFAULT_RANGE", "-65"),
        heartbeat=os.environ.get("HEARTBEAT_FILE", HERE / "heartbeat"),
    )
    bot.run()


if __name__ == "__main__":
    main()
