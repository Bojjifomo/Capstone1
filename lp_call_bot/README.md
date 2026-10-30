# LP Call Bot: blast open / close / reentry LP ke follower lewat Telegram

Gantinya ngetik manual di grup WA. Lu ketik call di DM bot pakai format yang sama,
bot langsung kirim ke **channel Telegram** lu + **semua follower yang udah /start bot**.
CA dikirim sebagai `code`, jadi follower tinggal tap buat copy.

Python 3 standard library aja, nggak perlu `pip install`.

## Format call (ketik di DM bot)

| Ketik | Yang kekirim ke follower |
| --- | --- |
| `0x… hydx 2.5% -65` | 🟢 OPEN LP — HYDX, CA, pool 2.5%, range -65% (single sided USDG) |
| `0x… dark 3% -60 -65` | Range dua angka: -60% / -65% |
| `0x… rebate 2%` | Range nggak ditulis = `DEFAULT_RANGE` (-65) |
| `close dark` | 🔴 CLOSE LP — DARK + CA-nya |
| `close worm boomer` / `close all` | Beberapa koin sekaligus dalam satu pesan |
| `reentry hydx vrax` / `reenter prior` | 🔁 REENTRY: CA, fee, range diambil dari open terakhir koin itu |

Huruf besar/kecil bebas. Koin yang belum pernah di-open nggak bakal di-blast pas
`close`/`reentry` (biar salah ketik nggak nyampe ke follower); bot ngasih tau koin mana
yang dilewatin. Tiap blast, bot bales "✅ Terkirim ke N penerima".

Command owner: `/posisi` (LP yang kebuka), `/stats`, `/blast <teks bebas>`,
`/hapus <koin>`, `/help`.
Command follower: `/start` (langganan), `/stop`, `/posisi`.

## Setup

1. Chat [@BotFather](https://t.me/BotFather) → `/newbot` → simpan token-nya.
2. `cp lp_call_bot/.env.example lp_call_bot/.env`, isi `TELEGRAM_BOT_TOKEN`.
3. Jalanin `python3 lp_call_bot/bot.py`, buka bot lu di Telegram, kirim `/id`.
   Masukin angkanya ke `OWNER_IDS` di `.env`, restart bot.
4. (Opsional) Bikin channel, tambahin bot jadi **admin** (izin post), isi `CHANNEL_IDS`
   (`@namachannel` atau `-100…`).
5. Share link bot (`t.me/<username_bot>`) atau link channel ke follower.

Bot harus nyala terus biar call langsung kekirim (Mac mini / VPS). Pakai long polling,
jadi nggak butuh domain atau port kebuka. State (posisi + subscriber) disimpen di
`lp_call_bot/state.json`.

Jalan terus di Mac lewat launchd (disarankan: nyala sendiri tiap login, restart sendiri kalau mati):

```sh
sh lp_call_bot/install.sh              # pasang / update  (label: com.bojjifomo.lp-call-bot)
sh lp_call_bot/install.sh uninstall    # copot
launchctl kickstart -k gui/$(id -u)/com.bojjifomo.lp-call-bot   # restart
```

Kalau sebelumnya jalan pakai `nohup`, matiin dulu (`pkill -f lp_call_bot/bot.py`) biar nggak dobel.

Tiap kali Telegram berhasil dihubungi (kira-kira tiap 50 detik), bot nyentuh file `lp_call_bot/heartbeat`.
Agent Office (⚜️ Chain) ngecek umur file itu: kalau lebih dari 15 menit nggak diperbarui, bot dianggap mati
dan warden-nya (Lyra) dikirim buat ngecek.

## Test

```sh
python3 -m unittest discover -s lp_call_bot/tests
```
