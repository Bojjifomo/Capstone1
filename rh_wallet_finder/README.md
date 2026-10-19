# RH Wallet Finder

Database wallet LP **Uniswap V4 di Robinhood Chain** + app **Streamlit** buat nyari
top wallet, dibagi dua kelas: **Multiday player** dan **Degen player**, masing-masing
punya papan + skor sendiri. Isinya: skor & tier, realized / unrealized PnL, ROI/bulan, win rate, profit factor, drawdown,
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
| Wallets / Multiday / Degen / Positions | Wallet di database (+ yang udah ranked) / jumlah multiday player / degen player / posisi V4 yang ke-index |
| Rail atas `WORKER` / `SYNC` / `RANK` / `HISTORY` / `ETH` | Status sync di background, kapan terakhir sync & ranking (kuning = udah lama), history yang udah ke-load, harga ETH |
| Tab **Multiday** | Wallet yang trade-nya ditahan berjam-jam sampai berhari-hari (median hold ≥ 2 jam). Diranking sesama multiday, posisi yang masih open ikut dihitung (Total PnL) |
| Tab **Degen** | Wallet yang keluar-masuk cepet (median hold < 2 jam). Diranking sesama degen, pakai realized PnL + profit factor. Bot (hold < 5 menit / > 100 trade sehari) disembunyiin, centang **Show bots** di Filters kalau mau liat |
| Tab **Last 24h** | Dua kelas campur, cuma posisi yang ditutup 24 jam terakhir (ada kolom Class) |
| Tab **Twin finder** | Pilih / paste wallet → wallet lain yang gaya LP-nya paling mirip |
| Tab **Watchlist** | Wallet yang di-★ / di-import (bisa paste sheet "LP Agent Wallets - Robinhood Chain" langsung); label, notes, ★, Reference bisa diedit langsung di tabel |
| Tab **Settings** | RPC, API key LP Agent, window performa, batas idle, batas multiday, slippage, interval sync, tombol **Sync now**, log worker |
| Toggle **Real PnL** | Tiap posisi dipotong biaya jual balik token yang dia balikin (fee pool + slippage), terus ranking diulang |
| Preset + **Filters** | Per papan sendiri-sendiri: `Followable winners`, `Low drawdown`, `Size ≥ $1K`, `Active 24h`, `Idle wallets too`, `Uncrowded`, `Ladders`, `Smart LP top 10`, `Watchlist only`; filter sendiri bisa di-save jadi preset |
| Klik judul kolom | Sort; arahin mouse ke judul buat penjelasan kolomnya |
| Centang satu baris | **Wallet dossier**: kelas + seberapa cocok, grafik realized PnL, label/notes/★/Reference, ranking Smart LP Finder, **LP Agent check**, semua posisi (ladder ditandain) |
| Kolom `LP Agent` / `Explorer` | Buka wallet di LP Agent / Blockscout |

Penjelasan tiap kolom + arti warna juga ada di **Field guide** di bawah tabel Multiday / Degen.

## Multiday vs Degen

Dari dua wallet contoh lu (dicek on-chain):

| | Multiday `0xb1e4…8947` | Degen `0x5784…4ffd` |
| --- | --- | --- |
| Median hold per trade | ±6,4 jam (70% > 4 jam) | ±30 menit |
| Cara buka | Ladder 5 range sekaligus, USDG doang di bawah harga | 1 range, USDG doang di bawah harga |
| Trade per hari | ±9 | ±11 |

Bedanya bukan di token atau sisi (dua-duanya pasang USDG doang di memecoin $1–10M),
tapi di **berapa lama trade-nya ditahan**. Jadi aturannya: median hold trade
≥ `MULTIDAY_HOURS` (default 2 jam, bisa diubah di Settings) = **Multiday**, di
bawahnya = **Degen**. Trade yang masih open dan udah lebih lama dari batas itu
ikut dihitung. Di data 8 hari terakhir (2.109 wallet yang punya ≥ 3 trade
closed): ±300 multiday, ±1.000 degen, ±800 bot.

- **Ranking per kelas**: skor 0–100 cuma dibandingin sesama kelasnya.
  Multiday: total PnL (open ikut), ROI, green days, max DD, win rate, fee,
  recovery, jumlah trade. Degen: realized PnL, ROI, profit factor, win rate,
  green days, max DD, fee, jumlah trade.
- **Reference wallet** (◆): dua wallet contoh di atas otomatis jadi reference
  kelasnya. Kolom **Like ref** = seberapa mirip gaya LP tiap wallet sama
  reference kelasnya. Wallet mana aja bisa dijadiin reference di dossier
  (atau kolom Reference di Watchlist); reference juga ngunci kelasnya.
- **Cuma performa 30 hari**: semua angka dari 30 hari terakhir
  (`METRIC_DAYS`); wallet yang di 30 hari itu nggak punya ≥ 3 posisi closed
  nggak ditampilin sama sekali. Wallet yang nggak ada aksi LP 7 hari terakhir
  (`ACTIVE_DAYS`) juga disembunyiin dari papan (Filters → Active within, atau
  preset `Idle wallets too` buat liat lagi).

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
python3 finder.py top --kind multiday -n 30   # top wallet di terminal (--kind degen, --real, --1d)
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
3. **Ranking** (`metrics.py`): posisi yang dibuka di satu transaksi (pool +
   blok sama, misal ladder 5 range) = **satu trade**. Win rate, size, hold,
   jumlah trade dan pace dihitung per trade; trade yang hasilnya ±0,05% dari
   setorannya (ladder yang nggak kesentuh harga) = flat, nggak dihitung menang
   atau kalah. PnL, fee dan grafik tetap jumlah semua posisi. Tiap wallet masuk
   satu kelas (Multiday / Degen), terus skor 0–100 = gabungan persentil di dalam
   kelasnya. Wallet rugi maksimal 45, trade sedikit ditarik ke 50. ROI dihitung
   dari hari wallet itu aktif (wallet umur 3 hari nggak dianggep nganggur 27 hari).

Biar RPC nggak jebol, tiap run cuma menghargai `SCORE_POSITIONS_PER_RUN`
posisi (default 4000), maksimal 500 posisi terbaru per wallet (cukup buat
sebulan wallet yang aktif). Urutannya: wallet di Watchlist / reference, wallet
dari Smart LP Finder, wallet orang, terus **bot paling belakang** (dikenalin
dari jam buka-tutupnya aja, sebelum dihargai, dan cuma 20 posisi terbarunya).
Wallet yang posisi lamanya belum kehargai diukur dari posisi tertua yang udah
(ada keterangannya di dossier).

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
