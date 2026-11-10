# Elysium Terminal: buka posisi + liat portfolio di Elysium

Sama idenya kayak **RH Terminal**, tapi chain-nya Elysium (bukan Robinhood) dan quote-nya **USDC / HYPE**.
Server di Mac, dibuka dari browser Mac dan HP (Tailscale). `rh_terminal` nggak diubah: ini paket sendiri,
port sendiri, env sendiri.

| Fitur | Isinya |
| --- | --- |
| **Pool dari chain** | Index swap Uniswap **v4** (PoolManager) dan Uniswap **v3** (ElysiumSwap + Uniswap resmi). Fee, volume, jumlah swap per 5m / 1h / 6h / 24h. |
| **ENTRY CHECK** | `OPEN / CAREFUL / WAIT / SKIP`. Fee 0 / honeypot = SKIP. Pool baru, TVL tipis, fee pelan = WAIT. Pump, dump, seller dominan, pool v4 ber-hook = flag. |
| **PING, watch, search** | Top pool yang lolos gate. Watchlist di Mac. Search nama / CA / pool id (DexScreener chain `elysium` + yang ada di index). |
| **Zap ladder** | Slice sama lebar di bawah harga, isi 1:2:…:n, quote-only, cap budget, warning size vs TVL. v4 = satu `modifyLiquidities`. v3 = beberapa NFT, di-batch `multicall`. |
| **Portfolio** | Posisi dibaca dari chain. **Satu baris per pembukaan**: dua posisi di pool yang sama tetap dua baris, masing-masing PnL, tombol, dan rule auto sendiri. v4 dari log Transfer + `ownerOf`. v3 dari `tokenOfOwnerByIndex` (dua NPM). Fee v3 = static-call `collect` (nggak ngirim tx). |
| **Aksi** | **Close** · **Claim** · **Close+swap** (minOut = harga pool − fee − max loss) · **Re-entry** (ladder) · **Rebalance** (satu range) · **Cleanup**. Semua di-`eth_call` dulu, revert = batal. |
| **Tight** | Range sempit di sekitar harga: swap + mint dalam satu klik, dari tab 🎯 Tight atau tombol 🎯 di detail pool mana pun yang tanpa hook. |
| **⚙ Auto** | Per posisi: TP, SL, dan aksi kalau harga keluar range (atas / bawah masing-masing: diam, rebalance, atau close + swap). Jalan di proses server, bukan di tab. |
| **PIN, practice** | `ELYSIUM_PRACTICE_MODE` default **true**. Banner kuning di atas halaman. PIN buat trading dari HP. Key cuma dibaca Node. |
| **Index health** | Chip ⛓ nulis jumlah pool v4/v3, jam histori, lag block, dan error kalau ada. Baris di bawah tabel: RPC publik vs Alchemy hari ini. |
| **Data tambahan** | Umur pool, TVL + mcap dari DexScreener (chain `elysium`, udah dicek), APR dari fee 1 jam, jumlah trader 1 jam, nama hook v4 (sekali, disimpan di disk). Portfolio: nilai dalam $ dan HYPE, fee yang udah di-claim (log Collect v3) masuk PnL. |

## Kenapa tadi cuma 1 pool

Index swap nyimpen pool yang **gagal** di-resolve (RPC publik ke-limit, atau `eth_getLogs` Initialize kepanjangan di chain 100ms) sebagai “bukan pool USDC/HYPE”. Abis itu head-nya sudah maju, jadi jam itu nggak di-scan ulang. Hasilnya tabel nempel di 1 pool.

Sekarang lookup yang gagal **nggak** di-cache. Scan-nya per chunk, head cuma maju kalau chunk itu berhasil, jadi poll berikutnya nerusin. Cache lama yang isinya null kebanyakan dibuang pas start, terus di-backfill lagi. Hapus `elysium_terminal/data/poolkeys.json` sama `activity.json` cuma kalau lo mau paksa scan dari nol.

## RPC, biar Alchemy nggak boros

Baca selalu **RPC publik dulu**. Alchemy cuma fallback, dan cuma kalau belum kena cap harian.

```
ELYSIUM_ALCHEMY_DAILY_CAP=1500
```

Itu jumlah request Alchemy per hari UTC, default 1500. Kecapai, sisa hari itu publik aja (atau gagal kalau publik juga mati). Pemakaian tertulis di `data/rpc_budget.json` dan `data/rpc_usage.jsonl`, keliatan di `doctor` dan di baris bawah tabel.

Yang nggak di-fetch ulang: symbol/decimals token (`data/token_meta.json`), nama hook (`data/hooks.json`), pool key, NFT id, fee claimed. Tab browser yang ketutup berhenti nanya pool/portfolio. Kalau nggak ada browser ±90 detik, index swap ngendor jadi 3 menit.

DexScreener `elysium` dipakai buat TVL, mcap, icon. Holder count nggak ada sumber gratis yang murah di chain ini, jadi belum.

Chart di panel kanan digambar dari bucket 5 menit yang sudah ada di index (harga relatif + volume). Nol request RPC, nol Alchemy. Tombol ⚡ di baris pool langsung simulasi/kirim ukuran itu (satu konfirmasi). ✏️ tetap buka plan lengkap. Kolom dan filter dex tersimpan di browser itu saja (`localStorage`), jadi iPhone dan Mac boleh beda. Banner kuning = PRACTICE, merah = LIVE. Jumlah trader 1 jam ikut tersimpan di `activity.json`, nggak hilang pas restart.

Fee v3 yang sudah di-claim (event Collect) masuk PnL. Fee v4 yang sudah di-claim tidak: PositionManager di chain ini tidak emit event Collect, cuma Transfer + perubahan likuiditas, jadi fee dan pokok tidak bisa dipisah tanpa baca tiap receipt.

## Yang udah dicek on-chain (9 Nov 2026)

RPC publik `https://rpc.elysium.kinetiq.xyz` balas **chain id 99901**. Block time yang keukur ±0.10 detik.
Semua alamat di bawah `eth_getCode`-nya ada isinya. Yang ada `factory()` / `WETH9()` / `poolManager()`-nya juga dicek.

| | Alamat | Cek |
| --- | --- | --- |
| USDC | `0xb47c4c8f73f009cc0c0d3f92b388d13e293f0b65` | symbol USDC, 6 desimal |
| WHYPE | `0xbA876237B0F021A6c6D852d6c26E107997cC5E81` | symbol WHYPE, 18 desimal. `WETH9()` di router/NPM nunjuk ke sini |
| ElysiumSwap v3 factory | `0x7266c0EcfF2A687B4Ad0Ead7B24840929540b697` | 6 pool dari `PoolCreated` (semua fee 1%) |
| ElysiumSwap SwapRouter | `0x9fae201fa4c0740b1b92DebEcDDfFE74C1Ecd8b3` | `factory()` = factory ES. Selector-nya SwapRouter klasik (`exactInputSingle` ada deadline), bukan SwapRouter02 |
| ElysiumSwap NPM | `0xC6Dc519451F58dFd27bB2920EeFAe57597bd91f5` | `factory()` + `WETH9()` bener. Ada `mint` / `decreaseLiquidity` / `collect` / `burn` / `multicall` / `tokenOfOwnerByIndex` |
| ElysiumSwap QuoterV2 | `0xc256C98819BB29661Dd66e3DBCE3cA71875D8143` | `factory()` = factory ES |
| USDC/WHYPE 1% (ES) | `0xec2e846b66eee17bc856129aa9e9b1baee4d521d` | `getPool` = CREATE2 pakai init code hash dokumentasi. Harga kebaca (±75 USDC per WHYPE pas dicek) |
| Uniswap v4 PoolManager | `0xE2e105d3F7209A9DA11f83cCf3E7398a753823F1` | sama dengan [deployments Uniswap](https://developers.uniswap.org/docs/protocols/v4/deployments). Swap-nya rame (ribuan per beberapa ribu block) |
| v4 PositionManager | `0x1d1C51b9d07fD27d36412A2b66CC23D19Eb3E518` | `poolManager()` nunjuk PoolManager di atas. Ada `modifyLiquidities` + `poolKeys` |
| v4 StateView | `0x27eD73aE0652fdCf71d6793412A7525f32bE9dfE` | `poolManager()` sama |
| Universal Router 2.2.0 | `0x1B341FDD55d56F8E08409A2C25DDFad93Eb03C1a` | bytecode-nya nyebut PoolManager v4, factory v3 Uniswap resmi, WHYPE, Permit2. **Nggak** nyebut factory ElysiumSwap |
| Uniswap v3 factory (resmi) | `0x1707327f626496a7b5b3872e7e4d2879df7d7a9f` | 148 `PoolCreated`. Bukan yang di docs ElysiumSwap. NPM-nya `0xf025e0fe9e331a0ef05c2ad3c4e9c64b625cda6f` |
| Permit2, Multicall3 | `0x000000000022D473030F116dDEE9F6B43aC78BA3`, `0xcA11bde05977b3631167028862bE2a173976CA11` | ada kodenya |

Posisi NFT beneran juga kebaca: ElysiumSwap `positions(6)` = USDC/WHYPE 1% full-range, likuiditasnya sama dengan `liquidity()` pool itu. Static-call `collect` dari owner-nya balik fee (bukan 0) dan **nggak ada tx yang terkirim**.

v4 dipakai penuh (paling dekat sama RH Terminal): index, zap, portfolio, close, claim, swap lewat Universal Router.
v3 ElysiumSwap juga penuh, lewat NPM + SwapRouter mereka.
v3 Uniswap resmi: index, zap, close, claim di NPM-nya. Jual token-nya lewat Universal Router (`V3_SWAP_EXACT_IN`), karena router v3 khusus buat factory itu nggak ada di halaman deployments Uniswap.

**Belum.** v2 (factory-nya ada, nggak dipakai buat buka posisi). API 0x. Fee yang udah ke-claim nggak masuk PnL. TVL diambil dari DexScreener (`elysium`); kalau belum ke-index, TVL kosong dan gate-nya WAIT.

## Buka di Mac

Panduan Mac mini + iPhone + MacBook: **[PANDUAN_MAC_HP.md](PANDUAN_MAC_HP.md)**. RH Terminal boleh tetap nyala di port **8892**. Ini di **8893**.

1. `brew install node` sekali (tanpa Node tabel tetap jalan, tombol transaksi mati).
2. Double-click **`elysium_terminal/Buka Elysium Terminal.command`** (pertama kali: klik kanan → Open).
3. Dia bikin `elysium_terminal/.env`. Isi `ELYSIUM_PRIVATE_KEY` (wallet LP **Elysium**, jangan tempel key Robinhood) dan kalau ada `ALCHEMY_API_KEY`. Biarin `ELYSIUM_PRACTICE_MODE=true`.
4. `python3 -m elysium_terminal doctor` nggak pernah nampilin key.

Dari HP lewat Tailscale: `http://<nama-mac>:8893`. Isi `ELYSIUM_WEB_PIN` kalau mau trading dari situ.

## Terminal

```bash
python3 -m elysium_terminal web
python3 -m elysium_terminal web --host 0.0.0.0
python3 -m elysium_terminal screen
python3 -m elysium_terminal screen --all --top 50
python3 -m elysium_terminal portfolio --wallet 0x…
python3 -m elysium_terminal open <POOL> --usd 400 [--slices 5 --bottom 60] [--dry-run]
python3 -m elysium_terminal doctor
```

`POOL` = pool id v4 (32 byte) atau alamat pool v3 (20 byte).

## Gimana transaksinya jalan

Key cuma dibaca proses Node (`executor/`). Python bahkan ngebuang `RH_PRIVATE_KEY` dari env sebelum manggil Node, biar key Robinhood nggak kepake. Browser cuma lihat address.

Setiap aksi di-`eth_call` dulu. Revert = nggak dikirim, nggak makan gas.

| Aksi | v4 | v3 |
| --- | --- | --- |
| Zap | `modifyLiquidities`: MINT × slice + SETTLE_PAIR (+ SWEEP kalau HYPE native) | `multicall` mint × slice. Quote WHYPE boleh dibayar HYPE native (NPM yang wrap), sisa di-`refundETH` |
| Close | BURN × slice + TAKE_PAIR | `decreaseLiquidity` + `collect` + `burn`, satu multicall |
| Claim | DECREASE 0 + TAKE_PAIR | `collect` aja |
| Close+swap / Re-entry | jual lewat Universal Router, pool key itu sendiri, minOut dijaga | ElysiumSwap: `exactInputSingle` di SwapRouter. Uniswap resmi: Universal Router. minOut sama |
| Cleanup | jual sisa token (≥ $1) lewat pool USDC/HYPE-nya. USDC, HYPE, WHYPE nggak dijual | sama |

Approve: v4 dan swap Uniswap resmi lewat Permit2. v3 ElysiumSwap (mint + SwapRouter) approve ERC-20 biasa ke NPM / router.

**Tes** (offline nggak nyentuh jaringan; live sim cuma `eth_call`):

```bash
python3 -m unittest elysium_terminal.tests.test_elysium_terminal elysium_terminal.tests.test_tight elysium_terminal.tests.test_auto
cd elysium_terminal/executor && node tests/test_offline.mjs
cd elysium_terminal/executor && node tests/test_live_sim.mjs
```

`test_live_sim.mjs` nolak `eth_sendRawTransaction`. **Belum pernah ada transaksi dari terminal ini yang dikirim ke mainnet.** Practice mode nyala dulu. Ukurannya kecil kalau nanti dimatiin.

## Yang perlu lu tahu

- Gate & flag ini nilai awal, bukan hasil backtest. Atur di ⚙.
- Harga HYPE diambil dari pool USDC/HYPE atau USDC/WHYPE yang paling rame di index. Kalau index masih kosong, fallback-nya pool ElysiumSwap USDC/WHYPE 1% di atas.
- Pool v4 yang fee LP-nya 0% (hook yang nentuin harga) kena SKIP. Pool ber-hook yang fee-nya normal kena flag, bukan skip. Mint ke hook bisa aja revert: simulasi yang nolak.
- Deposit di Portfolio: yang dicatat terminal saat buka. Posisi dari luar terminal dihitung dari likuiditas (anggap single-sided quote), ditandai `≈`. Fee v3 yang sudah di-claim (event Collect) masuk PnL. Fee v4 yang sudah di-claim tidak: PositionManager tidak emit Collect, cuma perubahan likuiditas dan transfer token, jadi fee dan pokok tidak bisa dipisah tanpa baca tiap receipt.
- Rule ⚙ Auto cuma jalan selama proses web-nya nyala.
- RPC publik rate-limited. `ALCHEMY_API_KEY` disaranin buat kirim tx. Baca tetap coba RPC publik dulu.
- Umur pool v4 yang cuma ketemu lewat `poolKeys` (belum lewat event Initialize) bisa kosong. DexScreener ngisi kalau dia udah index. PING nggak masukin pool yang umurnya nggak ketahuan.

## Data

`elysium_terminal/data/` (gitignore): `activity.json`, `poolkeys.json`, `ids_<wallet>.json`, `settings.json`, `watchlist.json`, `ui_prefs.json`, `decisions.jsonl`, `closes.jsonl`, `rpc_usage.jsonl`, `groups.json` (NFT mana yang satu posisi), `auto.json` (rule + batas auto), `auto_log.jsonl`, `tight_config.json`, `tight_signals.json`.

## Satu baris per posisi

Dulu Portfolio menggabung semua NFT di satu pool jadi satu baris ("2 slice"), walaupun itu dua posisi yang lo buka terpisah. Sekarang satu baris = satu pembukaan:

- Yang dibuka dari terminal ini dicatat di `data/groups.json` (NFT mana saja, kapan, berapa quote yang masuk). Itu juga yang dipakai buat umur dan deposit.
- Yang dibuka dari tempat lain dikelompokkan dari fakta chain: NFT v4 yang di-mint di block yang sama = satu ladder. NFT v3 dengan id berurutan dan range yang nyambung = satu ladder. Sisanya berdiri sendiri.
- Posisi kedua di pool yang sama dapat label `#2`. Close, Claim, Close+swap, Rebalance, dan ⚙ Auto cuma kena ke baris itu.
- Deposit posisi dari luar terminal dihitung dari likuiditas seolah satu sisi, ditandai `≈`. Buat range yang di-mint dua sisi angka itu bisa kegedean, jadi PnL-nya kekecilan.

## Tight range dan rebalance

Range sempit di sekitar harga sekarang. Bukan janji profit. Angka %/jam di kartu itu **fee seluruh pool dibagi TVL**, bukan fee posisi lo, dan APR-nya cuma `%/jam × 24 × 365`. Target %/jam itu filter, bukan patokan aman.

**Buka.** Tab 🎯 Tight, atau tombol 🎯 tight di detail pool mana pun yang tanpa hook dan tanpa fee dinamis. Isi ukuran, lebar (% harga, total kiri + kanan), slippage. Satu klik menjalankan:

1. swap sebagian quote ke token lewat pool itu sendiri (minOut = harga pool − fee − slippage),
2. mint range di sekitar harga saat itu.

Mint cuma pakai quote yang lo minta plus token hasil swap tadi. Saldo wallet yang lain tidak ikut ditarik. Lebar di-snap ke garis grid terdekat (`tickSpacing`), dan kartunya nulis lebar yang beneran jadi.

**Rebalance.** Tombol di baris posisi satu range: close (fee ikut) → swap ke komposisi range baru → mint di harga sekarang. Deposit awal dibawa ke posisi baru, jadi PnL-nya menghitung seluruh rangkaian rebalance, bukan cuma potongan terakhir.

Close, swap, dan mint itu transaksi terpisah. Tiap-tiap disimulasikan dulu. Kalau satu gagal, yang sebelumnya tidak dibatalkan: hasilnya nulis berhenti di langkah mana, dan token yang sudah keluar ada di wallet (Cleanup bisa jual balik).

## ⚙ Auto per posisi

Tombol ⚙ Auto di tiap baris Portfolio (atau langsung di form buka tight):

| Rule | Yang terjadi |
| --- | --- |
| Take profit / stop loss | PnL posisi itu lewat batas → close + swap ke quote. Langsung, tanpa nunggu. |
| Harga **naik** lewat atas range | Posisi jadi 100% quote dan fee berhenti. Pilih: diam, rebalance, atau close + swap. |
| Harga **turun** lewat bawah range | Posisi jadi 100% token. Pilih: diam, rebalance, atau close + swap. |

Contoh yang sering dipakai: atas → rebalance (ngikutin harga naik, murah karena sudah quote semua), bawah → close + swap (berhenti ngejar turun).

Batas buat aksi range, di tab 🎯 Tight (`data/auto.json`):

- **tahan di luar range** (default 120 detik): harga yang cuma nyentuh tepi lalu balik tidak memicu apa-apa.
- **jeda antar aksi** (600 detik) dan **maks per posisi per hari** (6).
- **maks nilai posisi** ($2000): di atas itu auto tidak nyentuh.
- **slippage swap maks** (100 bps) dan lebar default rebalance.
- **⏸ Jeda semua auto**: satu tombol, juga ada di bar Portfolio.

Yang perlu lo tahu:

- Loop-nya hidup di proses `python3 -m elysium_terminal web`, dicek tiap menit (⚙ Settings → Web). Tab boleh ditutup, prosesnya harus jalan.
- Practice mode: auto cuma mensimulasikan close, mencatatnya, dan tidak mengirim apa-apa.
- Live (`ELYSIUM_PRACTICE_MODE=false`): begitu syarat kena, server mengirim transaksi pakai key di `.env` tanpa nanya lagi. Itu yang lo setujui waktu menyimpan rule.
- Gagal sebelum ada yang terkirim (RPC, simulasi revert): dicoba lagi setelah jeda, maksimal 3 kali, lalu rule berhenti.
- Berhenti di tengah (close masuk, swap atau mint tidak): **tidak** diulang otomatis. Rule jadi ⚠ dan nunggu lo klik "sudah dicek, lanjutkan".
- Portfolio yang kebaca sebagian (RPC gagal di tengah) tidak dianggap "posisi sudah hilang". Auto nunggu baca berikutnya.
- Rebalance cuma buat posisi satu range di pool tanpa hook. Ladder: close atau diam, atau pakai Re-entry.
- Satu wallet, satu antrean: klik manual dan auto tidak pernah menandatangani bersamaan.
- Take profit lama yang dipasang per pool (`data/auto_exit.json`) dipindah sekali jadi rule per posisi di pool itu.
- Rebalance bolak-balik di pasar yang gergaji bisa makan fee swap dan IL lebih besar dari fee yang masuk. Batas di atas membatasi seberapa sering, bukan menjamin untung.
