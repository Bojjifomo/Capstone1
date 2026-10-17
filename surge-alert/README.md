# Surge alert — Robinhood Chain

Bot alert buat koin yang **surging abis jatoh** (kayak pola IGNITION / AGEM) dan koin yang
**lagi pump lagi** (kayak ZERO). Data dari DexScreener (gratis, tanpa API key),
cuma butuh Python 3.10+ tanpa install apa-apa.

## Cara pakai

```bash
# cek status semua token di watchlist.txt
python surge_alert.py --check

# cek token tertentu
python surge_alert.py --check 0x316fa3AB9A8FD8d7567a823DedecF28d9FEE2894

# pantau watchlist.txt terus tiap 60 detik + kirim alert
python surge_alert.py

# + ikut scan token baru di Robinhood Chain (yang masang profile/boost di DexScreener)
python surge_alert.py --scan
```

## Watchlist

Token yang mau dipantau ditaruh di `watchlist.txt`, satu per baris. Udah ada ZERO, IGNITION, AGEM:

```
0x316fa3AB9A8FD8d7567a823DedecF28d9FEE2894  # ZERO (ZeroTrace)
IGNITION
AGEM
```

Lebih aman pakai **alamat kontrak** (`0x…`) daripada simbol, soalnya banyak clone dengan ticker sama.
Kalau simbolnya dobel, bot bakal ngasih warning plus alamat yang dipilih.
`--watch TOKEN …` bisa dipakai buat ganti isi watchlist sekali jalan.

## Kirim alert ke HP

Set salah satu (atau semua) env var ini sebelum jalanin:

| Tujuan   | Env var                                    |
|----------|--------------------------------------------|
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`   |
| Discord  | `DISCORD_WEBHOOK_URL`                      |
| ntfy.sh  | `NTFY_TOPIC` (install app ntfy, subscribe topic yang sama) |

## Logika sinyal

Ada 2 jenis sinyal (angka default, bisa diubah lewat flag):

**🚀 Bangkit abis jatoh** — semua token (watchlist + scan)

| Syarat | Default | Flag |
|---|---|---|
| Turun dari puncak ke dasar | ≥ 40% dalam 24 jam | `--drop`, `--lookback` |
| Dasarnya masih baru | ≤ 180 menit lalu | `--low-age` |
| Mantul dari dasar | ≥ 30% | `--rebound` |

**🚀 Lagi pump** — cuma token di watchlist, buat yang "pump lagi" kayak ZERO
(puncak lamanya bisa udah lewat 24 jam, jadi gak perlu ada dump dulu)

| Syarat | Default | Flag |
|---|---|---|
| Naik dari harga terendah barusan | ≥ 25% dalam 60 menit | `--pump`, `--pump-window` |

Dua-duanya juga harus lolos konfirmasi:

| Syarat | Default | Flag |
|---|---|---|
| Likuiditas | ≥ $10k | `--min-liq` |
| Volume 1 jam | ≥ $5k | `--min-vol` |
| Volume spike (1j vs rata-rata/jam 6j) | ≥ 2x | `--vol-spike` |
| Porsi tx buy 1 jam | ≥ 55% | `--min-buy` |

- Pertama kali lihat token, history diisi dari `priceChange` (5m/1j/6j/24j), jadi bisa langsung alert
  tanpa nunggu. Abis itu history diisi dari polling sendiri (`surge_state.json`), jadi makin akurat.
- Anti-spam: satu episode cuma di-alert sekali per token (kalau dua sinyal nyala bareng, yang dikirim
  "bangkit abis jatoh"). Alert lagi kalau ada dasar baru setelah cooldown 60 menit, atau harga udah
  1.5x dari alert terakhir (masih lanjut terbang).

## Catatan

- Chain id DexScreener diasumsikan `robinhood` (sesuai URL `dexscreener.com/robinhood`). Kalau
  `--check` bilang "gak ketemu di chain 'robinhood'", lihat daftar chain yang ditampilin lalu pakai `--chain`.
- `--scan` cuma nangkep token yang masang profile/boost di DexScreener, jadi bukan semua token di chain.
- Tes: `python -m unittest test_surge_alert`
- Bukan saran finansial. Mantul abis dump di memecoin sering jadi dead-cat bounce.
