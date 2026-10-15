"""Control the copytrade bot from Telegram, e.g. an emergency exit while away from the computer.

Only messages from the configured private chat are obeyed; commands sent while the tracker was not
running (older than COMMAND_MAX_AGE) are ignored, so nothing stale fires on start.
"""

import threading
import time

import requests

from .alerts import fmt_usd
from .copier import Skip

COMMAND_MAX_AGE = 120
HELP = """Perintah bot copytrade:
/status – mode, saldo, posisi bot
/posisi – daftar posisi bot (bernomor)
/tarik N P – tarik P% likuiditas posisi N (posisi tetap terbuka)
/tambah N USD – tambah USD ke posisi N
/tutup N – tutup posisi N
/darurat YA – TUTUP SEMUA posisi bot dan jeda bot
/jeda – jangan buka/tambah posisi baru
/lanjut – lanjutkan bot"""


def _text(note):
    title, lines = note
    return "\n".join([title, *lines])


class TelegramControl:
    def __init__(self, copier, token, chat_id):
        self.copier, self.token, self.chat = copier, token, str(chat_id).strip()
        self.offset = None
        self.stop_evt = threading.Event()
        self.thread = None
        self.error = None

    def _api(self, method, http_timeout=15, **params):
        params = {k: v for k, v in params.items() if v is not None}
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", json=params, timeout=http_timeout)
        data = r.json() if r.content else {}
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method} {r.status_code}: {str(data.get('description'))[:120]}")
        return data["result"]

    def reply(self, text):
        for i in range(0, len(text), 4000):
            self._api("sendMessage", chat_id=self.chat, text=text[i:i + 4000], disable_web_page_preview=True)

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="telegram-control")
        self.thread.start()

    def stop(self):
        self.stop_evt.set()

    def run(self):
        try:  # skip whatever was sent before we started
            last = self._api("getUpdates", offset=-1, timeout=0)
            self.offset = last[-1]["update_id"] + 1 if last else None
        except Exception as e:
            self.error = str(e).replace(self.token, "***")
        while not self.stop_evt.is_set():
            try:  # long poll: Telegram holds the request up to 25 s until a message arrives
                updates = self._api("getUpdates", http_timeout=40, offset=self.offset, timeout=25,
                                    allowed_updates=["message"])
            except Exception as e:
                self.error = str(e).replace(self.token, "***")
                self.stop_evt.wait(15)
                continue
            self.error = None
            if self.stop_evt.is_set():
                break  # tracker restarted: the new session's poller picks these up (offset not confirmed)
            for upd in updates:
                self.offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                reply = self.on_message(msg)
                if reply:
                    try:
                        self.reply(reply)
                    except Exception as e:
                        self.error = str(e).replace(self.token, "***")

    def on_message(self, msg):
        """Reply text for one incoming message, or None to stay silent."""
        chat = msg.get("chat") or {}
        text = (msg.get("text") or "").strip()
        if str(chat.get("id")) != self.chat or not text.startswith("/"):
            return None
        if time.time() - (msg.get("date") or 0) > COMMAND_MAX_AGE:
            return None
        if chat.get("type") != "private":
            return "Perintah bot hanya diterima dari chat pribadi dengan bot ini, bukan grup."
        try:
            return self.handle(text)
        except (Skip, ValueError) as e:
            return f"⚠️ {e}"
        except Exception as e:
            return f"❌ {type(e).__name__}: {e}"

    def _pick(self, args):
        ps = self.copier.positions_list()
        if not ps:
            raise ValueError("Bot tidak punya posisi terbuka.")
        try:
            n = int(args[0])
        except (IndexError, ValueError):
            raise ValueError("Sebutkan nomor posisi, lihat /posisi.")
        if not 1 <= n <= len(ps):
            raise ValueError(f"Nomor posisi 1–{len(ps)}, lihat /posisi.")
        return ps[n - 1]

    def _positions_text(self):
        ps = self.copier.positions_list()
        if not ps:
            return "Bot tidak punya posisi terbuka."
        out = []
        for i, p in enumerate(ps, 1):
            pnl = f" · PnL {p['pnl_pct']:+.1f}%" if p.get("pnl_pct") is not None else ""
            rng = "" if p.get("in_range") is None else (" · in range" if p["in_range"] else " · OUT OF RANGE")
            sim = " · simulasi" if p.get("virtual") else ""
            out.append(f"{i}. {p['pair']} · modal {fmt_usd(p.get('cost_usd', p.get('usd')))}"
                       f" · nilai {fmt_usd(p.get('value_usd'))}{pnl}{rng}{sim}")
        return "\n".join(out)

    def handle(self, text):
        parts = text.split()
        cmd, args = parts[0].split("@")[0].lower(), parts[1:]
        c = self.copier
        if cmd in ("/start", "/help", "/bantuan"):
            return HELP
        if cmd == "/status":
            st = c.status()
            bal = st.get("balances") or {}
            head = f"Bot copytrade: {st['run'].upper()}" + (f" · DIJEDA ({st['pause_reason']})" if st["paused"] else "")
            return "\n".join([head, f"Wallet: {st['wallet']}",
                              f"Saldo: {bal.get('SOL', 0):.4f} SOL · {bal.get('USDC', 0):.2f} USDC",
                              "", self._positions_text()])
        if cmd == "/posisi":
            return self._positions_text()
        if cmd == "/tarik":
            p = self._pick(args)
            try:
                pct = float(args[1].rstrip("%"))
            except (IndexError, ValueError):
                raise ValueError("Contoh: /tarik 1 50 (tarik 50% posisi nomor 1).")
            return _text(c.withdraw(p["position"], pct))
        if cmd == "/tambah":
            p = self._pick(args)
            try:
                usd = float(args[1].lstrip("$"))
            except (IndexError, ValueError):
                raise ValueError("Contoh: /tambah 1 20 (tambah $20 ke posisi nomor 1).")
            return _text(c.add(p["position"], usd))
        if cmd == "/tutup":
            p = self._pick(args)
            return _text(c.close_position(p["position"], "ditutup dari Telegram"))
        if cmd == "/darurat":
            if not args or args[0].upper() != "YA":
                n = len(c.positions_list())
                return f"Ini akan MENUTUP SEMUA {n} posisi bot dan menjeda bot. Kirim: /darurat YA"
            return "\n\n".join(_text(n) for n in c.emergency_exit("darurat dari Telegram"))
        if cmd == "/jeda":
            c.pause("dijeda dari Telegram")
            return "⏸️ Bot dijeda: tidak membuka atau menambah posisi. Penutupan ikut vault tetap jalan. /lanjut untuk melanjutkan."
        if cmd == "/lanjut":
            c.resume()
            return "▶️ Bot dilanjutkan."
        return "Perintah tidak dikenal.\n\n" + HELP
