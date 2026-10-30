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

## Auto call dari wallet (tanpa ngetik)

Bot baca transaksi wallet lu di Uniswap V4 (Robinhood Chain) **real time** (tiap 15 detik, cuma block baru, nggak
ada baca-mundur) dan ngirim call sendiri, ke channel + semua follower:

| Yang kebaca di chain | Yang nyampe ke follower |
| --- | --- |
| Lu mint posisi (NFT baru masuk wallet) | 🟢 OPEN LP: CA koin, simbol, fee pool, range % (dari tick posisi vs harga pool) |
| Lu close posisi (NFT di-burn) | 🔴 CLOSE LP: CA + nama koin |

* **Nggak ada logika reentry di auto call.** Reentry = CLOSE lalu OPEN lagi, jadi dua pesan biasa.
  (Command ketik `reentry <koin>` di DM bot tetep ada.)
* **CA** = token di pool yang bukan USDG. **Range** = kedalaman ladder dari ujung atas ke ujung bawahnya, dalam %
  harga koin, dibulatin ke kelipatan `AUTO_RANGE_STEP` (5). Tool LP nyetel tick per rung pakai tick spacing, jadi
  -70 yang lu ketik jadi -71.3% di chain; dibulatin balik jadi -70. `AUTO_RANGE_STEP=1` = angka persis.
  Nggak tergantung harga pool, jadi nggak geser walau harga gerak.
* **Ladder:** satu posisi lu biasanya beberapa NFT sekaligus dalam satu transaksi. Bot gabungin jadi **satu** call
  per transaksi dan pool, dengan range dari rung paling atas sampai paling bawah.
* Cuma open yang **single sided USDG** yang di-blast. Open dua sisi atau di pool tanpa USDG dilewatin dan lu dikabarin
  lewat DM. Nambah / ngurangin liquidity di NFT yang udah ada nggak dianggap call.
* Close dikenali dari NFT yang di-burn. Di 72 jam wallet asli, 35 dari 35 close ikut burn. Kalau lu pernah nutup
  posisi tanpa burn, itu nggak kebaca.
* Bot mati lebih dari `AUTO_CATCHUP_MINUTES` (30)? Aktivitas yang lebih lama dari itu dilewatin (daripada ngirim call
  basi ke follower) dan lu dikabarin. Yang lebih baru dari itu tetep dikirim begitu bot nyala lagi.
* Call manual (ketik) tetep jalan barengan. Cek status: `/auto` (block terakhir dibaca, kapan terakhir sehat, error
  terakhir). Kalau watcher error 3x berturut-turut, bot ngabarin lewat DM. Owner harus pernah `/start` bot dari
  akun sendiri biar DM bisa masuk.

Setup, di `lp_call_bot/.env`:

```
WALLET_ADDRESSES=0x...          # alamat publik wallet lu (BUKAN private key), boleh lebih dari satu
AUTO_DRY_RUN=1                  # tes dulu: call otomatis cuma dikirim ke lu
```

RPC: `RPC_URL` boleh kosong (otomatis RPC publik Robinhood). `CALL_RPC_URL` opsional (API key Alchemy); tambahin
`CALL_SPLIT=rpc1` biar Alchemy cuma jadi cadangan. Pas pertama nyala bot cuma nyatet "sekarang" dan mulai dari situ.
Pas udah yakin hasilnya bener, ganti `AUTO_DRY_RUN=0` terus restart bot.

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
