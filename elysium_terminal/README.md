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
| **Portfolio** | Posisi dibaca dari chain. v4 dari log Transfer + `ownerOf`. v3 dari `tokenOfOwnerByIndex` (dua NPM). Fee v3 = static-call `collect` (nggak ngirim tx). PnL $, %, strip range, in/out of range. |
| **Aksi** | **Close** · **Claim** · **Close+swap** (minOut = harga pool − fee − max loss) · **Re-entry** · **Cleanup** · **auto exit** TP. Semua di-`eth_call` dulu, revert = batal. |
| **PIN, practice** | `ELYSIUM_PRACTICE_MODE` default **true**. PIN buat trading dari HP. Key cuma dibaca Node. |

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
python3 -m unittest elysium_terminal.tests.test_elysium_terminal
cd elysium_terminal/executor && node tests/test_offline.mjs
cd elysium_terminal/executor && node tests/test_live_sim.mjs
```

`test_live_sim.mjs` nolak `eth_sendRawTransaction`. **Belum pernah ada transaksi dari terminal ini yang dikirim ke mainnet.** Practice mode nyala dulu. Ukurannya kecil kalau nanti dimatiin.

## Yang perlu lu tahu

- Gate & flag ini nilai awal, bukan hasil backtest. Atur di ⚙.
- Harga HYPE diambil dari pool USDC/HYPE atau USDC/WHYPE yang paling rame di index. Kalau index masih kosong, fallback-nya pool ElysiumSwap USDC/WHYPE 1% di atas.
- Pool v4 yang fee LP-nya 0% (hook yang nentuin harga) kena SKIP. Pool ber-hook yang fee-nya normal kena flag, bukan skip. Mint ke hook bisa aja revert: simulasi yang nolak.
- Deposit di Portfolio dihitung dari likuiditas (anggap slice-nya single-sided quote). Fee yang udah di-claim belum masuk PnL.
- Auto exit cuma jalan selama proses web-nya nyala.
- RPC publik rate-limited. `ALCHEMY_API_KEY` disaranin buat kirim tx. Baca tetap coba RPC publik dulu.
- Umur pool v4 yang cuma ketemu lewat `poolKeys` (belum lewat event Initialize) bisa kosong. DexScreener ngisi kalau dia udah index. PING nggak masukin pool yang umurnya nggak ketahuan.

## Data

`elysium_terminal/data/` (gitignore): `activity.json`, `poolkeys.json`, `ids_<wallet>.json`, `settings.json`, `watchlist.json`, `ui_prefs.json`, `auto_exit.json`, `decisions.jsonl`, `closes.jsonl`, `rpc_usage.jsonl`.
