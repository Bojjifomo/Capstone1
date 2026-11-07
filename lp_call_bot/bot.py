"""LP Call Bot: owner ketik call di DM bot, bot nge-blast ke channel + semua subscriber.

Jalanin: python3 lp_call_bot/bot.py   (config di lp_call_bot/.env, lihat .env.example)
"""
import html
import json
import os
import sys
import threading
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
/auto: status auto call dari wallet
/wallet: wallet yang dipantau
/wallet add 0x… nama: pantau wallet lain, call-nya dikasih nama itu
/wallet hapus nama: berhenti mantau
/rekap: kirim rekap posisi kebuka sekarang
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


def build_rpcs(watcher):
    """RPC cuma dari .env bot ini (nggak minjem punya rh_wallet_finder / RH Smart LP Finder, yang bisa kehabisan kuota).

    RPC_URL = utama (default RPC publik Robinhood). BACKUP_RPC_URLS (pisah koma, urut prioritas) + CALL_RPC_URL =
    cadangan: baru kepake kalau yang di depannya error, kena limit, atau kuotanya habis (yang kuotanya habis
    diistirahatin sejam). Key Alchemy doang otomatis jadi link Robinhood Chain-nya."""
    R, RH = watcher.R, watcher.RH
    primary = RH.rpc_link(os.environ.get("RPC_URL", "")) or watcher.PUBLIC_RPC
    backups = os.environ.get("BACKUP_RPC_URLS", "").split(",") + [os.environ.get("CALL_RPC_URL", "")]
    urls = list(dict.fromkeys([primary] + [RH.rpc_link(u) for u in backups if u.strip()]))
    print("RPC: " + " → ".join(RH.mask(u) for u in urls), flush=True)
    if len(urls) == 1:
        one = R.RPC(primary, workers=4)
        return one, one
    pool = R.RPCPool([R.RPC(u, workers=4) for u in urls], prefer=0)
    return pool, pool


def env_wallets():
    """Wallet owner dari WALLET_ADDRESSES di .env (call-nya tanpa nama)."""
    wallets = [w.strip().lower() for w in os.environ.get("WALLET_ADDRESSES", "").split(",") if w.strip()]
    bad = [w for w in wallets if not is_address(w)]
    if bad:
        sys.exit(f"WALLET_ADDRESSES salah (harus 0x + 40 karakter): {bad}")
    return wallets


def is_address(text):
    t = text.lower()
    return len(t) == 42 and t.startswith("0x") and all(c in "0123456789abcdef" for c in t[2:])


def build_watcher(store, wallets):
    """Auto call dari wallet. Selalu nyala biar wallet bisa ditambah lewat /wallet; tanpa wallet cuma baca nomor block."""
    import watcher
    logs_rpc, calls_rpc = build_rpcs(watcher)
    return watcher.Watcher(logs_rpc, calls_rpc, wallets, store,
                           catchup_minutes=float(os.environ.get("AUTO_CATCHUP_MINUTES", "30")),
                           range_step=int(os.environ.get("AUTO_RANGE_STEP", "5")))


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
    def __init__(self, tg, store, owners, channels, default_range="-65", heartbeat=None,
                 watcher=None, watch_seconds=15, dry_run=False, recap_minutes=60, owner_wallets=()):
        self.tg = tg
        self.store = store
        self.owners = set(owners)
        self.channels = channels
        self.default_range = default_range
        self.heartbeat = Path(heartbeat) if heartbeat else None
        self.watcher = watcher
        self.watch_seconds = watch_seconds
        self.dry_run = dry_run
        self.watch_ok = None
        self.watch_err = None
        self.watch_fails = 0
        self.recap_seconds = recap_minutes * 60
        self.started = time.time()
        self.conflict_warned = False
        self.owner_wallets = [w.lower() for w in owner_wallets]
        if self.watcher:
            self.watcher.set_wallets(self.all_wallets())

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
        elif cmd == "/auto":
            self.reply(chat_id, self.auto_status())
        elif cmd == "/wallet":
            self.reply(chat_id, self.wallet_command(arg.strip()))
        elif cmd == "/rekap":
            if not self.send_recap():
                self.reply(chat_id, "Lagi nggak ada LP yang kebuka, nggak ada yang di-blast.")
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

    # auto call dari wallet
    def notify_owners(self, text):
        for owner in self.owners:
            ok, code = self.tg.send(owner, text)
            if not ok:
                print(f"DM ke owner {owner} gagal (kode {code}). Owner sudah /start bot ini dari akun sendiri?", flush=True)

    def auto_status(self):
        if not self.watcher:
            return "Auto call mati."
        st = self.watcher.status()
        wallets = "\n".join(f"<code>{w}</code>" + (f" ({html.escape(self.wallet_name(w))})" if self.wallet_name(w) else "")
                            for w in st["wallets"]) or "(belum ada wallet: /wallet add 0x… nama)"
        mode = "DRY RUN (cuma ke lu)" if self.dry_run else "LIVE (ke follower)"
        return (f"🤖 Auto call nyala: {mode}\n{wallets}\nBlock terakhir dibaca: {st['cursor']}\n"
                + self.watch_health() + "\n" + self.rpc_usage())

    def rpc_usage(self):
        """Panggilan RPC sejak bot nyala + perkiraan per hari, biar keliatan muat kuota (misal SolidRPC 10k/hari)."""
        calls = getattr(self.watcher.rpc, "calls", None)
        if calls is None:
            return ""
        hours = max((time.time() - self.started) / 3600, 1 / 60)
        return f"Panggilan RPC sejak nyala: {calls:,} (± {calls / hours * 24:,.0f}/hari, tiap {self.watch_seconds:g} detik)"

    def watch_health(self):
        if self.watch_ok:
            return f"Terakhir sehat: {int(time.time() - self.watch_ok)} detik lalu"
        return "Belum pernah berhasil baca chain" + (f"\nError terakhir: <code>{html.escape(self.watch_err)}</code>" if self.watch_err else "")

    # wallet yang dipantau
    def all_wallets(self):
        return list(dict.fromkeys(self.owner_wallets + list(self.store.wallets())))

    def wallet_name(self, address):
        """Nama wallet tambahan; None buat wallet owner (call-nya kayak biasa)."""
        if not address or address.lower() in self.owner_wallets:
            return None
        return self.store.wallets().get(address.lower())

    def wallet_command(self, arg):
        sub, _, rest = arg.partition(" ")
        sub, rest = sub.lower(), rest.strip()
        if sub in ("add", "tambah"):
            address, _, name = rest.partition(" ")
            name = name.strip()
            if not is_address(address) or not name:
                return "Format: <code>/wallet add 0x… nama</code>"
            if address.lower() in self.owner_wallets:
                return "Itu wallet lu sendiri (WALLET_ADDRESSES), udah dipantau."
            taken = [a for a, n in self.store.wallets().items() if n.lower() == name.lower() and a != address.lower()]
            if taken:
                return f"Nama {html.escape(name)} udah dipakai wallet lain, pilih nama lain."
            self.store.add_wallet(address, name)
            self.refresh_wallets()
            return (f"✅ Mantau wallet <b>{html.escape(name)}</b> <code>{address.lower()}</code> mulai sekarang. "
                    f"Call-nya bertanda · {html.escape(name)}.")
        if sub in ("hapus", "del", "remove"):
            target = rest.lower()
            match = [a for a, n in self.store.wallets().items() if target in (a, n.lower())]
            if not match:
                if target in self.owner_wallets:
                    return "Wallet lu sendiri diatur di WALLET_ADDRESSES (.env), bukan lewat /wallet."
                return "Wallet itu nggak ada di daftar. Cek /wallet."
            name = self.store.remove_wallet(match[0])
            self.refresh_wallets()
            return f"🗑 Berhenti mantau <b>{html.escape(name)}</b>."
        if sub:
            return "Pakai <code>/wallet</code>, <code>/wallet add 0x… nama</code>, atau <code>/wallet hapus nama</code>."
        return self.wallet_list()

    def wallet_list(self):
        lines = ["👛 <b>Wallet yang dipantau</b>", ""]
        lines += [f"• <code>{w}</code> (lu)" for w in self.owner_wallets]
        lines += [f"• <b>{html.escape(n)}</b> <code>{a}</code>" for a, n in self.store.wallets().items()]
        if len(lines) == 2:
            lines.append("Belum ada. Tambah: <code>/wallet add 0x… nama</code>")
        return "\n".join(lines)

    def refresh_wallets(self):
        if self.watcher:
            self.watcher.set_wallets(self.all_wallets())

    def auto_call(self, ev):
        """Satu call dari watcher: sama kayak lu ketik sendiri, tapi dari transaksi wallet."""
        by = self.wallet_name(ev.get("wallet"))
        if ev["action"] == "skip":
            self.notify_owners("ℹ️ " + (f"[{html.escape(by)}] " if by else "") + ev["text"])
            return
        pos = dict(ev["pos"], by=by) if by else dict(ev["pos"])
        key = f"{pos['coin']}@{by.lower()}" if by else pos["coin"]  # posisi wallet lain nggak nimpa punya lu
        if ev["action"] == "open":
            text = calls.render_open(self.store.open_position(dict(pos, key=key)))
        else:
            known = self.store.get(key)
            text = calls.render_close([self.store.set_status(key, "closed") if known else pos])
        if self.dry_run:
            self.notify_owners("🧪 DRY RUN, nggak dikirim ke follower:\n\n" + text)
            return
        sent, failed = self.blast(text)
        self.notify_owners(f"🤖 Auto call terkirim ke {sent} penerima" + (f", {failed} gagal" if failed else "")
                           + ":\n\n" + text)

    def watch_once(self):
        for ev in self.watcher.poll():
            print("auto call:", ev["action"], ev.get("pos", ev.get("text")), flush=True)
            self.auto_call(ev)
        self.watch_ok = time.time()
        self.watch_fails = 0

    def watch_loop(self):
        while True:
            try:
                self.watch_once()
            except Exception as e:  # RPC lagi bermasalah: coba lagi nanti, bot Telegram tetep jalan
                self.watch_fails += 1
                self.watch_err = repr(e)
                print("watcher error:", self.watch_err, flush=True)
                if self.watch_fails == 3:  # sekali aja, biar lu tau tanpa liat terminal
                    self.notify_owners(f"⚠️ Auto call error 3x berturut-turut, call belum kebaca:\n<code>{html.escape(self.watch_err)}</code>")
            time.sleep(self.watch_seconds)

    # rekap per jam
    def send_recap(self):
        """Blast daftar posisi yang masih kebuka (tanpa nilai). False kalau nggak ada yang kebuka."""
        if not any(p["status"] == "open" for p in self.store.positions()):
            return False
        text = calls.render_list(self.store.positions(), title="Rekap posisi LP yang masih kebuka")
        if self.dry_run:
            self.notify_owners("🧪 DRY RUN, nggak dikirim ke follower:\n\n" + text)
        else:
            self.blast(text)
        return True

    def maybe_recap(self, now=None):
        """Rekap kalau udah lewat `recap_minutes` sejak rekap terakhir (dicatet di state, aman walau restart)."""
        now = now or time.time()
        with self.store.lock:
            last = self.store.data.get("last_recap")
            if last is None:  # baru nyala: jangan langsung blast, mulai ngitung dari sekarang
                self.store.data["last_recap"] = now
                self.store.save()
                return False
            if now - last < self.recap_seconds:
                return False
            self.store.data["last_recap"] = now
            self.store.save()
        return self.send_recap()

    def recap_loop(self):
        while True:
            try:
                self.maybe_recap()
            except Exception as e:
                print("rekap error:", repr(e), flush=True)
            time.sleep(30)

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
            if res.get("error_code") == 409:  # bot ini jalan dua kali: rebutan pesan + RPC kepake dobel
                if not self.conflict_warned:
                    self.conflict_warned = True
                    print("⚠️ Ada bot lain yang jalan pakai token yang sama (Terminal lain atau launchd). Matiin salah satu: "
                          "pkill -f lp_call_bot/bot.py  atau  launchctl bootout gui/$(id -u)/com.bojjifomo.lp-call-bot",
                          flush=True)
            else:
                print("getUpdates gagal:", res, flush=True)
            time.sleep(5)
            return False
        self.conflict_warned = False
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
        if self.watcher:
            print(f"Auto call nyala: {self.watcher.wallets}" + ("  (DRY RUN)" if self.dry_run else ""), flush=True)
            threading.Thread(target=self.watch_loop, daemon=True).start()
        if self.recap_seconds > 0:
            print(f"Rekap posisi tiap {self.recap_seconds // 60:.0f} menit", flush=True)
            threading.Thread(target=self.recap_loop, daemon=True).start()
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
    store = Store(os.environ.get("STATE_FILE", HERE / "state.json"))
    owner_wallets = env_wallets()
    bot = Bot(
        Telegram(token),
        store,
        owners,
        id_list(os.environ.get("CHANNEL_IDS", "")),
        os.environ.get("DEFAULT_RANGE", "-65"),
        heartbeat=os.environ.get("HEARTBEAT_FILE", HERE / "heartbeat"),
        watcher=build_watcher(store, owner_wallets),
        watch_seconds=float(os.environ.get("AUTO_POLL_SECONDS", "20")),
        dry_run=os.environ.get("AUTO_DRY_RUN", "0") == "1",
        recap_minutes=float(os.environ.get("RECAP_MINUTES", "60")),
        owner_wallets=owner_wallets,
    )
    bot.run()


if __name__ == "__main__":
    main()
