# DLMM Terminal: filter entry + auto exit (Solana LP)

Implementasi **DLMM-Auto-Entry-Spec v1** (133 posisi closed, 23 token, Oct 25 – Nov 4 2026).
Dua bagian:

1. **`screen`**: filter pool Meteora DLMM SOL: 4 gate (wajib lolos) + 4 flag (cuma warning). Semua kandidat ke-log, termasuk yang di-skip.
2. **`monitor`**: tiap 5 menit baca PnL posisi live dari wallet lu (API Meteora, 0 RPC), log ke file, kirim alert, dan jalanin exit rule. Mode `auto` nutup posisinya sendiri (remove 100% + claim + close) lewat SDK Meteora.

Stdlib Python doang buat filter + monitor. Node cuma buat executor close.

## Pakai

```bash
python3 -m dlmm_terminal screen                    # scan semua pool SOL, tampil yang lolos gate
python3 -m dlmm_terminal screen --all              # + yang di-skip dan alasannya
python3 -m dlmm_terminal screen --pool <POOL> --size 8
python3 -m dlmm_terminal decide <POOL> enter --reason "fee tinggi, no flag"
python3 -m dlmm_terminal monitor --wallet <WALLET>             # mode alert (default)
python3 -m dlmm_terminal monitor --wallet <WALLET> --mode auto --dry-run   # simulasi close, nggak kirim tx
python3 -m dlmm_terminal followup                  # isi harga 1h/6h/24h setelah tiap kandidat (buat v2)
python3 -m unittest dlmm_terminal.tests.test_dlmm_terminal
```

Mode auto beneran (kirim tx):

```bash
cd dlmm_terminal/executor && npm install
```

`.env` (di `dlmm_terminal/` atau root repo):

```
DLMM_WALLET=...                 # default --wallet
SOLANA_RPC_URL=https://...      # buat executor
DLMM_KEYPAIR=/path/keypair.json # cuma buat --mode auto tanpa --dry-run
DLMM_TG_BOT_TOKEN=...           # opsional: alert ke Telegram
DLMM_TG_CHAT_ID=...
```

Semua angka ada di `config.json`. Bankroll default 160 SOL × loss budget 5% → max posisi **8.89 SOL** (loss budget ÷ 0.9).

## Ping entry signal (benchmark wallet)

Ping kalau ada pool yang **lolos gate v1** dan **mirip pool tempat wallet benchmark masuk** 20 hari terakhir. Default benchmark: `WKX8JSQ7K4XNPHN24MjrDCi8fRVTr4KFkh4aa3TZmpt` (`config.json` → `signal`).

```bash
python3 -m dlmm_terminal benchmark                 # profil wallet: entry 20 hari terakhir → habitat
python3 -m dlmm_terminal benchmark --wallet <W> --days 30
python3 -m dlmm_terminal signal                    # scan tiap 5 menit, ping ke terminal + Telegram
python3 -m dlmm_terminal signal --once --min-score 0.8
```

Cara kerjanya:

1. **Benchmark** (0 RPC, API Meteora): semua posisi closed wallet itu dalam N hari, slice yang dibuka ≤ 30 menit di pool yang sama = 1 entry. Tiap entry direkonstruksi **pas waktu entry**: umur pool, fee 1h / 24h sebelum entry, mcap (perkiraan), harga 1h / 6h, hasil (PnL SOL, lama hold). Disimpan ke `data/benchmark.json` + `data/benchmark_entries.jsonl`, dibangun ulang tiap 24 jam.
2. **Filter = satu lapis** (2026-11-05): gate v1 di atas (pair SOL, bin step 20–250, umur pool ≥ 60 menit, fee APR 24h ≥ 1.000%) **+ filter GMGN lu** (`signal.matchDims` + `bandOverrides`): **umur token 1 – 250 jam** (60m–15000m) · **mcap $400K – $10M** · **volume 24h token ≥ $50K** · **total fees ≥ 50 SOL**. Lapis 2 lama (bin step dari wallet benchmark) udah dihapus; nyalain lagi pakai `requireBenchmarkBinSteps: true`. Wallet benchmark tetap dipakai buat ping 👀. Umur & volume dihitung se-token, dari semua pair-nya di DexScreener; kalau DexScreener gagal, pakai angka pool. "Total fees" = fee LP pool DLMM ini sejak launch, dikonversi ke SOL. Ini cuma pendekatan: "Total Fees" di GMGN ngitung fee trader se-token di semua venue, dan datanya nggak ada sumber gratisnya. Dim lain yang bisa dipakai: `poolAgeHours`, `fees24hUsd`, `fees1hUsd`.
3. **Signal**: tiap 5 menit scan pool SOL → gate v1 → skor habitat (0–1). Lolos semua gate + skor ≥ `minMatchScore` (default 1.0 = semua cocok) → ping. Flag (pump, drop 6h, TVL kecil, entry malam) ikut di pesan sebagai ⚠️, nggak nge-block.
4. **Blast tiap 30 menit** (`mode: "blast"`, default): satu pesan berisi **10 pool terbaik** yang lolos lapis 1 + 2, format kayak card "DLMM Top 10 MEME Fees Opportunities": legend, lalu per pool 🧹 fee 1d/TVL · 📌 FDV · 📊 TVL · 📐 vol 5m · ⏳ harga 5m · 🟥 bin step · 🥩 vol 1h/TVL · 🥤 vol 5m×12 / vol 1h · 🎯 skor benchmark · ⚡ boost DexScreener · 🟢/🔴/♻️ buy/sell/txn 5m · ⏰ umur pool · link Meteora + DS, plus ⚠️ flag. Urutan default **vol 1h / TVL** (`blastSortBy`: `volTvl1h` | `feeTvl24h` | `fees1hUsd` | `prorated5m`). Atur `blastEveryMin` / `blastTop`. Data 5 menit dari DexScreener (gratis, tanpa key). `mode: "new"` = cara lama: ping per pool pas baru masuk kriteria (cooldown `cooldownHours`).
5. **Buang pool mati-lalu-hidup** (`signal.revival`, default `gate`): pump-and-dump di token yang sepi, contoh DAVID-SOL (20 jam fee nol, lalu 94% fee 24 jam numpuk di 3 jam, mcap 378k → 3.2M → 850k). Kena kalau dalam 24 jam terakhir ada ≥ `minDeadHours` (6) jam berturut-turut fee-nya < 0.5% total 24 jam **dan** 3 jam tersibuk ≥ `minTop3Share` (70%) fee 24 jam. Pool baru (history < 9 jam) nggak dihitung, karena belum pernah "mati". Di blast ada 🫀 jam aktif (x/24h) dan footer "Excluded N dead→alive". Catatan data: wallet benchmark 4× masuk pool berpola ini dan 4× untung (+1.42 SOL), dan 5 posisi rugi besar di spec nggak ada yang berpola ini. Jadi ini keputusan risiko lu, bukan filter yang udah kebukti; `mode: "flag"` = cuma ⚠️, `"off"` = mati.
6. **🆕 Koin baru masuk deteksi** (`newEntryAlerts`): tiap scan (5 menit), token yang **sebelumnya nggak ada** di daftar terdeteksi dan sekarang masuk langsung dapet alert sendiri (detail + link), nggak nunggu blast. Satu alert per token (pool terbaiknya), maksimal sekali per `newAlertCooldownHours` (6 jam). Scan pertama cuma nyatet yang udah ada.
7. **👀 Wallet entry**: kalau wallet benchmark buka posisi di pool baru, langsung ping + verdict gate & skor habitat (`pingWalletEntries`).

Telegram: isi `DLMM_TG_BOT_TOKEN` + `DLMM_TG_CHAT_ID` di `.env` (sama dengan alert `monitor`). Semua ping ke `data/signals.jsonl`; kandidat lengkap ke `candidates.jsonl` (maks tiap 15 menit) buat v2.

Benchmark 20 hari (4 Nov 2026): 87 entry di 25 pool, win 87%, +12.86 SOL (PnL Meteora, belum termasuk jual sisa token). Bin step 100 paling sering (47×, +9.16 SOL). Habitat: umur pool 1–195 jam, mcap $341k–$2.1M, fee 24h $293–$23.6k, fee 1h $19–$2.6k.

Ini **cek kemiripan**, bukan prediksi: spec nemu nggak ada fitur entry yang misahin winner/loser di token baru. Tetap assist mode: ping → lu cek → `open`.

## Hemat Helius (RPC free tier)

Yang **nggak pernah** pakai RPC (0 request): `screen`, `signal` (blast + benchmark), `benchmark`, `monitor` mode alert, `followup`, dan list pool + Portfolio di web. Semuanya dari API Meteora + DexScreener (gratis, tanpa key). Jadi signal 24/7 di Mac mini **nggak makan kuota Helius sama sekali**.

Helius (`SOLANA_RPC_URL`) cuma dipakai pas beneran transaksi:

| Aksi | RPC |
| --- | --- |
| Preview ladder (modal Zap di web, `open --plan`) | **node publik** dulu; Helius cuma kalau publik gagal. Dulu ±4 request Helius tiap ubah size |
| Buka ladder (`open`, tombol ⚡) | Helius: baca pool, simulate + kirim + konfirmasi per slice |
| Close (`Close` / `monitor --mode auto`) | Helius, cuma pas exit rule kena / lu klik |

Cek pemakaiannya (dicatat ke `data/rpc_usage.jsonl` tiap kali executor jalan):

```bash
python3 -m dlmm_terminal rpc            # 30 hari: total, Helius vs publik, per aksi, per method, pace / 30 hari
python3 -m dlmm_terminal rpc --days 7
```

## Filter (gate = wajib, flag = warning)

| Tipe | Rule | Kenapa (dari data) |
| --- | --- | --- |
| GATE | Pool DLMM pair SOL, bin step 20–250 | Semua 133 posisi kayak gini; di luar itu nggak ada bukti |
| GATE | Umur pool ≥ 60 menit | 5 entry di pool < 1h: −1.74 SOL |
| GATE | Fee APR 24h (atau sejak launch) ≥ 1.000% | 8 entry di bawah itu: −1.30 SOL |
| ~~GATE~~ | ~~Posisi ≤ 3% TVL~~ | **Dimatiin** (2026-11-04): % TVL tetap ditampilin sebagai info. Nyalain lagi: `gates.maxPositionPctOfTvl: 3` |
| RISK | Range bottom −70% (wide), bukan −50% | Wide: +9.63 SOL / 61 posisi, terburuk −6%. Standard: −0.72 SOL, terburuk −93% |
| RISK | Size = loss budget ÷ 0.9 | Satu posisi pernah −93% |
| FLAG | pump 1h ≥ 25% · drop 6h ≥ 17% · TVL < $25k · entry 00–09 WIB | Bagus di hindsight, gagal di token yang belum dilihat → di-log, nggak nge-block |

Fee APR = fee N jam ÷ TVL × 8760/N × 100. Harga selalu dihitung sebagai token dalam SOL (pool SOL-TOKEN dibalik).

## Exit / auto TP

Hasil replay 131 posisi tiap 5 menit, dibanding exit asli lu:

| Rule | Hasil | Default |
| --- | --- | --- |
| Take-profit +1 / +2 / +5 / +10% | −7.16 / −7.57 / −4.37 / −1.06 SOL | **off** (`takeProfitPct: null`) |
| Stop-loss −10 / −15 / −25% PnL | −7.60 / −4.24 / −1.35 SOL | **off** |
| Trailing 5 / 10 / 25 pts | −7.27 / −7.66 / −1.82 SOL | **off** |
| Close setelah 10 menit di bawah range | **+0.22 SOL** | **on** (`closeBelowRangeAfterMin: 10`) |
| Price stop −40% dari entry | ~impas hindsight, −6.47 SOL di token baru | off (setting) |
| Alert PnL ≤ −10% | — | on |
| Alert harga masuk 1/3 bawah range | — | on |

Kenapa TP fix rugi: posisi SOL-only di bawah harga cuma dapet dari fee, PnL biasanya naik terus sampai akhir (winner median peak di menit 25 dari 28). TP nutup posisi yang masih ngasilin. Jadi "auto TP" default-nya: **satu-satunya rule yang nambah SOL** (keluar kalau udah 10 menit di bawah range) + alert buat sisanya. TP/SL/trailing tetep bisa dinyalain di `config.json`; monitor bakal kasih warning.

`mode: "alert"` = cuma ngabarin ("exit rule hit, tutup sendiri"). `mode: "auto"` = executor nutup posisi. Sisa memecoin dari range **nggak** di-swap otomatis; jual sendiri (di data, jual sisa token makan −3.93 SOL).

## Log (buat v2)

`dlmm_terminal/data/` (di-gitignore):

| File | Isi |
| --- | --- |
| `candidates.jsonl` | Satu baris per kandidat: semua fitur, gatesPassed/Failed, flags, decision, plan |
| `decisions.jsonl` | Keputusan lu (enter / skip + alasan) |
| `pnl_log.jsonl` | PnL tiap 5 menit per posisi + event rule |
| `closes.jsonl` | Close yang dijalanin executor |
| `followup.jsonl` | Harga 1h/6h/24h setelah kandidat + dip terdalam: bukti skip nyelamatin atau nggak |

Flag dipromote jadi gate kalau (spec §7): ≥ 300 posisi closed di 50+ token (15+ yang rugi > 5%), nambah SOL di token yang belum dilihat (leave-one-token-out), skip ≤ 45% kandidat, dan log skip-nya konfirm.

## Versi HP: MET Pool + MET Portfolio + DLMM Rules (Scriptable iPhone)

`dlmm_terminal/scriptable/` = versi iPhone dari terminal ini, dengan fitur yang sama dengan web. Mesin DLMM-nya (buka ladder, close, claim, swap Jupiter, practice mode, key di HP) tetap punya script MET lu; logika filternya sekarang **dibagi** sama Mac lewat satu modul.

**Isi folder `dlmm_terminal/scriptable/`, 4 script yang di-paste ke Scriptable (nama persis):**

| Nama di Scriptable | File | Isi |
| --- | --- | --- |
| `MET Secrets` | `MET Secrets.example.js` | Key, RPC, ukuran, setting. Dibikin sekali, nggak ikut update. |
| `DLMM Rules` | `DLMM Rules.js` | **Baru.** Gate v1, flag, verdict OPEN/CAREFUL/WAIT/SKIP, benchmark, revival. Nggak punya UI dan nggak pegang key. |
| `MET Pool` | `MET Pool.js` | Daftar pool + ENTRY CHECK + 📡 PING (filter signal entry Mac) + ★ watchlist + ◎/$/Rp + ⚙ Settings + zap ladder. |
| `MET Portfolio` | `MET Portfolio.js` | Posisi, PnL, exit rule, close. |

**Fitur MET Pool (sama kayak web):**

- **Scan** pool SOL dua sisi (SOL di X atau di Y), TVL ≥ `SCAN_MIN_TVL_USD` (default $2.000), tanpa floor mcap (widget parameter `300K` tetap bisa dipakai).
- **ENTRY CHECK** di bawah tiap pool: `● OPEN / CAREFUL / WAIT / SKIP` + skor + 2 alasan teratas (gate gagal, flag, plus konteks DexScreener: volume jam terakhir vs pace 24 jam, buyers vs sellers). Tap baris itu buat semua alasan.
- **Tiga mode**: `ALL` · `V1 PASS` (OPEN + CAREFUL, default) · `SIGNAL`.
- **Jendela fee** `1h / 4h / 24h` (kolom FEES + %/hari + sort ikut berubah).
- **SIGNAL** = lolos gate v1 **dan** mirip habitat wallet benchmark (bin step yang untung, umur pool, band mcap/fees dari lu) **dan** bukan pool "mati lalu hidup" (revival gate). Diurut kayak kartu top-10 di Mac (`SIGNAL_SORT_BY`, default Vol/TVL 1 jam). Tiap baris: `🎯 hits/dims · 🥩 vol/TVL 1h · 🫀 jam aktif`.
- **Benchmark** dibangun di HP dari data Meteora (±100 request, tanpa RPC, ±20 detik): otomatis tiap 24 jam, atau tap baris info di mode SIGNAL. Sebelum itu dipakai profil bawaan di `DLMM Rules`.
- **Zap** default = ladder terminal: Spot SOL-only, 5 slice sampai −70%, isi 1:2:3:4:5, dua ukuran (setengah dan penuh `DEFAULT_SIZE_SOL`, dibatasi loss budget). Layar konfirmasi nampilin gate/flag/signal, cap loss budget, dan warning kalau range bottom < −65%.
- **Log**: tiap refresh ke `met_candidates.jsonl` (skema sama dengan `candidates.jsonl` di Mac), keputusan enter/skip pas konfirmasi.
- **Widget** nampilin pool yang lolos V1 (atau semuanya kalau belum ada yang lolos).

**MET Portfolio** (nggak berubah): alert PnL ≤ −10%, alert harga di 1/3 bawah range, **exit** kalau posisi SOL-only ≥ 10 menit di bawah range, notifikasi iOS, tombol `Exit → SOL`. `EXIT_MODE: "auto"` = buka app langsung close + swap. TP/SL ada tapi default `null`.

**Panduan install lengkap:** `dlmm_terminal/scriptable/PANDUAN_IPHONE.md` (PDF: `Panduan iPhone.pdf`). Bikin paket yang tinggal dipindahin ke iPhone (4 script dengan nama persis + PDF):

```bash
bash dlmm_terminal/scriptable/build_iphone_pack.sh ~/Desktop     # -> DLMM-iPhone.zip
```

**Ringkasnya:**

1. Scriptable → `+` → paste `MET Secrets.example.js`, namain **`MET Secrets`**, isi `HELIUS_API_KEY` dan `SOLANA_PRIVATE_KEY` (wallet khusus LP), tambah `BANKROLL_SOL` (modal LP lu, SOL). **Biarin `MET_ZAP_PRESETS: []`** biar dapat ladder terminal (kalau Secrets lama lu masih ada preset `bidask`/`3 slice`, kosongin).
2. Paste `DLMM Rules.js` → namain **`DLMM Rules`**.
3. Paste `MET Pool.js` → **`MET Pool`**, dan `MET Portfolio.js` → **`MET Portfolio`**.
4. `PRACTICE_MODE: true` dulu (semua tombol cuma simulasi) sampai lu udah coba sekali.

Update angka filter di Mac (`config.json`) lalu jalanin `python3 dlmm_terminal/scriptable/stamp_rules.py` dan paste ulang `DLMM Rules.js`; test-nya nolak kalau defaultnya beda dari `config.json`.

Batasan iOS: widget refresh tiap ~15 menit dan nggak boleh kirim transaksi, jadi "10 menit di bawah range" = dua refresh yang jaraknya ≥ 10 menit, dan close-nya jalan pas app dibuka (auto) atau pas lu tap notifikasi (alert). Buat yang beneran jalan sendiri tiap 5 menit: `python3 -m dlmm_terminal monitor --mode auto` di Mac. Daftar Signal di Telegram tiap 30 menit tetap dari Mac (`signal`), bukan dari HP.

**Test** (Node, di luar Scriptable):

```bash
node dlmm_terminal/scriptable/tests/test_dlmm_rules.mjs     # DLMM Rules vs jawaban Python (92 cek)
node dlmm_terminal/scriptable/test_rules.mjs                # exit rule MET Portfolio
NODE_USE_ENV_PROXY=1 node dlmm_terminal/scriptable/tests/harness_pool.mjs --mode=signal   # jalanin MET Pool beneran ke API live, UI di-stub
python3 dlmm_terminal/scriptable/tests/make_golden.py       # rekam ulang jawaban Python setelah ubah filters/benchmark/config
```

## Versi web (kayak LP WEB, buat Solana)

Di Mac mini: double-click **`dlmm_terminal/Buka DLMM Web.command`** di Finder (browser kebuka sendiri ke `http://localhost:8790`, biarin jendela Terminal-nya kebuka). Dari HP lewat Tailscale: `http://<nama-mac-mini>:8790`. Pertama kali, macOS mungkin nolak file-nya: klik kanan → Open → Open.

```bash
python3 -m dlmm_terminal web                      # http://localhost:8790
python3 -m dlmm_terminal web --host 0.0.0.0       # buka dari HP lewat Tailscale
```

- **Pilih pool**: semua pool DLMM SOL (TVL ≥ filter), kolom TVL / FEES (1h · 4h · 24h) / FEE 1 JAM / MCAP + sparkline 24h / **ENTRY CHECK** / ZAP. Sort tiap kolom, search, `v1 pass only`, `hide SKIP`. Refresh tiap 1 menit.
- **ENTRY CHECK** = spec v1: `OPEN` (gate lolos, nggak ada flag) · `CAREFUL` (gate lolos, ada flag) · `WAIT` (gate yang bisa berubah gagal: umur, fee APR) · `SKIP` (bin step / bukan SOL). Skor + alasan kayak "Volume picking up", "Buyers lead" dari DexScreener cuma konteks, **nggak** ngubah level (spec: nggak ada sinyal entry yang tahan di token baru).
- **Detail** (klik baris): harga, mcap, TVL, fee/hari, kotak verdict, alasan ✓/✗, tombol zap, chart DexScreener.
- **Zap**: modal plan (size SOL, range bottom −70%, size vs TVL, cap loss budget, flag) → `Open di Meteora`, `Copy plan`, `Log: enter / skip` (ke `data/decisions.jsonl`). Web ini **nggak pegang key**; buka posisinya di Meteora atau MET Pool di HP.
- **Portfolio**: posisi terbuka wallet (Meteora API), PnL SOL + peak, range bar (merah = 1/3 bawah), EXIT CHECK pakai rules yang sama dengan `monitor` (dicek tiap 5 menit selama halaman kebuka). Close tetap dari MET Portfolio / Meteora / `monitor --mode auto`.
- Snapshot semua kandidat ke `data/candidates.jsonl` maksimal tiap 15 menit (buat v2).

## Buka & tutup posisi (ladder RH, Spot, SOL-only)

Bentuk ladder sama persis dengan MET Pool di HP (`executor/ladder.mjs` = `MET.planLadder`): **5 slice** lebar bin-nya sama, dari harga sekarang sampai **−70%**, isi SOL **1:2:3:4:5** (slice paling dalam paling gede), Spot di tiap slice. Contoh 8 SOL di bin step 80: 0.53 / 1.07 / 1.6 / 2.13 / 2.67 SOL.

Sekali aja di Mac: `cd dlmm_terminal/executor && npm install` (butuh Node: `brew install node`).

**Key (sekali, kayak LP WEB):** bikin `dlmm_terminal/.env` dari template dan buka di TextEdit (atau double-click `Buka DLMM Web.command`, yang bikinin otomatis kalau belum ada):

```bash
cp -n dlmm_terminal/.env.example dlmm_terminal/.env && chmod 600 dlmm_terminal/.env && open -e dlmm_terminal/.env
```

Isinya, lalu save dan start ulang server:

```
DLMM_PRIVATE_KEY=<private key base58, dari Phantom: Settings → Manage Accounts → Show Private Key>
SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=<key lu>
DLMM_WEB_PIN=<opsional: PIN, biar bisa trading dari HP>
```

Pakai wallet khusus LP yang isinya cuma yang siap lu risikoin. `.env` udah di-gitignore. Key cuma dibaca proses Node (executor) di Mac; Python dan browser cuma lihat address-nya. Chip kanan atas jadi **🔑 address** kalau key kebaca, **🔒 read-only** kalau belum.

Siapa yang boleh trading: tanpa `DLMM_WEB_PIN` cuma browser di Mac yang jalanin server; dengan PIN, device lain (HP via Tailscale) juga bisa, tapi ditanya PIN. Website lain yang kebuka di browser lu nggak bisa nyuruh server ini trading (butuh header khusus).

**Dari web:**
1. Zap di pool → modal nunjukin gate v1 + tabel 5 slice → **⚡ Buka ladder** → konfirmasi.
2. Server simulasi tiap slice, lalu kirim satu-satu. Kalau satu gagal, sisanya berhenti dan tetap SOL. Wallet kurang SOL = stop sebelum kirim apa pun.
3. Gate gagal atau di atas cap loss budget = ada konfirmasi tambahan, dicatat sebagai override.
4. Portfolio: tombol **Close** per posisi (tarik 100% + klaim fee + tutup akun, rent balik). Kalau exit rule nyala, tombolnya jadi merah **Exit → close**.

**Dari Terminal:**

```bash
python3 -m dlmm_terminal open <POOL> --sol 8                 # gate → plan 5 slice → ketik y → kirim
python3 -m dlmm_terminal open <POOL> --sol 8 --dry-run --owner <WALLET>   # simulasi aja
```

Pakai key yang sama di `.env` (`DLMM_PRIVATE_KEY`, atau `DLMM_KEYPAIR=/path/keypair.json`) + `SOLANA_RPC_URL`. Opsi: `--slices`, `--bottom`, `--top`, `--strategy spot|curve|bidask`, `--force` (gate gagal), `--force-size` (lewat cap).

### Jumlah slice = yang lu minta

`--slices 1` sekarang beneran kebuka **1 posisi**. Dulu, kalau satu slice lebih dari 70 bin (posisi standar maksimal 70 bin), jumlah slice dinaikin diam-diam (1 jadi 2, bisa sampai 18 di bin step kecil). Sekarang slice yang lebih lebar dari 70 bin dibuka sebagai **satu posisi extended** (dibikin kosong lalu diisi per chunk, 3-4 transaksi). Konsekuensinya: **rent lebih gede** (±0.0006 SOL per bin di atas 70; balik pas close), modal tampil di plan sebagai `wide`. Mau perilaku lama (dipecah jadi banyak posisi 70 bin), pakai `--split` di `open_ladder.mjs`. Dites lokal di LiteSVM (program DLMM asli + akun mainnet): 1 slice 152 bin = 1 posisi extended 4 tx, 2 slice = 2 posisi extended, 5 slice standar = 5 posisi; semua tx sukses. **Belum dites kirim beneran ke mainnet.**

### Jual token: Jupiter, kalau gagal langsung ke pool Meteora

`executor/swap.mjs` dipakai Close+swap, Re-entry, auto exit, dan Cleanup. Urutannya: Jupiter (retry kalau 429 / server error, pakai `JUPITER_API_KEY` di `.env` kalau ada) → kalau Jupiter nolak/rate limit/nggak ada rute, jual langsung di pool Meteora DLMM token itu (pool posisi yang ditutup, atau pool TOKEN/SOL dengan TVL terbesar). Dua-duanya batal kalau harganya lebih dari max loss (⚙ Risk, default 5%) di bawah harga pool. Alasan gagal sekarang ditulis apa adanya (misal `HTTP 429: Rate limit exceeded`), bukan cuma "no route".

### Web v6: PnL dalam ◎/$, refresh 15 menit, 🎯 auto exit per ladder

- Angka besar PnL di Portfolio sekarang **nominal** (◎ atau $, ikut toggle), % di bawahnya.
- Pool, PING, dan Portfolio di-refresh tiap **15 menit** (⚙ Settings → Web → `web.refreshMin`) supaya hemat RPC.
- **🎯 Auto exit (take profit)** per ladder: dropdown di tiap baris Portfolio, `off / TP +3% / +5% / +10%`. Server DLMM Web ngecek ladder yang nyala tiap `web.autoExitCheckMin` menit (default 2, cuma Meteora data API, **tanpa RPC**). Kalau PnL ladder (PnL ◎ ÷ deposit, angka % di baris) ≥ target: close semua slice + jual token ke SOL (= Close+swap, batal jual kalau > max loss swap), lapor ke Telegram kalau di-set, dicatat di `data/closes.jsonl`. Status di baris: nyala / lagi jalan / ✓ selesai / ✗ gagal. Disimpan di `data/auto_exit.json`. **Cuma jalan selama DLMM Web nyala di Mac.** Belum dites kirim beneran. Catatan spec v1: TP tetap rata-rata lebih jelek dari exit manual.

### iPhone v5 (sama dengan web v5)

- **⚙ Settings** di MET Pool dan MET Portfolio (UITable): knob yang sama dengan web, disimpan di HP (`dlmm_settings.json`) di atas `MET Secrets`. Ladder/gate/PING kepakai langsung di MET Pool; exit rules + max loss swap di MET Portfolio.
- **📡 PING**: sekarang filter yang sama dengan Mac (v1 gates + umur token / mcap / vol 24h token / total fees, tanpa cek bin step benchmark, buang mati→hidup, top N urut 1h Vol/TVL). Umur token + vol 24h diambil dari DexScreener per token (cache 10 menit). Rank `📡n` juga muncul di MET Portfolio.
- **★ watchlist** (`dlmm_watch.json`) dan **◎ SOL / $ USD / Rp** buat semua angka pool (TVL, vol, fees, mcap).
- `DLMM Rules.js` di-stamp ulang dari `config.json` (fix tes `DEFAULTS == config.json`); tes: `node scriptable/tests/test_dlmm_rules.mjs` (92) + `node scriptable/test_rules.mjs`.

### Web v5: settings, SOL/USD, PING, watchlist

- **⚙ Settings** (top bar): semua angka yang dipakai terminal bisa diubah dari web: ladder (size, slice, bottom, bentuk), risk (bankroll, loss budget, max loss swap), gate v1, flag, filter PING, exit rules. Disimpan di `data/settings.json` (cuma yang beda dari `config.json`), dan dipakai juga sama `monitor`, `signal`, `open`, `reenter`. **Reset** = balik ke `config.json`.
- **◎ SOL / $ USD** (top bar, shortcut `u`): semua nilai (TVL, fees, mcap, harga, posisi, PnL, wallet) dibaca dalam SOL atau USD. Angka kecil ditulis `0.0₆637` (6 nol).
- **Mode pool**: `ALL` · `V1 PASS` · `📡 PING` · `★ WATCH` (shortcut `1`–`4`, `/` = cari).
  - **PING** = filter signal entry: SOL pair, bin 20–250, umur pool ≥ 60m, fee APR 24h ≥ 1.000%, umur token 1h–250h, mcap $400k–$10M, vol 24h ≥ $50k, total fees pool ≥ 50 SOL, buang pool mati→hidup, top 10 urut 1h Vol/TVL (semua bisa diubah di ⚙). Scan ulang tiap 5 menit di background (20–60 detik). Pool yang masuk PING dapat badge `📡#rank` di semua mode dan di Portfolio; detail pool nunjukin angka PING-nya.
  - **★ WATCH** = pool pilihan lu: klik ☆ di baris/detail. Disimpan di `data/watchlist.json` (kebaca juga dari HP via Tailscale).
- **🔔** notifikasi browser: pool baru masuk PING, exit alert / exit rule kena (sekali per kejadian).
- Top bar & Portfolio: **wallet bebas** (SOL yang belum di posisi), nilai dan fee per ladder, PnL % total.
- Zap modal: pilih bentuk (Spot/Curve/BidAsk) + kesetaraan USD.

### Portfolio web: satu baris per ladder

Ladder 3 slice tampil **satu baris** (PnL gabungan dalam % / SOL / $, range gabungan). Tombol: **Close** (semua slice, satu-satu), **Close+swap**, **Re-entry**, dan **Cleanup** di header kolom aksi (jual sisa token ≥ 0.005 SOL dengan price impact ≤ 5% + tutup token account kosong; `executor/cleanup.mjs`, `--dry-run --owner <WALLET>` buat lihat dulu). Cleanup menjual token apa pun di wallet, termasuk yang dibeli di luar terminal ini (kecuali SOL/USDC/USDT).

### Close + swap dan Re-entry (juga saat masih in range)

```bash
# lihat dulu: posisi, in range atau nggak, token yang dipegang, harga jual Jupiter vs harga pool (nggak kirim apa-apa)
python3 -m dlmm_terminal reenter <POOL> <POS1,POS2,...> --reopen --dry-run --owner <WALLET>
python3 -m dlmm_terminal reenter <POOL> <POS1,POS2,...>            # close semua + jual token ke SOL
python3 -m dlmm_terminal reenter <POOL> <POS1,POS2,...> --reopen   # + buka ladder lagi di harga sekarang
```

Urutannya: close semua posisi (klaim fee) → token yang keluar (selisih saldo, token lama di wallet nggak disentuh) dijual ke SOL via Jupiter, **batal kalau lebih dari `--maxloss` % (default 5) di bawah harga pool** → kalau `--reopen`, ladder dibuka lagi (default jumlah slice = jumlah posisi lama, `--bottom` dari config, SOL = semua yang balik dikurangi rent; atau `--sol`). Nggak ada bedanya harga udah lari di atas ladder (semua SOL) atau masih in range (sebagian token): token apa pun yang keluar dijual dulu. Kalau jual gagal atau ditolak, ladder nggak dibuka dan token tetap di wallet. Di web: tombol **Close+swap** dan **Re-entry** di tiap baris Portfolio (jalan di semua posisi pool itu). Dry-run dites ke posisi mainnet nyata yang in range; alur kirimnya (close, swap Jupiter, buka ulang) **belum dites kirim beneran**: coba dulu dengan ukuran kecil.

Sisa memecoin setelah close **belum** di-swap otomatis ke SOL: jual di Jupiter / Cleanup di MET Portfolio.

## Kirim ke temen

`dlmm_terminal/share/` = paket buat orang lain: launcher klik-dua-kali (Mac `Mulai DLMM Terminal.command`, Windows `Mulai DLMM Terminal.bat`) yang ngecek Python/Node, install SDK, bikin `.env` dan buka web, plus panduan (`PANDUAN.md` / PDF). Bikin ZIP-nya:

```bash
bash dlmm_terminal/share/build_share.sh ~/Desktop
```

ZIP nggak berisi `.env`, `data/`, `node_modules`, atau script HP. Ukuran posisi per orang diatur di `.env` (`DLMM_BANKROLL_SOL`, `DLMM_SIZE_SOL`), bukan di `config.json`.
