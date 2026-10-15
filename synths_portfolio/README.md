# Synths Portfolio – Profit Calculator untuk Synth Bot

Halaman synth di [synths.lol](https://www.synths.lol) cuma nampilin **saldo ETH wallet** dan **UPnL per bag**.
Total portfolio (ETH + nilai semua koin) dan profit terhadap modal yang lu fund nggak dihitung.
Script ini ngitung semuanya:

- **Total portfolio** = saldo ETH di wallet + nilai semua koin (bags)
- **Total koin**: modal, nilai sekarang, dan UPnL per koin, plus totalnya
- **Profit total** terhadap modal bersih (fund − withdraw), dipecah jadi realized, unrealized, dan gas & fee
- **Skenario TP/SL**: hasilnya kalau semua bag kena take profit / stop loss (misal `CUSTOM +6% / −6%`)

Cuma pakai standard library Python 3.8+, jadi nggak perlu `pip install` apa-apa.
Default-nya baca data dari **Robinhood Chain mainnet**, chain tempat synths.lol jalan.

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

Synths jalan di **Robinhood Chain mainnet**, dan itu udah jadi default. Yang dibutuhin cuma API key
[Alchemy](https://www.alchemy.com/) (free tier cukup) di env `ALCHEMY_API_KEY`, plus **alamat wallet si synth**.

```bash
export ALCHEMY_API_KEY=...
python3 synths_portfolio.py scan 0xALAMAT_WALLET_SYNTH
```

Windows (PowerShell):

```powershell
$env:ALCHEMY_API_KEY="..."
python synths_portfolio.py scan 0xALAMAT_WALLET_SYNTH
```

**Nggak tahu alamat wallet synth-nya?** Cari pakai alamat wallet lu sendiri (yang dipakai bikin/fund synth):

```bash
python3 synths_portfolio.py wallets 0xWALLET_LU
```

```
Wallet synth                                  Saldo ETH  Block pertama
------------------------------------------  -----------  -------------
0xf3c908bef2a4a1dc72ce2157969d6c3e4bf2c2ca  0.018549840       89029796
```

Wallet synth itu smart contract kecil (beacon proxy) yang dibikin factory synths, bukan wallet biasa.

Contoh output:

```
SYNTH WALLET 0xf3c9...c2ca  [robinhood-mainnet]   ETH/USD $2,454.20
Owner (fund/withdraw): 0xb55f...740c

BAGS - koin yang lagi dipegang (1)
Koin        Jumlah    Modal ETH    Nilai ETH      UPnL ETH        %           Harga
-------  ---------  -----------  -----------  ------------  -------  --------------
GRONAUT  1,618,547  0.002859758  0.003566783  +0.000707025  +24.72%  trade terakhir
TOTAL               0.002859758  0.003566783  +0.000707025  +24.72%

RIWAYAT TRADE - realized PnL (2 koin)
Koin     #Beli  #Jual     Beli ETH     Jual ETH  Realized ETH
-------  -----  -----  -----------  -----------  ------------
KELV         3      1  0.001862044  0.001794901  -0.000067144
MOTHMAN      3      1  0.001712220  0.001728363  +0.000016143

RINGKASAN (17 transaksi)
  Saldo ETH di wallet         0.018549840  (~$45.53)
  Nilai semua koin            0.003566783  (~$8.75)
  TOTAL PORTFOLIO             0.022116623  (~$54.28)

  Total fund (deposit)        0.020000000
  Total withdraw              0.000000000
  Modal bersih                0.020000000  (~$49.08)

  PROFIT TOTAL                +0.002116623  +10.58%  (~$5.19)
    realized (koin dijual)    -0.000051000
    unrealized (bags)         +0.000707025
    reward/fee dari platform  +0.003460598
    fee/gas keluar            -0.002000000
```

### Cara kerjanya

Di Robinhood Chain, ETH hasil swap masuk/keluar wallet synth lewat **transfer internal**, dan itu
nggak ada di riwayat transfer Alchemy. Jadi script ini:

1. Ambil saldo ETH dan semua koin ERC-20 (Alchemy Portfolio API).
2. Ambil semua transaksi ke/dari wallet, termasuk call bernilai 0 dari operator synths, plus transfer
   ERC-20 (`alchemy_getAssetTransfers`).
3. Hitung **perubahan saldo ETH di tiap block** itu: `saldo(block) - saldo(block - 1)`.
   Ini nangkep ETH yang lewat transfer internal: hasil jual, ETH buat beli, fee.
4. Saldo **di sela-sela** transaksi juga dibandingin. Kalau berubah, block-nya dicari, misalnya fund awal
   lewat factory waktu synth dibikin, atau reward dari kontrak platform. Jadi semua ETH kelacak.
5. Kelompokkan per block:
   - ETH keluar + koin masuk = **beli** (jadi modal koin itu)
   - koin keluar + ETH masuk = **jual** (realized PnL, metode average cost)
   - koin keluar + koin lain masuk = **swap** (modal pindah ke koin baru)
   - ETH doang dari/ke **owner** = **fund / withdraw**
   - ETH doang dari/ke alamat lain = **reward/fee dari platform** (fee kreator dari koin yang
     di-launch synth, reward) atau **fee/gas keluar** (fee setup synth, dll)
6. Nilai bag: harga dari Alchemy kalau ada. Kalau nggak ada (koin synths biasanya nggak ada), pakai
   **harga jual terakhir** dari event `Trade` yang dipancarkan wallet synth. Kalau belum pernah ada yang
   jual, pakai harga beli terakhir.

**Owner** dideteksi otomatis, yaitu alamat yang pertama kali ngisi ETH ke wallet (yang bikin synth).
Ganti pakai `--owner` kalau salah.

| Flag | Isi |
|---|---|
| `--network` | default `robinhood-mainnet`. Bisa juga `base-mainnet`, `eth-mainnet`, dst. |
| `--owner 0x...` | wallet lu. ETH dari/ke wallet ini = fund/withdraw, selain itu = pemasukan/pengeluaran lain. Default: dideteksi otomatis. |
| `--value KOIN=ETH` | override nilai bag (dalam ETH), contoh `--value GRONAUT=0.0035` |
| `--only-traded` | jangan hitung koin yang masuk tanpa dibeli (airdrop/spam) |
| `--json` | output JSON |

Catatan:

- Free tier Alchemy dibatesin ±25 request saldo per detik. Synth kecil selesai beberapa detik, tapi
  synth yang udah ratusan kali trade bisa 1-2 menit. Progresnya ditampilin di layar, dan kalau kena limit
  script otomatis memperlambat diri.
- Kalau nggak ada harga sama sekali, koin dihitung senilai modalnya (ditandai `=modal`).
- Koin yang masuk **tanpa bayar** (airdrop / alokasi launch) dipisah. Yang nggak ada harganya disembunyikan
  dan nggak dihitung.
- Gas dibayar operator synths, bukan wallet synth. Kalau masih ada baris `selisih`, berarti ada ETH yang
  nggak kelacak.

---

## Test

```bash
python3 -m unittest -v test_synths_portfolio
```
