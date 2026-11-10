# Elysium Wallet Finder

Database wallet LP di **Elysium** (chain 99901) + app **Streamlit** buat nyari
top wallet. Dibagi dua kelas, sama kayak RH Wallet Finder: **Multiday** dan
**Degen**, masing-masing punya papan + skor. Isinya: skor & tier, realized /
unrealized PnL, ROI, win rate, profit factor, drawdown, copier, style, Real PnL,
preset, papan 24 jam, twin finder, watchlist, pool finder (GeckoTerminal),
hasil LP per pool, dan etalase web di `web/`.

Beda sama `rh_wallet_finder/`: ini chain-nya Elysium, gas-nya **HYPE**,
stable-nya **USDC**, dan yang di-index bukan cuma Uniswap v4. v3 ikut, karena
chain-nya masih baru dan likuiditasnya belum tentu duduk di satu venue.

Semua angka dari RPC lu (default-nya RPC publik Elysium). Butuh Python 3.10+.
Streamlit di-install sendiri ke `.venv` pas lu double-click launchernya
(Python Homebrew nolak `pip install` di luar virtualenv). Sync & scoring
sendiri cuma pakai standard library.

## Jalanin

1. Ambil repo (`git pull`, atau clone).
2. Double-click **`elysium_wallet_finder/Buka Wallet Finder.command`**.
   Pertama kali dia bikin `.venv` di folder ini (kalau lu udah bikin sendiri,
   yang itu yang dipakai), install Streamlit ke situ, terus browser kebuka ke
   **http://localhost:8502** (8501 dipakai RH Wallet Finder, biar dua-duanya
   bisa nyala bareng). Path yang ada spasinya (misalnya
   `/Volumes/SSD 1TB/work/Capstone1`) aman. Dari Terminal sama aja: jalankan
   file `.command` itu. Jangan `pip install` ke Python Homebrew — itu yang
   keluar `externally-managed-environment`.
   Kalau install-nya gagal, jendela Terminal-nya nunggu (Tekan Enter), nggak
   langsung ilang.
3. RPC publik `https://rpc.elysium.kinetiq.xyz` udah jadi default, jadi lu bisa
   langsung sync tanpa isi apa-apa. Kalau mau RPC sendiri (Alchemy lebih enak
   buat history panjang): tab **Settings**, atau
   ```bash
   python3 finder.py set-rpc 'https://…'
   ```
   Key Alchemy doang (tanpa `https://`) jadi
   `https://elysium-mainnet.g.alchemy.com/v2/<key>`.
   Link-nya disimpen di `elysium_wallet_finder/.env` (gitignored, **jangan**
   di-commit: repo ini public).
4. Biarin Terminal-nya kebuka. Sync jalan di background:
   - history default **dari blok 1** (`BACKFILL_DAYS=0`), bukan 30 hari.
     Genesis chain 19 Okt 2026; mainnet publik 9 Nov 2026. Uniswap v4 baru
     rame beberapa hari sebelum tanggal ini, jadi backfill-nya nggak sepanjang
     Robinhood Chain, tapi bloknya cepet (~0,11 detik).
   - tiap beberapa segmen langsung di-score.
   - abis history keisi, update sendiri tiap 5 menit.

Database-nya `elysium_wallet_finder/data/wallets.db`, pisah dari RH Wallet Finder.

## Command

```bash
cd elysium_wallet_finder
python3 finder.py                         # app + sync
python3 finder.py worker                  # sync + skor, tanpa UI
python3 finder.py sync --segments 1       # satu segmen (~20 ribu blok)
python3 finder.py sync --days 2           # cuma N hari, bukan dari genesis
python3 finder.py score
python3 finder.py top --kind degen -n 20 --json
python3 finder.py check 0x…
python3 finder.py twin 0x…
python3 finder.py early --hours 6         # LP yang masuk launch pool pas masih baru, JSON
python3 finder.py alerts --hours 6        # brief: harga HYPE, early LP, top papan
python3 finder.py pools --sort 1h --quote HYPE
python3 finder.py pool-results --days 7 --min-positions 3 --min-wallets 2
python3 finder.py status
python3 finder.py publish                 # upload etalase web, atau --out board.json
```

## Isi app

Sama polanya kayak RH Wallet Finder: papan **Multiday** / **Degen** / **Last 24h**,
filter, preset, Real PnL, Twin finder, Watchlist, Settings. Yang diganti:

| Di RH | Di sini |
| --- | --- |
| ETH + USDG | HYPE + USDC |
| Explorer Blockscout Robinhood | [Elyscan](https://elyscan.live) |
| Tab vs RH Discovery | Tab **Early**: wallet yang LP di pool launch dalam `EARLY_MINUTES` (default 30) setelah posisi locker |
| LP Agent, RH Smart LP Finder | nggak ada (LP Agent cuma Solana + Robinhood Chain) |

`Min closed` default **1** (chain-nya muda; naikin di Settings kalau papannya berisik).
Wallet idle > `ACTIVE_DAYS` (default 7) nggak di-ranking.

Skornya tetap per kelas, 0–100. Multiday = median hold ≥ 2 jam. Di data yang
masih beberapa hari, hampir semua orang jatuh ke Degen. Itu expected.

## Harga

1 HYPE dalam USD diambil dari pool **USDC/WHYPE 1%** ElysiumSwap v3
(`0xec2e846b…521d`, likuiditasnya paling dalam waktu dicek), kalau kosong
pindah ke pool Uniswap v3 yang sama fee-nya
(`0x5bbf2d96…0fd6`). USDC = $1. Native HYPE (v4 `address(0)`) dan WHYPE
dihargain sama.

## Kontrak

Dicek di RPC publik 10 Nov 2026: chain id 99901, tiap alamat punya kode,
`factory()` + `WETH9()` dua NPM v3-nya cocok, dua PositionManager v4
`poolManager()`-nya nunjuk PoolManager yang sama.

| Venue | Kontrak | Alamat | Sumber |
| --- | --- | --- | --- |
| v4 | PoolManager | `0xE2e105d3F7209A9DA11f83cCf3E7398a753823F1` | [Uniswap deployments](https://developers.uniswap.org/docs/protocols/v4/deployments) |
| v4 | StateView | `0x27eD73aE0652fdCf71d6793412A7525f32bE9dfE` | sama |
| v4 | PositionManager (dokumen Uniswap) | `0x1d1C51b9d07fD27d36412A2b66CC23D19Eb3E518` | sama. `name()` = "Uniswap v4 Positions NFT" |
| v4 | PositionManager kedua | `0x73a3098520404f4ea174788bab5f0e04810e6460` | nggak ada di halaman Uniswap, ketemu on-chain: `name()` sama, `poolManager()` sama, `nextTokenId` ribuan. Ikut di-index. Manager lain yang lolos cek yang sama ikut otomatis |
| v3 | ElysiumSwap Factory / NPM | `0x7266c0Ec…b697` / `0xC6Dc5194…91f5` | [docs.elysiumswap.finance/contracts](https://docs.elysiumswap.finance/contracts.html). Init code hash `0xe34f199b…8b54`. Fee 500 / 3000 / 10000 |
| v3 | Uniswap Factory / NPM | `0x1707327f…7a9f` / `0xf025e0fe…da6f` | `factory()` dan `WETH9()` dibaca dari NPM. Deployer `0x2179a608…27b6` (wallet deploy Uniswap). Init hash sama dengan ElysiumSwap |
| | WHYPE | `0xbA876237B0F021A6c6D852d6c26E107997cC5E81` | docs Elysium + [kinetiq contract addresses](https://elysium.kinetiq.xyz/docs/contract-addresses) |
| | USDC (6 desimal) | `0xb47c4c8f73f009cc0c0d3f92b388d13e293f0b65` | sama |
| | Multicall3 | `0xcA11bde05977b3631167028862bE2a173976CA11` | sama |
| launch | Locker ElysiumPad | `0xa83212Cac640Cb27005B956531c6cB251F14465C` | docs ElysiumSwap |

Posisi v4 dikunci `venue:tokenId`. Dua PositionManager sama-sama mulai dari id 1,
jadi id-nya tabrakan kalau nggak dipisah.

## Launch pool (creator fee)

Pool launch ElysiumPad itu v3 1% di ElysiumSwap. Fee swap-nya masuk ke posisi
yang di-lock selamanya, terus dibagi 70% kreator / 30% ElysiumPad
([creator fees](https://docs.elysiumswap.finance/pad/fees.html)). Bukan buat LP
lain.

Yang tool ini lakuin:

- posisi milik locker ditandai `launch` dan **nggak** masuk ranking (itu inventaris protokol, bukan wallet),
- pool-nya ditandai `creator_fee`,
- wallet lain yang buka posisi di pool itu dalam `EARLY_MINUTES` setelah locker
  masuk papan **Early** dan kolom `early`. `finder.py early` / `alerts` ngeluarin JSON buat cron.

Waktu dicek, NFT ElysiumSwap v3 #1–#3 emang dipegang locker, fee 10000.
Jendela sync pendek di head bisa belum nyentuh mint lama itu, jadi `early`
baru keisi setelah backfill nyampe blok launch-nya.

## Pool finder

GeckoTerminal punya network id **`elysium`** (bukan tebakan). `finder.py pools`
narik volume / TVL dari situ, fee tier-nya dibaca on-chain (v4 `StateView.getSlot0`,
v3 `fee()`). Kalau GeckoTerminal 404, scan-nya berhenti dengan pesan, dan
`pool-results` tetep jalan dari posisi yang udah dihargai di database sendiri.

`pool-results` itu kebalikannya: fee yang beneran didapet LP (dari posisi yang
udah ditutup dan dihargai), bukan volume × fee tier.

## Etalase web

Folder `web/`. Di Vercel, Root Directory = `elysium_wallet_finder/web`,
framework Other. Blob store + `PUBLISH_KEY`, terus di Settings paste
`https://<project>.vercel.app/api/publish?key=…`. Snapshot-nya
`elywf/snapshot.json.gz` (beda path dari RH, biar satu store nggak nimpah).
Yang ke-upload cuma alamat + angka. Label, notes, RPC nggak ikut.

## Cara kerjanya, singkat

1. **Discovery.** Satu `eth_getLogs`: `Initialize` + `ModifyLiquidity` di
   PoolManager, `Transfer` di tiap PositionManager v4, dan
   `IncreaseLiquidity` / `DecreaseLiquidity` / `Collect` / `Transfer` di dua NPM v3.
   Pengirim `ModifyLiquidity` yang bukan PositionManager dan bukan EOA
   (router, vault, launchpad yang megang likuiditas sendiri) **dihitung terus
   dilewat**. Nggak ada wallet buat disalahin.
2. **Harga.** v4: sama kayak RH, state 1 blok sebelumnya lewat StateView
   (archive `eth_call`). v3: jumlah token udah ada di event, jadi yang dibaca
   cuma `slot0` pool-nya per blok, bukan checkpoint fee tiap posisi. Jauh lebih
   irit. Fee v3 = `Collect` dikurangi pokok yang masih ngantung dari
   `Decrease` sebelumnya.
3. **Ranking.** Sama mesinnya: ladder satu transaksi = satu trade, skor
   persentil di dalam kelas, Real PnL motong fee pool + slippage.

## Batasan

- **v2 nggak di-index.** ElysiumSwap v2 cuma beberapa pair, Uniswap v2
  puluhan, dan LP-nya token ERC-20 biasa bukan NFT. Aktivitas yang keliatan
  sekarang ada di v4 (dua PositionManager) plus sedikit v3. v2 ditinggal.
- Posisi v4 yang garamnya (`salt`) nggak muat di integer 63-bit dilewat.
  NFT manager mint id kecil, jadi yang kelewat cuma panggilan langsung ke
  PoolManager dengan salt 32 byte.
- Likuiditas yang dipegang kontrak (bukan NFT) nggak jadi wallet. Di segmen
  head, jumlah event kayak gitu bisa lebih banyak dari event NFT. Angka
  `skipped_mods` di `finder.py status`.
- Nilai posisi v3 yang masih open = likuiditas di harga sekarang + `tokensOwed`.
  Fee yang belum di-checkpoint ke NFT belum masuk sampai dia `Collect`.
  Posisi yang udah ditutup akurat, karena `Collect`-nya ada di log.
- Pool tanpa USDC / HYPE / WHYPE di salah satu sisi nggak bisa dihargain.
- Posisi yang dibuka sebelum history kita nggak punya setoran awal, jadi PnL-nya
  nggak dihitung.
- Harga token ngawur (pool kesedot) dan posisi yang balik > 1000× dibuang.
- RPC harus archive (`eth_call` blok lama) dan `eth_getLogs`. RPC publik
  waktu dicek ngelayanin dua-duanya, tapi ini RPC bareng-bareng: `RPC_WORKERS`
  default 4. Range log-nya mentok ~10 ribu log, indexer nurunin chunk sendiri.
- GeckoTerminal kadang ngasih fee dari nama pool (DEX yang bukan Uniswap).
  Yang `fee_src=name` jangan dipercaya buta.
- Nggak ada patokan RH Discovery di chain ini, jadi nggak ada tab banding.

## Tes

```bash
python3 -m unittest discover -s elysium_wallet_finder/tests
python3 -m unittest discover -s rh_wallet_finder/tests
```
