# Surge alert — Robinhood Chain

Bot alert buat koin yang **surging abis jatoh** (kayak pola IGNITION / AGEM): harga anjlok dalam,
terus mantul kenceng dengan volume & buy pressure. Data dari DexScreener (gratis, tanpa API key),
cuma butuh Python 3.10+ tanpa install apa-apa.

## Cara pakai

```bash
# cek status sekarang
python surge_alert.py --check IGNITION AGEM

# pantau terus tiap 60 detik + kirim alert
python surge_alert.py --watch IGNITION AGEM

# + ikut scan token baru di Robinhood Chain (yang masang profile/boost di DexScreener)
python surge_alert.py --watch IGNITION AGEM --scan
```

Lebih aman pakai **alamat kontrak** (`0x…`) daripada simbol, soalnya banyak clone dengan ticker sama.
Kalau simbolnya dobel, bot bakal ngasih warning plus alamat yang dipilih.

## Kirim alert ke HP

Set salah satu (atau semua) env var ini sebelum jalanin:

| Tujuan   | Env var                                    |
|----------|--------------------------------------------|
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`   |
| Discord  | `DISCORD_WEBHOOK_URL`                      |
| ntfy.sh  | `NTFY_TOPIC` (install app ntfy, subscribe topic yang sama) |

## Logika sinyal

Alert nyala kalau **semua** syarat ini kepenuhi (angka default, bisa diubah lewat flag):

| Syarat | Default | Flag |
|---|---|---|
| Turun dari puncak ke dasar | ≥ 40% dalam 24 jam | `--drop`, `--lookback` |
| Dasarnya masih baru | ≤ 180 menit lalu | `--low-age` |
| Mantul dari dasar | ≥ 30% | `--rebound` |
| Likuiditas | ≥ $10k | `--min-liq` |
| Volume 1 jam | ≥ $5k | `--min-vol` |
| Volume spike (1j vs rata-rata/jam 6j) | ≥ 2x | `--vol-spike` |
| Porsi tx buy 1 jam | ≥ 55% | `--min-buy` |

- Pertama kali lihat token, history diisi dari `priceChange` (5m/1j/6j/24j), jadi bisa langsung alert
  tanpa nunggu. Abis itu history diisi dari polling sendiri (`surge_state.json`), jadi makin akurat.
- Anti-spam: satu episode dip cuma di-alert sekali. Alert lagi kalau ada dasar baru setelah cooldown
  60 menit, atau harga udah 1.5x dari alert terakhir (masih lanjut terbang).

## Catatan

- Chain id DexScreener diasumsikan `robinhood` (sesuai URL `dexscreener.com/robinhood`). Kalau
  `--check` bilang "gak ketemu di chain 'robinhood'", lihat daftar chain yang ditampilin lalu pakai `--chain`.
- `--scan` cuma nangkep token yang masang profile/boost di DexScreener, jadi bukan semua token di chain.
- Tes: `python -m unittest test_surge_alert`
- Bukan saran finansial. Mantul abis dump di memecoin sering jadi dead-cat bounce.
