# RH Wallet Finder

Database wallet LP **Uniswap V4 di Robinhood Chain** + app **Streamlit** buat nyari
top wallet: skor & tier, realized / unrealized PnL, ROI/bulan, win rate, drawdown,
copier, style, Real PnL, preset filter, papan 24 jam, twin finder, grafik PnL per
wallet, dan cek wallet ke **LP Agent**. Tema cockpit mecha (logo V-fin + range bar,
aksen emas, heatmap biru = lebih bagus / merah = lebih jelek).

Semua angka diambil langsung dari chain lewat RPC lu (SolidRPC / Alchemy / dll),
nggak pakai API pihak ketiga. Butuh Python 3.10+ dan Streamlit
(`pip install -r requirements.txt`); sync & scoring sendiri cuma pakai standard library.

## Jalanin di Mac (disarankan)

Jalanin di Mac, bukan di Streamlit Cloud: sync harus jalan terus, database SQLite
harus awet (Cloud ngehapus disk tiap restart dan tidur kalau nggak dibuka), dan
integrasi RH Smart LP Finder cuma bisa lokal.

1. Ambil folder ini (`git pull`, atau clone `https://github.com/Bojjifomo/Capstone1`).
2. Double-click **`rh_wallet_finder/Buka Wallet Finder.command`**.
   Pertama kali dia install Streamlit sendiri, terus browser kebuka ke
   **http://localhost:8501**. Dari Terminal sama aja:
   ```bash
   cd rh_wallet_finder
   python3 -m pip install -r requirements.txt   # sekali aja
   caffeinate -i python3 finder.py              # caffeinate = Mac nggak sleep
   ```
3. Buka tab **Settings**, paste link RPC Robinhood Chain lu, misalnya
   `https://rpc.solidrpc.io/ak_…/evm/4663` → Save. Opsional: link Alchemy di
   "Calls RPC" buat `eth_call` blok lama. (Kalau folder RH Smart LP Finder ada di
   sebelahnya, langkah ini bisa dilewat: RPC-nya diambil dari sana.)
   Link + key disimpen di `rh_wallet_finder/.env` di Mac lu doang
   (file itu di-gitignore, **jangan** di-commit: repo ini public).
4. Biarin jendela Terminal kebuka. Sync jalan di background, di proses yang sama:
   - mulai dari blok terbaru, mundur sampai 30 hari (`HISTORY` di rail atas),
   - tiap beberapa segmen langsung di-score, jadi tabel udah keisi sebelum
     backfill 30 hari selesai,
   - abis itu update sendiri tiap 5 menit (ubah di Settings, atau klik **Sync now**).
   - **Live:** halaman ngecek status tiap 15 detik dan gambar ulang tabelnya sendiri
     begitu ada ranking baru; nggak perlu refresh browser. Tab browser boleh ditutup,
     sync tetap jalan selama Terminal-nya hidup.

Kalau `.command` diblok Gatekeeper: klik kanan → Open, sekali aja.

Dari Terminal juga bisa simpen RPC-nya + langsung dicek chain id-nya:

```bash
python3 rh_wallet_finder/finder.py set-rpc 'https://rpc.solidrpc.io/ak_…/evm/4663'
```

## Isi app

| Bagian | Isinya |
| --- | --- |
| Wallets / Ranked / Positions / Styles | Wallet di database / yang udah punya skor / posisi V4 yang ke-index / jumlah style LP |
| Rail atas `WORKER` / `SYNC` / `RANK` / `HISTORY` / `ETH` | Status sync di background, kapan terakhir sync & ranking (kuning = udah lama), history yang udah ke-load, harga ETH |
| Tab **Board** | Papan utama, window 30 hari |
| Tab **Last 24h** | Sama, tapi cuma posisi yang ditutup 24 jam terakhir |
| Tab **Twin finder** | Pilih / paste wallet → wallet lain yang gaya LP-nya paling mirip |
| Tab **Watchlist** | Wallet yang di-★ / di-import (bisa paste sheet "LP Agent Wallets - Robinhood Chain" langsung); label, notes, ★ bisa diedit langsung di tabel |
| Tab **Settings** | RPC, API key LP Agent, window, slippage, interval sync, tombol **Sync now**, log worker |
| Toggle **Real PnL** | Tiap posisi dipotong biaya jual balik token yang dia balikin (fee pool + slippage), terus ranking diulang |
| Preset + **Filters** | `Followable winners`, `Low drawdown`, `Size ≥ $1K`, `Active 24h`, `Uncrowded`, `Smart LP top 10`, `Watchlist only`; filter sendiri bisa di-save jadi preset |
| Klik judul kolom | Sort; arahin mouse ke judul buat penjelasan kolomnya |
| Centang satu baris | **Wallet dossier**: grafik realized PnL, label/notes/★, ranking Smart LP Finder, **LP Agent check**, semua posisi |
| Kolom `LP Agent` / `Explorer` | Buka wallet di LP Agent / Blockscout |

Penjelasan tiap kolom + arti warna juga ada di **Field guide** di bawah tabel Board.

## Nyambung ke RH Smart LP Finder

Kalau folder **RH Smart LP Finder** lu taruh di sebelah folder ini (sama-sama di
dalam `Capstone1/`, kayak di Finder lu sekarang), tool ini otomatis:

- **pakai RPC-nya** kalau di sini belum diisi: SolidRPC (`RH_RPC_BACKUP1_LP_FINDER`)
  buat `eth_getLogs`, Alchemy (`RH_RPC_MAIN_LP_FINDER`) buat `eth_call` blok lama.
  Jadi nggak perlu isi tab Settings lagi.
- **ambil saved wallets** dari tabel `smart_lp_wallets` → masuk Watchlist (di-★,
  labelnya ikut; label yang udah lu tulis di sini nggak ditimpa).
- **ambil hasil ranking per pool** yang pernah lu jalanin di sana
  (`pool_lp_finder_cache`) → kolom **Smart LP** (`#3/76 · VRAX/USDG` = peringkat
  terbaik wallet itu di satu pool), preset **Smart LP top 10**, dan tabel
  "In RH Smart LP Finder rankings" di wallet dossier. Wallet-wallet ini juga
  dihitung duluan pas scoring.
- link **SMART LP open ↗** di rail atas buka tool itu (`localhost:8787`).

Database Smart LP Finder cuma dibaca dari salinan sementara, file aslinya nggak
diubah dan nggak kekunci walaupun servernya lagi jalan. Diulang tiap siklus sync,
jadi wallet yang baru lu save di sana nongol sendiri. Manual:
`python3 rh_wallet_finder/finder.py import-smart-lp` (atau `--db path/ke/lp-finder.sqlite`).
Folder di tempat lain: set `SMART_LP_FINDER_DIR=/path/ke/RH Smart LP Finder`.

Bedanya dua tool ini: Smart LP Finder = **satu pool**, dihitung on-demand pas lu
klik; Wallet Finder = **semua pool sekaligus**, jalan terus di background dan
nyimpen hasilnya di database.

## LP Agent

- Tiap baris ada link `LP Agent ↗` → `app.lpagent.io/portfolio?address=…&chain=ROBINHOOD`.
- Kalau lu punya **API key LP Agent** (dari LP Agent portal), isi di tab Settings.
  Di wallet dossier, tombol **Check now** narik angka LP Agent sendiri
  (`/lp-positions/overview` + `/lp-positions/opening`, uniswap_v4 & v3) dan
  nampilin di sebelah angka kita. Key cuma dipake di sisi Python, nggak pernah
  dikirim ke browser.
- Dari Terminal: `python3 rh_wallet_finder/finder.py check 0xWALLET`

## Command line

```bash
cd rh_wallet_finder
python3 finder.py                 # app Streamlit + sync background (default)
python3 finder.py worker          # sync + scoring doang, tanpa UI
python3 finder.py sync --days 7   # load history sekali jalan
python3 finder.py score           # hitung harga posisi + ranking sekali jalan
python3 finder.py top -n 30       # top wallet di terminal (--real, --1d)
python3 finder.py check 0x…       # satu wallet + LP Agent
python3 finder.py import wallets.txt
python3 finder.py import-smart-lp # saved wallets + ranking dari RH Smart LP Finder
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
