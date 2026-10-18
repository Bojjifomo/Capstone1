# RH Wallet Finder

Database wallet LP **Uniswap V4 di Robinhood Chain** + dashboard buat nyari top wallet:
skor & tier, realized / unrealized PnL, ROI/bulan, win rate, drawdown, copier,
style, Real PnL, preset filter, papan 24 jam, twin finder, grafik PnL per wallet,
dan cek wallet ke **LP Agent**. Tampilannya tema cockpit mecha (logo V-fin + range
bar, aksen emas, heatmap biru = lebih bagus / merah = lebih jelek).

Semua angka diambil langsung dari chain lewat RPC lu (SolidRPC / Alchemy / dll),
nggak pakai API pihak ketiga. Cuma butuh Python 3.10+ (standard library aja, nggak
perlu `pip install`).

## Cara pakai (Mac)

1. Ambil folder ini (`git pull`, atau clone `https://github.com/Bojjifomo/Capstone1`).
2. Double-click **`rh_wallet_finder/Buka Wallet Finder.command`**
   (atau dari Terminal: `python3 rh_wallet_finder/finder.py`).
   Browser kebuka ke **http://127.0.0.1:4664**.
3. Klik **⚙ Settings**, paste link RPC Robinhood Chain lu, misalnya:
   `https://rpc.solidrpc.io/ak_…/evm/4663` → Save.
   Link + key disimpen di `rh_wallet_finder/.env` di laptop lu doang
   (file itu di-gitignore, **jangan** di-commit: repo ini public).
4. Biarin jendela Terminal kebuka. Sync jalan di background:
   - mulai dari blok terbaru, mundur sampai 30 hari (`HISTORY` di rail atas),
   - tiap beberapa segmen langsung di-score, jadi tabel udah keisi sebelum
     backfill 30 hari selesai,
   - abis itu update sendiri tiap 10 menit (atau klik **Sync now**).

Kalau `.command` diblok Gatekeeper: klik kanan → Open, sekali aja.

Dari Terminal juga bisa simpen RPC-nya + langsung dicek chain id-nya:

```bash
python3 rh_wallet_finder/finder.py set-rpc 'https://rpc.solidrpc.io/ak_…/evm/4663'
```

## Isi dashboard

| Bagian | Isinya |
| --- | --- |
| Wallets / Ranked / Positions / Styles | Wallet di database / yang udah punya skor / posisi V4 yang ke-index / jumlah style LP |
| Rail atas `SYNC` / `RANK` / `HISTORY` / `ETH` | Kapan terakhir sync & ranking (kuning = udah lama), history yang udah ke-load, harga ETH |
| Tab **Board** | Papan utama, window 30 hari |
| Tab **Last 24h** | Sama, tapi cuma posisi yang ditutup 24 jam terakhir |
| Tab **Twin finder** | Paste wallet → wallet lain yang gaya LP-nya paling mirip |
| Tab **Watchlist** | Wallet yang di-★ / di-import (bisa paste sheet "LP Agent Wallets - Robinhood Chain" langsung) |
| Switch **Real PnL** | Tiap posisi dipotong biaya jual balik token yang dia balikin (fee pool + slippage), terus ranking diulang |
| Chip preset | `Followable winners`, `Low drawdown`, `Size ≥ $1K`, `Active 24h`, `Uncrowded`, plus **+ Save current** buat nyimpen filter + sort sendiri |
| ◆ di header kolom | Filter min/max (atau teks) per kolom; klik judul kolom buat sort |
| Klik wallet | **Wallet dossier**: grafik realized PnL, semua posisi, label/notes, **LP Agent check** |
| Kolom `LP Agent` / `Explorer` | Buka wallet di LP Agent / Blockscout |

Penjelasan tiap kolom + arti warna ada di tombol **?** (Field guide) di pojok kanan atas.

## LP Agent

- Tiap baris ada link `LP Agent ↗` → `app.lpagent.io/portfolio?address=…&chain=ROBINHOOD`.
- Kalau lu punya **API key LP Agent** (dari LP Agent portal), isi di ⚙ Settings.
  Di wallet dossier, tombol **Check now** narik angka LP Agent sendiri
  (`/lp-positions/overview` + `/lp-positions/opening`, uniswap_v4 & v3) dan
  nampilin di sebelah angka kita. Key cuma dipake server lokal, nggak pernah
  dikirim ke browser.
- Dari Terminal: `python3 rh_wallet_finder/finder.py check 0xWALLET`

## Command line

```bash
cd rh_wallet_finder
python3 finder.py                 # dashboard + sync background (default)
python3 finder.py sync --days 7   # load history sekali jalan
python3 finder.py score           # hitung harga posisi + ranking sekali jalan
python3 finder.py top -n 30       # top wallet di terminal (--real, --1d)
python3 finder.py check 0x…       # satu wallet + LP Agent
python3 finder.py import wallets.txt
```

Database-nya `rh_wallet_finder/data/wallets.db` (SQLite). Bisa dibuka pakai
DB Browser for SQLite / pandas kalau mau olah sendiri. Tabel: `wallets`,
`positions`, `events`, `transfers`, `pools`, `tokens`, `labels`.

## Cara kerjanya

1. **Discovery** (`indexer.py`): satu filter `eth_getLogs` buat
   `ModifyLiquidity` dari PoolManager V4 (`0x8366…0951`) dan `Transfer` NFT dari
   PositionManager (`0x58da…a7`). Mint = buka posisi, burn = tutup, pemilik NFT
   = wallet. Ukuran range `eth_getLogs` nyesuaiin sendiri sama limit RPC lu.
2. **Scoring** (`scorer.py`): tiap event LP dihargai pakai state chain 1 blok
   sebelumnya (archive `eth_call` lewat Multicall3): harga pool, likuiditas, dan
   fee growth posisi itu dari StateView. Jumlah token = dari liquidity delta;
   fee = yang posisi itu dapet sejak checkpoint terakhir. Harga ETH dari pool
   ETH/USDG. Jadi PnL = semua yang ditarik + fee + nilai sekarang (kalau masih
   open) − semua yang disetor, dalam USD.
3. **Ranking** (`metrics.py`): metrik per wallet → skor 0–100 (gabungan
   persentil Net, ROI, win rate, daily, drawdown, recovery, fee, jumlah sampel).
   Wallet rugi maksimal 45, sampel kecil ditarik ke 50.

Biar RPC nggak jebol, tiap run cuma menghargai `SCORE_POSITIONS_PER_RUN`
posisi (default 4000), maksimal 150 posisi terbaru per wallet. Wallet yang
di-★ diprioritasin.

## Batasan

- Cuma posisi Uniswap V4 yang lewat PositionManager resmi (NFT). V3 dan LP
  manager / vault lain belum.
- Pool yang nggak punya USDG atau ETH/WETH di salah satu sisi nggak bisa
  dihargain (dilewatin).
- Posisi yang dibuka sebelum awal history kita nggak dihitung PnL-nya (setoran
  awalnya nggak ketahuan). Makanya `Age` bisa nampil `>14d`.
- Harga pakai state 1 blok sebelum transaksi (~0.1 detik), jadi bisa beda
  dikit dari harga persis di dalam tx.
- RPC harus support **archive** `eth_call` (state blok lama) dan
  `eth_getLogs`. Alchemy free tier cuma ngizinin `eth_getLogs` 10 blok per
  request, jadi buat backfill 30 hari itu kelamaan; RPC berbayar (kayak
  SolidRPC lu) jauh lebih cepet.

## Tes

```bash
python3 -m unittest discover -s rh_wallet_finder/tests
```
