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
3. Buka tab **Settings**, paste link RPC Robinhood Chain lu di **RPC 1**, misalnya
   `https://rpc.solidrpc.io/ak_…/evm/4663` → Save. Disarankan: **Alchemy API
   key** lu di **RPC 2** (cukup key-nya; link-nya dibikin sendiri). Dua RPC itu
   jalan barengan: hitung harga dibagi rata ke keduanya, dan yang lagi kena
   rate limit (HTTP 429) atau kuotanya abis istirahat dulu sementara yang satu
   lanjut; sync tetap di RPC 1 (range blok-nya gede) dan pindah ke RPC 2 cuma
   pas RPC 1 lagi istirahat. (Kalau folder RH Smart LP Finder ada di sebelahnya,
   langkah ini bisa dilewat: RPC-nya diambil dari sana.)
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

**Abis `git pull`, buka lagi `Buka Wallet Finder.command`.** Kalau versi lama
masih jalan (dari folder ini), dia dimatiin dulu otomatis, terus yang baru
nyala di port yang sama; port yang dipakai program lain dilewatin ke port
berikutnya. Sync jalan di background proses yang sama, jadi tanpa restart dia
masih pakai kode lama (app nampilin catatan kuning kalau ranking-nya masih dari
versi lama). Pas nyala, ranking langsung diulang pakai aturan baru dari data
yang udah dihargai, sebelum sync jalan.

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
| Tab **vs RH Discovery** | Paste baris dari tabel RH Discovery → tiap wallet dicocokin ke wallet kita, angkanya dijejerin (✓ / ✗). Lihat [Cek vs RH Discovery](#cek-vs-rh-discovery) |
| Tab **Settings** | RPC, API key LP Agent, window performa (30 hari), batas wallet idle (14 hari), batas multiday, slippage, interval sync, tombol **Sync now**, log worker |
| Toggle **Real PnL** | Tiap posisi dipotong biaya jual balik token yang dia balikin (fee pool + slippage), terus ranking diulang |
| Strip status | `X ranked · Y rated of Z found · N positions in queue · ranked … ago`: wallet yang ke-ranking, yang udah punya posisi kehargai, yang ketemu, dan posisi yang masih antre dihargai |
| **Filter bar** (selalu kelihatan) | Search, Min closed, Active within, Min/Max avg size, Min net worth, Min ROI 30D, 7D/30D, Min USDG %, Min win, Max open loss, Max drawdown, Min tier, Followable, Style + centang `30D profit only`, `7D profit only`, `USDG pairs only`, `Watchlist only`, `Smart LP top 10`, `Show bots`. Di bawahnya: "Showing X of Y · Z hidden by filters", tombol **Clear filters** |
| Preset | Per papan sendiri-sendiri: `Proven earners` (≥ 30 trade closed, aktif 7 hari, avg size ≥ $300, net worth ≥ $1k, profit 30D, USDG doang), `Hot this week`, `Followable winners`, `Low drawdown`, `Size $1k+`, `Ladders`, `Smart LP top 10`, `Watchlist only`; filter sendiri bisa di-save jadi preset |
| Kolom performa | `Net worth`, `PnL 30D`, `ROI 30D`, `PnL 7D`, `7D/30D` (porsi PnL 30 hari yang dibikin 7 hari terakhir: ±0,23 = rata, tinggi = lagi panas, ~0 = udah sepi), `PnL 24h`, `Avg / month`, `PnL 3M`, `PnL ALL`, `ROI ALL`, `Open PnL`, `Total 30D`, `Win`, `Trade win`, `Profit factor`, `Capital ROI 30D`, `ROI/mo`, `Green days`, `Fees 30D`, `Max DD`, `USDG %`. Definisinya sama kayak RH Discovery (lihat di bawah) |
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
  (`METRIC_DAYS`, kolomnya ditulis `Realized 30d`, `Total 30d`, `Fees 30d`);
  wallet yang di 30 hari itu nggak punya ≥ 3 posisi closed nggak masuk list.
- **Wallet nggak aktif dibuang**: wallet yang nggak ada aksi LP (buka / tambah /
  tarik / tutup) lebih dari 14 hari (`ACTIVE_DAYS`, ubah di Settings) nggak
  di-ranking dan nggak ditampilin sama sekali. Mau lebih ketat: Filters →
  Active within, atau preset `Active 24h` / `Active 3 days`.

## Cek vs RH Discovery

RH Discovery dianggap patokan, jadi kolom yang namanya sama sekarang dihitung
dengan cara yang sama:

| Kolom | Artinya (di kita = di RH Discovery) |
| --- | --- |
| `PnL 30D` / `PnL 7D` / `PnL 3M` / `PnL ALL` | Realized PnL posisi yang ditutup di rentang itu: semua yang ditarik + fee − semua yang disetor (USD saat kejadian) |
| `Net worth` | ETH + USDG di wallet + nilai posisi yang masih open |
| `ROI 30D` | PnL 30D ÷ net worth sekarang (wallet yang udah narik profitnya keluar kebaca tinggi, misal +4140%) |
| `ROI ALL` | PnL ALL ÷ total yang pernah disetor ke posisi-posisi itu |
| `Avg / month` | PnL ALL ÷ jumlah bulan kalender sejak LP pertama (14 Agu → Okt = 3) |
| `Win` | Posisi yang untung ÷ posisi yang untung atau rugi (range yang nggak kesentuh harga = flat, nggak dihitung) |
| `Open PnL` | Nilai posisi open + fee yang belum diklaim − setorannya |

Ukuran kita yang lama tetap ada dengan nama lain: `Trade win` (ladder dihitung
satu trade, ini yang dipakai skor), `Capital ROI 30D` (PnL ÷ modal yang
beneran kepake), `ROI/mo`.

**Cara ngecek**: buka RH Discovery, blok baris-baris tabelnya (header boleh
ikut), ⌘C, terus paste di tab **vs RH Discovery**. Tiap wallet (`0x74a9…181b`)
dicocokin ke alamat lengkap di database kita, terus tiap angka dijejerin:
✓ = masih dalam toleransi (PnL ±10%, Win ±3 poin, Net worth ±15%), ✗ = lebih
jauh. Wallet yang nggak ke-ranking dikasih alasannya (belum ada di data kita,
idle > 14 hari, kurang posisi closed, masih antre dihargai). Dari terminal:
`python3 finder.py compare baris.txt` (atau `-` terus paste).

Hasil cek pertama (screenshot RH Discovery 22 Okt vs data kita):

- `0x5784…4ffd` (contoh degen): PnL 30D $3.957 vs $3.858, PnL ALL $8.853 vs
  $8.521, ROI ALL 1,68% vs 1,6%, Win per posisi 89,3% vs 87,8% → cocok (beda
  3 hari datanya). Net worth juga definisinya sama: 22 dari 22 wallet yang
  nggak punya posisi open, ETH + USDG-nya = net worth RH Discovery (±6%).
- **Bug yang ketemu**: dulu cuma 500 posisi terbaru per wallet yang dihargai.
  7 dari 52 wallet top RH Discovery buka 800–6.000 posisi sebulan (ladder),
  jadi PnL 30D mereka di kita cuma kehitung beberapa hari terakhir. Sekarang
  semua posisi di window dihargai (urut: 20 terbaru tiap wallet, 200, 1000,
  sisanya), dan event yang di blok yang sama dibaca dalam **satu** `eth_call`
  (ladder 7 range = 1 call, bukan 7), jadi RPC-nya malah lebih hemat.
- `PnL ALL` / `Avg / month` RH Discovery mulai dari launch chain (1 Jul 2026).
  Punya kita cuma sejauh `History to load` di Settings (default 30 hari): set
  ke jumlah hari sejak 1 Juli (tooltip-nya ngasih angkanya) kalau mau sama.
  Posisi lama dihargai setelah posisi di window beres (strip status:
  "older ones for PnL ALL").

## Etalase web di Vercel (bisa dibuka orang lain)

Mac lu tetap jadi mesinnya (sync, harga, ranking). Tiap beberapa menit worker
upload **snapshot read-only** board-nya ke Vercel Blob, dan halaman di
`web/` nampilin itu: tab Multiday / Degen / Last 24h, Real PnL, filter bar,
preset, tabel heatmap, sortir per kolom, detail wallet, link LP Agent &
Explorer. Halamannya refresh sendiri tiap 2 menit; kalau snapshot-nya udah
lebih dari 30 menit, muncul catatan "scanner may be offline".

**Yang keluar cuma data publik dari chain**: alamat wallet + angka-angkanya.
Label, notes, watchlist, hasil LP Agent, ranking RH Smart LP Finder, dan semua
key/RPC tetap di Mac.

Setup sekali:

1. `git pull` di Mac, terus buka lagi `Buka Wallet Finder.command`.
2. Di [vercel.com](https://vercel.com): **Add New → Project** → import repo
   `Bojjifomo/Capstone1`.
   - **Root Directory**: `rh_wallet_finder/web`
   - Framework Preset: **Other** (nggak ada build step) → **Deploy**.
3. Project itu → **Storage → Create → Blob** → bikin store (akses **Private**
   atau Public, dua-duanya jalan: private lebih aman, file snapshot-nya cuma
   kebaca lewat web lu), **Connect** ke project (Production + Preview). Web-nya
   baca dan nulis store itu pakai credential OIDC bawaan Vercel
   (`BLOB_STORE_ID`), jadi nggak ada token Blob yang perlu disalin.
4. Project itu → **Settings → Environment Variables** → tambah `PUBLISH_KEY`
   (Production), isinya kata sandi acak yang panjang (32+ karakter), terus
   **Redeploy**. Di Wallet Finder → **Settings → Web showcase · publish link**
   paste `https://<nama-project>.vercel.app/api/publish?key=<PUBLISH_KEY>` →
   Save → klik **Publish now**. Mac upload ke web lu sendiri (`/api/publish`),
   dan web itu yang nyimpen ke Blob. (Token Blob dari tab `.env.local` di
   dashboard Vercel sekarang kedaluwarsa sendiri, makanya nggak dipakai lagi;
   kalau masih ada di Settings, publish link yang didahulukan.)
5. Buka `https://<nama-project>.vercel.app`, share link-nya.

Upload otomatis maksimal sekali tiap `PUBLISH_MINUTES` (default 30 menit, ubah
di Settings) selama app jalan: tiap upload itu satu operasi tulis ke Blob, jadi nggak boros
kuota paket gratis Vercel. Dari Terminal: `python3 finder.py publish` (upload
sekarang) atau `python3 finder.py publish --out board.json` (simpen file-nya
aja). Selama Mac mati, halaman tetap kebuka dengan snapshot terakhir.

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
python3 finder.py publish         # upload board ke etalase web (Vercel), atau --out file.json
python3 finder.py compare baris.txt  # angka kita vs baris yang dicopy dari RH Discovery (- = paste)
python3 finder.py status          # state sync / ranking / RPC, JSON
python3 finder.py top --json      # top wallet sebagai JSON (check 0x… --json juga bisa)
python3 finder.py twin 0x…        # wallet yang gaya LP-nya paling mirip, JSON
python3 finder.py smart-lp        # pool Smart LP Finder + peringkat terbaik vs skor kita, JSON
python3 finder.py web-check       # snapshot yang tayang di web vs publish terakhir, JSON
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
   fee growth posisi itu dari StateView. Semua event di blok yang sama dibaca
   dalam satu `eth_call`. Jumlah token = dari liquidity delta;
   fee = yang posisi itu dapet sejak checkpoint terakhir. Harga ETH dari pool
   ETH/USDG. Jadi PnL = semua yang ditarik + fee + nilai sekarang (kalau masih
   open) − semua yang disetor, dalam USD.
3. **Ranking** (`metrics.py`): posisi yang dibuka di satu transaksi (pool +
   blok sama, misal ladder 5 range) = **satu trade**. Trade win, size, hold,
   jumlah trade dan pace dihitung per trade; trade yang hasilnya ±0,05% dari
   setorannya (ladder yang nggak kesentuh harga) = flat, nggak dihitung menang
   atau kalah. PnL, fee, grafik dan `Win` (cara RH Discovery) tetap per posisi. Tiap wallet masuk
   satu kelas (Multiday / Degen), terus skor 0–100 = gabungan persentil di dalam
   kelasnya. Wallet rugi maksimal 45, trade sedikit ditarik ke 50. ROI dihitung
   dari hari wallet itu aktif (wallet umur 3 hari nggak dianggep nganggur 27 hari).

### Hemat RPC

Yang paling makan kuota itu *archive* `eth_call` buat ngehargain posisi (satu
call per blok, semua event di blok itu sekalian). Biar irit tapi angka di board
tetap akurat dan update:

1. **Sampel dulu, lengkap belakangan.** Tiap wallet dihargai dulu
   trade-trade yang paling baru ditutup: minimal 20 posisi dari 3+ trade (satu
   ladder = satu trade, maks 150 posisi). Cuma wallet yang **menjanjikan**
   yang dihargai sampai satu window penuh (200 → 1000 → semua): wallet di
   Watchlist / reference, atau yang sampelnya ruginya < 1% dengan rata-rata ≥
   $50 per trade (ladder yang nggak kesentuh harga hasilnya 0, jadi tetap
   lolos). Diuji ke data asli: nggak ada wallet yang untung yang ketahan di
   sampel, tapi hampir separuh wallet berhenti di sampel. Wallet yang nanti
   mulai cuan otomatis naik kelas (trade barunya selalu dihargai).
2. **Yang cuma geser nilainya nggak dibaca tiap putaran.** Nilai posisi open
   dibaca ulang tiap 30 menit (cuma wallet yang ada di board), saldo wallet dan
   harga token tiap 1 jam. Putaran yang nggak ada posisi baru = 0 call.
3. **History lama** (buat `PnL ALL`) cuma buat wallet yang skornya Decent ke atas.
4. **Batas harian** (Settings → *RPC calls per day*, 0 = bebas): kalau
   kena, harga berhenti dulu sampai 00:00 UTC; sync & board tetap jalan, dan
   yang paling penting selalu dihargai duluan. Patokan: Alchemy gratis ±30 juta
   CU/bulan ≈ 35.000 call/hari.
5. **Pilih RPC yang kuotanya gede** (Settings → *With two RPCs, price reads go
   to*): `RPC 1 (RPC 2 as backup)` = SolidRPC yang kerja, Alchemy cuma
   gantiin pas SolidRPC istirahat / habis kuota. `eth_getLogs` tetap ke RPC 1.

Log worker nulis pemakaiannya tiap putaran (`RPC calls this pass: prices … ·
open values … · balances …`), dan strip status di atas board nunjukin `RPC
calls today`.

## Batasan

- Cuma posisi Uniswap V4 yang lewat PositionManager resmi (NFT). V3 dan LP
  manager / vault lain belum.
- Pool yang nggak punya USDG atau ETH/WETH di salah satu sisi nggak bisa
  dihargain (dilewatin).
- Posisi yang dibuka sebelum awal history kita nggak dihitung PnL-nya (setoran
  awalnya nggak ketahuan). Makanya `Age` bisa nampil `>14d`.
- Harga pakai state 1 blok sebelum transaksi (~0.1 detik), jadi bisa beda
  dikit dari harga persis di dalam tx.
- Pool yang likuiditasnya kesedot habis bisa nyangkut di harga nggak masuk
  akal (misal $10⁵⁰ per token). Harga token > $10 juta atau market cap > $10
  miliar, dan posisi yang balik > 1000× setorannya, dianggap nggak bisa
  dihargai dan nggak dihitung.
- Kalau salah satu RPC kehabisan kuota bulanan (misal Alchemy), dia istirahat
  sejam dan yang satunya lanjut sendiri; ada catatan kuning di atas. Cuma kalau
  dua-duanya kena limit barengan, app nunggu (sampai ±10 menit); kalau masih,
  kurangin "Parallel RPC requests" di Settings. Dengan satu RPC aja, limit per
  detik ditungguin sampai ±5 menit.
- RPC harus support **archive** `eth_call` (state blok lama) dan
  `eth_getLogs`. Alchemy free tier cuma ngizinin `eth_getLogs` 10 blok per
  request, jadi buat backfill 30 hari itu kelamaan; RPC berbayar (kayak
  SolidRPC lu) jauh lebih cepet.

## Tes

```bash
python3 -m unittest discover -s rh_wallet_finder/tests
```
