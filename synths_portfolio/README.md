# Synths Portfolio – Profit Calculator untuk Synth Bot

Halaman synth di [synths.lol](https://www.synths.lol) cuma nampilin **saldo ETH wallet** dan **UPnL per bag**.
Total portfolio (ETH + nilai semua koin) dan profit terhadap modal yang lu fund nggak dihitung.
Script ini ngitung semuanya:

- **Total portfolio** = saldo ETH di wallet + nilai semua koin (bags)
- **Total koin**: modal, nilai sekarang, dan UPnL per koin, plus totalnya
- **Profit total** terhadap modal bersih (fund − withdraw), dipecah jadi realized, unrealized, dan gas & fee
- **Skenario TP/SL**: hasilnya kalau semua bag kena take profit / stop loss (misal `CUSTOM +6% / −6%`)

Cuma pakai standard library Python 3.8+, jadi nggak perlu `pip install` apa-apa.

---

## Mode 1: `calc`, kalkulator manual (tanpa internet)

Salin angka dari halaman synth: `WALLET · ETH`, lalu tiap baris di tab **Bags** (nilai ETH + UPnL).

```bash
python3 synths_portfolio.py calc \
  --name Kinbit --wallet 0.014 --total-pnl 0.000046379 \
  --bag MOTHMAN:0.0017:0.000035698 \
  --bag KELV:0.0019:-0.000051456 \
  --bag GRONAUT:0.0023:0.000062137 \
  --tp 6 --sl 6
```

Atau pakai file JSON (contoh: [`example_kinbit.json`](example_kinbit.json)):

```bash
python3 synths_portfolio.py calc --file example_kinbit.json --funded 0.02
```

Contoh output untuk Kinbit:

```
BAGS (3 koin)
Koin       Modal ETH    Nilai ETH      UPnL ETH       %
-------  -----------  -----------  ------------  ------
MOTHMAN  0.001664302  0.001700000  +0.000035698  +2.14%
KELV     0.001951456  0.001900000  -0.000051456  -2.64%
GRONAUT  0.002237863  0.002300000  +0.000062137  +2.78%
TOTAL    0.005853621  0.005900000  +0.000046379  +0.79%

RINGKASAN
  Saldo ETH di wallet       0.014000000  (~$33.10)
  Nilai semua koin          0.005900000  (~$13.95)
  TOTAL PORTFOLIO           0.019900000  (~$47.05)
  ...
```

Format `--bag`: `KOIN:NILAI_ETH:UPNL_ETH` atau `KOIN:NILAI_ETH:PERSEN%`.
Di file JSON, tiap bag cukup diisi **dua** dari `cost_eth`, `value_eth`, `upnl_eth`, `pct`.

| Flag | Isi |
|---|---|
| `--wallet` | saldo `WALLET · ETH` |
| `--total-pnl` | angka `Total PnL`, dipakai buat realized PnL dan perkiraan modal awal |
| `--funded` / `--withdrawn` | total ETH yang pernah lu fund / withdraw, buat hitung profit total |
| `--tp` / `--sl` | level take profit / stop loss dalam % |
| `--eth-usd` | harga ETH manual. Kalau kosong, diambil dari Alchemy (kalau ada `ALCHEMY_API_KEY`) |

> Angka di halaman synth dibulatkan (misal `0.0017 ETH`), jadi hasil `calc` juga perkiraan.
> Buat angka persis, pakai `scan`.

---

## Mode 2: `scan`, baca langsung dari chain

Butuh API key [Alchemy](https://www.alchemy.com/) di env `ALCHEMY_API_KEY` dan **alamat wallet si synth**
(alamat tujuan waktu lu klik **Fund**).

```bash
export ALCHEMY_API_KEY=...
python3 synths_portfolio.py scan 0xALAMAT_WALLET_SYNTH --owner 0xWALLET_LU
```

Yang dilakukan:

1. Ambil saldo ETH (`eth_getBalance`) dan semua koin ERC-20 + harga USD (Alchemy Portfolio API).
2. Ambil semua riwayat transfer ETH (external + internal), WETH, dan ERC-20 (`alchemy_getAssetTransfers`).
3. Kelompokkan per transaksi:
   - ETH keluar + koin masuk = **beli** (jadi modal koin itu)
   - koin keluar + ETH masuk = **jual** (realized PnL, metode average cost)
   - koin keluar + koin lain masuk = **swap** (modal pindah ke koin baru)
   - ETH doang tanpa koin = **fund / withdraw**
4. Hitung total portfolio, modal bersih, profit total, realized, unrealized, dan sisa selisih (= gas & fee).

| Flag | Isi |
|---|---|
| `--network` | default `base-mainnet`. Ganti kalau synth-nya di chain lain (`eth-mainnet`, `arb-mainnet`, dst.) |
| `--owner 0x...` | wallet lu. Kalau diisi, cuma ETH dari/ke wallet ini yang dihitung fund/withdraw. ETH dari alamat lain (misal fee kreator) jadi pemasukan. |
| `--value KOIN=ETH` | override nilai bag (dalam ETH). Pakai ini kalau harga koinnya nggak ketemu di Alchemy, misal koin yang masih di bonding curve. Ambil angkanya dari tab Bags. |
| `--only-traded` | jangan hitung koin yang masuk tanpa dibeli (airdrop/spam) |
| `--json` | output JSON |

Catatan:

- Koin yang **nggak ada harganya** di Alchemy dihitung senilai modalnya (ditandai `=modal`) sampai lu isi `--value`.
- Koin yang masuk **tanpa bayar** (airdrop / alokasi launch) dipisah. Yang nggak ada harganya disembunyikan dan nggak dihitung.
- Gas nggak tercatat di riwayat transfer, jadi muncul sebagai `gas & fee (selisih)`.
- Kalau synth-nya nggak megang koin on-chain di alamat itu (misal posisinya disimpan di kontrak), pakai mode `calc`.

---

## Test

```bash
python3 -m unittest -v test_synths_portfolio
```
