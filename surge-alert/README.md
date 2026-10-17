# Surge alert — Robinhood Chain

Bot yang mantau **semua koin aktif di Robinhood Chain** terus-terusan, dan nge-ping kamu kalau ada
koin yang volume + chart-nya surging:

- **💀→🚀 bangkit dari mati**: pump → dump → mati lama → surging lagi (kayak chart ZERO)
- **🚀 bangkit abis jatoh**: dump dalam 24 jam terus mantul
- **🚀 lagi pump**: naik tajam barusan dengan volume spike

Filter default: **mcap ≥ $400k** dan **TVL LP ≥ $5k**.

Data gratis tanpa key dari DexPaprika, GeckoTerminal, dan DexScreener (GMGN opsional). Cuma butuh
Python 3.10+, gak perlu install apa-apa.

## Cara pakai

Jalanin di dalam folder `surge-alert`. Pantau semua koin terus, tiap 60 detik (`Ctrl+C` buat stop):

```bash
python surge_alert.py
```

Biar Mac gak sleep selama bot jalan:

```bash
caffeinate -i python surge_alert.py
```

Di terminal ada baris status yang ke-update tiap putaran:

```
[14:02:31] 412 koin dipantau · 96 lolos filter mcap/LP · 0 alert putaran ini
```

Cek status sekarang buat koin di `watchlist.txt` (atau kasih alamat langsung):

```bash
python surge_alert.py --check
```

```bash
python surge_alert.py --check 0x316fa3AB9A8FD8d7567a823DedecF28d9FEE2894
```

Ganti filter, misalnya mcap ≥ $1M dan LP ≥ $20k:

```bash
python surge_alert.py --min-mcap 1000000 --min-liq 20000
```

## Notifikasi (ping)

- **Mac**: notif desktop + bunyi, otomatis nyala tanpa setup.
- **HP**: salin `notify.env.example` jadi `notify.env`, isi salah satu:
  - **ntfy** (paling gampang): install app *ntfy* di HP, subscribe ke nama topic bikinan sendiri
    (bikin yang susah ditebak, topic ntfy itu publik), terus isi `NTFY_TOPIC=nama-topic-itu`.
  - **Telegram**: bikin bot lewat @BotFather (dapet token), ambil chat id dari @userinfobot,
    isi `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID`. Kirim `/start` ke bot kamu dulu.
  - **Discord**: isi `DISCORD_WEBHOOK_URL`.

Cek notifnya nyampe atau gak:

```bash
python surge_alert.py --test-notify
```

## Koin yang dipantau

**Default (gratis, tanpa key).** Tiap 5 menit bot ngumpulin koin dari:

- **DexPaprika**: 500 pool volume 24 jam terbesar di Robinhood Chain yang LP-nya ≥ $5k (disaring
  di server DexPaprika). Token yang nongol di banyak pool (WETH, USDC, saham token) dibuang karena
  itu token pasangan, bukan koinnya.
- **GeckoTerminal**: pool trending 5 menit / 1 jam, pool baru, dan pool volume 24 jam terbesar
  (digilir 2 halaman per putaran).
- **DexScreener**: token yang baru pasang profile / boost.

Mcap ≥ $400k dicek per koin lewat DexScreener tiap menit. Semua API gratis itu punya batas
request, jadi bot sengaja pelan-pelan. Kalau tetap kena limit, bot diem sampai limitnya reset,
bukan nembak terus.

**GMGN (opsional).** Kalau kamu punya `GMGN_API_KEY` (GMGN minta deposit buat bikin key), isi di
`notify.env`. Bot bakal ambil koin dari GMGN tiap menit dengan filter mcap & LP langsung di server
GMGN, plus candle-nya. Cara bikin key:

1. Bikin pasangan kunci. GMGN minta *public key*-nya pas daftar:

   ```bash
   openssl genpkey -algorithm ed25519 -out ~/gmgn_private.pem
   ```

   ```bash
   openssl pkey -in ~/gmgn_private.pem -pubout
   ```

2. Buka https://gmgn.ai/ai, tempel teks `-----BEGIN PUBLIC KEY----- …` ke form pembuatan API key
   (cukup *Enable Reading*), submit, terus salin API key-nya.
3. Isi `GMGN_API_KEY=…` di `notify.env`, lalu jalanin ulang bot. Terminal bakal nulis
   `Sumber koin: GMGN`.

`watchlist.txt` berisi koin yang pengen selalu kamu pantau (udah ada ZERO, IGNITION, AGEM). Koin
yang udah ketemu tetap dipantau 24 jam sejak terakhir nongol (maks 500 koin).

Cuma mau pantau watchlist: `python surge_alert.py --no-scan`

Di `watchlist.txt` lebih aman pakai **alamat kontrak** (`0x…`) daripada simbol, soalnya banyak clone
dengan ticker sama.

## Logika sinyal

Angka default, bisa diubah lewat flag (`python surge_alert.py --help`).

**💀→🚀 Bangkit dari mati**: pakai candle 1 jam 30 hari terakhir dari GeckoTerminal, karena fase
"mati"-nya bisa berhari-hari (DexScreener cuma kasih data 24 jam).

| Syarat | Default | Flag |
|---|---|---|
| Ada puncak lama, level matinya jauh di bawah | ≥ 70% di bawah puncak | `--dead-drop` |
| Mati: harga flat (rentang p90/p10) sebelum surge | ≥ 24 jam, rentang ≤ 2x | `--dead-hours` |
| Breakout di atas atap rentang mati, dalam 6 jam terakhir | ≥ +50% | `--revival` |
| Volume per jam pas surge vs pas mati | ≥ 5x | — |

Biar hemat rate limit GeckoTerminal, candle cuma diambil buat koin yang lagi naik
(1 jam ≥ +20% atau 6 jam ≥ +40%) dan udah lolos filter, maks 8 koin per putaran.

**🚀 Bangkit abis jatoh**: versi cepat dalam 24 jam.

| Syarat | Default | Flag |
|---|---|---|
| Turun dari puncak ke dasar | ≥ 40% dalam 24 jam | `--drop`, `--lookback` |
| Dasarnya masih baru | ≤ 180 menit lalu | `--low-age` |
| Mantul dari dasar | ≥ 30% | `--rebound` |

**🚀 Lagi pump**: gak perlu ada dump dulu.

| Syarat | Default | Flag |
|---|---|---|
| Naik dari harga terendah barusan | ≥ 25% dalam 60 menit | `--pump`, `--pump-window` |

**Semua sinyal** harus lolos filter ini:

| Syarat | Default | Flag |
|---|---|---|
| Market cap | ≥ $400k | `--min-mcap` |
| Likuiditas / TVL LP | ≥ $5k | `--min-liq` |
| Volume 1 jam | ≥ $5k | `--min-vol` |
| Volume spike (1j vs rata-rata/jam 6j) | ≥ 2x | `--vol-spike` |
| Porsi tx buy 1 jam | ≥ 55% | `--min-buy` |

- Pertama kali lihat koin, history diisi dari `priceChange` (5m/1j/6j/24j), jadi bisa langsung alert
  tanpa nunggu. Abis itu history diisi dari polling sendiri (`surge_state.json`).
- Anti-spam: satu episode cuma di-alert sekali per koin. Alert lagi kalau ada dasar baru setelah
  cooldown 60 menit, atau harga udah 1.5x dari alert terakhir (masih lanjut terbang). Kebangkitan
  dari mati yang sama cuma sekali dalam 24 jam.
- Kalau kebanyakan alert, naikin `--pump`, `--min-mcap`, atau `--min-vol`.

## Catatan

- Bot cuma jalan selama terminal-nya kebuka dan Mac-nya nyala.
- Chain id DexScreener, GeckoTerminal, & GMGN: `robinhood`. Kalau sumber candle gagal, bot ngasih
  warning sekali dan sinyal "bangkit dari mati" dilewat. Sinyal lain tetap jalan.
- Update bot: `git pull` di folder `~/surge-bot`.
- Tes: `python -m unittest test_surge_alert`
- Bukan saran finansial. Mantul abis dump di memecoin sering jadi dead-cat bounce.
