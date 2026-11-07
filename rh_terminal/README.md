# RH Terminal: LP Uniswap V4 di Robinhood Chain (versi Mac / web)

Upgrade dari RH Pool + RH Portfolio (Scriptable) jadi terminal kayak **DLMM Terminal**: satu
server di Mac, dibuka dari browser Mac dan HP (Tailscale), dengan semua utility-nya.

| Fitur | Isinya |
| --- | --- |
| **Pool dari chain langsung** | Index swap baca semua event Swap Uniswap V4 dari RPC publik Robinhood (±25 ribu swap/jam, 0 kuota Alchemy). **Semua** pool USDG / ETH yang lagi trading, nggak cuma top 200 GeckoTerminal. Fee LP persis dari chain (protocol fee udah dipotong), volume & fee per 5m / 1h / 6h / 24h. |
| **ENTRY CHECK** | `OPEN / CAREFUL / WAIT / SKIP` + alasan ✓/✗: fee LP 0% (hook) / honeypot = SKIP, pool baru / TVL tipis / mcap kecil / fee pelan = WAIT, pump, dump, pool tipis, seller dominan, pool ber-hook = flag. Info: volume picking up, buyers lead. |
| **📡 PING** | Top N pool (satu per token) yang lolos gate + band TVL / mcap / umur / fee 1 jam, urut fee 1 jam ÷ TVL. Dihitung tiap refresh, 0 request tambahan. Telegram + notif browser kalau ada yang baru masuk. |
| **★ WATCH, 🔎 search** | Watchlist disimpan di Mac (sama di HP). Search (Enter) nyari **semua** pool V4 USDG/ETH buat nama / CA / pool id, termasuk yang kecil. |
| **⚡ Zap ladder** | Bentuk RH Pool: slice sama lebar dari `mulai` sampai `bottom` di bawah harga, isi 1:2:…:n, quote-only (USDG atau **ETH native**). Plan dihitung dari harga pool sekarang, cap loss budget, warning size vs TVL. Approve Permit2 otomatis kalau perlu. |
| **💼 Portfolio** | Posisi wallet dibaca dari chain (NFT dari log Transfer, tanpa explorer): satu baris per ladder, PnL $ / %, fee unclaimed, strip range (mcap bawah ▮ atas, posisi harga, in range / di atas / di celah / lewat bawah), exit check. |
| **Aksi** | **Close** (burn semua slice, satu tx) · **Claim** (fee aja) · **Close+swap** (close + jual token ke USDG/ETH, batal kalau > max loss di bawah harga pool) · **Re-entry** (close + jual + buka ladder baru di harga sekarang) · **Cleanup** (jual sisa token di wallet) · **🎯 auto exit** TP per ladder. |
| **⚙ Settings, $ / Ξ, PIN, practice** | Semua angka (ladder, risk, gate, flag, PING, exit) diubah dari web. Toggle dolar / ETH. PIN buat trading dari HP. `RH_PRACTICE_MODE=true` = semua tombol cuma simulasi. |

## Buka di Mac

> **Mac mini sebagai server + iPhone & MacBook Pro:** ikutin **[PANDUAN_MAC_HP.md](PANDUAN_MAC_HP.md)** (juga `Panduan Mac + HP.pdf`): Tailscale, PIN, Mac nggak tidur, nyala sendiri, coba practice dulu.

1. Sekali aja: `brew install node` (buat tombol transaksi; tanpa Node tabel tetap jalan).
2. Double-click **`rh_terminal/Buka RH Terminal.command`** di Finder (pertama kali: klik kanan → Open → Open).
   Browser kebuka ke **http://localhost:8892**. Biarin jendela Terminal-nya kebuka.
3. Pertama kali dia bikin `rh_terminal/.env` dan buka di TextEdit:
   - **Udah pakai RH Secrets di Scriptable (iCloud)?** Kosongin aja key-nya: `PRIVATE_KEY`,
     `ALCHEMY_API_KEY` dan `PRACTICE_MODE` dibaca dari situ, sama kayak di HP.
   - Atau isi `RH_PRIVATE_KEY=` (64 hex) dan `ALCHEMY_API_KEY=` di `.env`.
   - Biarin `RH_PRACTICE_MODE=true` dulu sampai lu udah coba tiap tombol sekali.
4. Chip kanan atas jadi **🔑 0x…** kalau key kebaca, **🔒 read-only** kalau belum (klik buat lihat kenapa).
   Di Terminal: `python3 -m rh_terminal doctor` (nggak pernah nampilin key).

Dari HP / MacBook lewat Tailscale: `http://<nama-mac>:8892`, Add to Home Screen. Tanpa PIN: Mac itu,
device di Wi-Fi rumah, dan Tailscale bisa langsung trading (IP publik tetap ditolak). Mau pakai PIN buat
device lain: isi `RH_WEB_PIN=` di `.env`.

Pertama kali index swap baca 1 jam terakhir (±15–30 detik). Makin lama server nyala, makin panjang
historinya (sampai 24 jam, disimpan di `data/activity.json`, jadi restart nggak hilang). Fee 6h / 24h
sebelum index punya histori segitu diambil dari volume DexScreener × fee tier.

## Terminal

```bash
python3 -m rh_terminal web                    # http://localhost:8892
python3 -m rh_terminal web --host 0.0.0.0     # bisa dibuka dari HP lewat Tailscale
python3 -m rh_terminal screen                 # tabel pool di Terminal (OPEN/CAREFUL, urut fee 1 jam)
python3 -m rh_terminal screen --all --top 50
python3 -m rh_terminal portfolio --wallet 0x…
python3 -m rh_terminal open <POOL_ID> --usd 400 [--slices 5 --bottom 60] [--dry-run]
python3 -m rh_terminal doctor
```

## Gimana transaksinya jalan

Key cuma dibaca proses Node (`executor/`, ethers). Python dan browser cuma lihat address-nya.
Semua aksi **disimulasikan dulu** (eth_call) dan batal kalau bakal revert, jadi yang gagal nggak makan gas.

| Aksi | Transaksi |
| --- | --- |
| Zap | (approve token → Permit2 → PositionManager, sekali per token) + `modifyLiquidities`: MINT_POSITION × slice + SETTLE_PAIR (+ SWEEP buat ETH native) |
| Close / Claim | satu `modifyLiquidities`: BURN_POSITION (atau DECREASE 0) × slice + TAKE_PAIR |
| Close+swap / Re-entry / auto exit | close → jual token yang keluar lewat Universal Router (V4_SWAP di PoolKey pool itu sendiri, minOut = harga pool − fee − max loss) → (re-entry) ladder baru pakai semua yang balik |
| Cleanup | jual sisa token dari ladder lu (≥ $1) lewat pool USDG/ETH terdalamnya |

Encoding-nya sama dengan RH Pool / RH Portfolio (tes ngecek byte-nya), dan swap router-nya
byte-identik dengan swap yang ketambang di chain ini.

**Tes** (dijalanin pas bikin ini, semua lolos):

```bash
python3 -m unittest rh_terminal.tests.test_rh_terminal      # 18 tes offline
cd rh_terminal/executor && node tests/test_live_sim.mjs --usdg-pool 0x… --eth-pool 0x… --owner 0x… --ids 1,2
```

`test_live_sim.mjs` ngejalanin tiap aksi ke chain asli pakai eth_call, **tanpa ngirim apa-apa**: buka
ladder di pool USDG dan pool ETH dari wallet palsu yang dikasih saldo lewat state override, close & claim
posisi nyata sebagai owner-nya, swap router yang di-rebuild dari tx ketambang (byte-identik) dan diputar
ulang buat penjual asli. **Belum pernah dikirim beneran ke mainnet dari terminal ini**: coba dulu pakai
practice mode, lalu ukuran kecil.

## Yang perlu lu tahu

- **Gate & flag ini nilai awal, bukan hasil backtest** kayak spec DLMM. Atur di ⚙ sesuai pengalaman lu.
- Pool baru (< 60 menit) yang lagi pump ratusan persen sering nongol paling atas di fee 1 jam: itu kena
  WAIT sampai umurnya cukup, dan tetap kena flag pump.
- Deposit di Portfolio dihitung dari likuiditas (ladder single-sided, kayak RH Portfolio). Fee yang udah
  di-claim belum ikut PnL. Ladder ETH dihitung pakai harga ETH sekarang.
- Pool yang fee LP-nya 0% (harga diatur hook) di-SKIP: LP nggak dapat apa-apa di situ. Contohnya pool
  utama IGNITION/ETH.
- Auto exit cuma jalan selama RH Terminal nyala di Mac.

## Versi HP

`scriptable/RH_Pool.js` (zap-v37, ⭐ pin) dan `scriptable/RH_Portfolio.js` (close-v120, range strip fix)
tetap jalan di Scriptable kayak biasa. `RH Secrets` yang sama dipakai dua-duanya.

## Data

`rh_terminal/data/` (di-gitignore): `activity.json` + `poolkeys.json` (index swap), `ids_<wallet>.json`
(NFT posisi), `settings.json`, `watchlist.json`, `ui_prefs.json`, `auto_exit.json`, `decisions.jsonl`
(zap / skip), `closes.jsonl` (close, re-entry, cleanup, auto exit), `rpc_usage.jsonl`.
