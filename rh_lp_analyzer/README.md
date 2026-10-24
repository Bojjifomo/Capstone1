# RH LP Analyzer

App **Streamlit terpisah** (nggak ngubah RH Wallet Finder; databasenya cuma dibaca kalau ada) buat bedah wallet LP
**Uniswap V4 di Robinhood Chain** buat copytrade, ala Apex Liquidity. Datanya
dibaca langsung dari chain per wallet pakai RPC lu, nggak butuh database / sync
yang jalan terus.

## Jalanin

Di Mac: double-click **`Buka LP Analyzer.command`** (pertama kali install
Streamlit sendiri, terus kebuka di http://localhost:8502). Dari Terminal:

```bash
cd rh_lp_analyzer
python3 -m pip install -r requirements.txt
python3 -m streamlit run streamlit_app.py
```

Buka **⚙ Settings — RPC**, paste link RPC Robinhood Chain lu (SolidRPC) di
RPC 1, dan (disarankan) Alchemy API key di RPC 2 → **Save & test**. Disimpen di
`rh_lp_analyzer/.env` (di-gitignore, jangan di-commit: repo public).

Di **Streamlit Cloud**: main file `rh_lp_analyzer/streamlit_app.py`, isi Secrets:

```toml
RPC_URL = "https://rpc.solidrpc.io/ak_…/evm/4663"
CALL_RPC_URL = "alchemy key"
APP_PASSWORD = "password buat halaman ini"
```

## Isinya (layout ala Apex Liquidity)

**Single wallet**: paste 1 wallet (atau beberapa, digabung), pilih period →
- **Quality**: `ELITE` (kuat & profit) / `SOLID` (layak copy) / `MIXED` (hati-hati,
  kecilin size) / `WEAK` (hindari) / `REJECT` (rugi), plus alasan kenapa belum naik tier.
- **Trader type**: `MULTIDAY` / `HYBRID` / `DEGEN` (dari mcap token, lama hold, lebar range).
- **Gameplay archetype**: Spike Hunter, Liquid Scalper, Micro Degen, Intraday Swinger,
  Conviction Holder, Patient Big-Cap LP, Range Farmer, Adaptive.
- Flag: profit concentration, tail risk, efficiency, out-of-range, pace bot.
- Kartu Performance & Returns, Daily & Cumulative PnL, Risk Management, Profit Quality
  (masing-masing ada "What these mean").
- **Filters**: min/max market cap pas masuk + Position Sided (ETH/USDG Only, Dual Sided,
  Token Only), berlaku ke seluruh laporan.
- **Find the best filter**: grid search rentang mcap × sided × pool ETH/USDG, bisa langsung di-apply.
- Position Sizing (balance / target deploy lu, atau baca wallet lu), Position Composition,
  distribusi range / durasi / mcap, tabel pool, Per-Position Detail (+ CSV), share link, ★ simpan wallet.
- **Better wallets**: kalau database RH Wallet Finder ada, wallet sekelas yang skornya lebih tinggi.

**Compare wallets**: paste sampai 20 wallet → tiap wallet dianalisa sendiri,
dijejerin (paling bagus di atas) + kurva PnL barengan. Di bawahnya **Split your
capital**: modal lu (default $2,500) dibagi per profil:

| Profil | Wallet yang masuk | Cash | Maks per wallet |
| --- | --- | --- | --- |
| **Safety** | Cuma ELITE / SOLID; bobot ke return yang mulus (Multiday dapet lebih) | 30% | 25% |
| **Balanced** | ELITE / SOLID / MIXED yang bukan bot; bobot ROI × profit factor, diredam drawdown | 15% | 35% |
| **Aggressive** | 3 wallet ROI tertinggi yang nggak REJECT; condong ke yang terbaik | 5% | 50% |

Tiap wallet dikasih $ + size per trade & maks per posisi, perkiraan 30 hari kalau
performa keulang, drawdown yang harus siap ditahan, dan peringatan kalau dua wallet
main di pool yang sama. Cuma dari data masa lalu, bukan saran finansial.

## Dari mana datanya (hemat RPC)

Urutan sumber, dari yang gratis:

1. **Database RH Wallet Finder** (`../rh_wallet_finder/data/wallets.db`, dibuka
   **read-only**, nggak diubah sama sekali): dia udah nge-index semua posisi V4 dan
   banyak event-nya udah dihargai → **nol request RPC** buat nyari posisi. Kalau
   databasenya di tempat lain, isi `WALLET_FINDER_DB` di `.env`.
2. **Explorer Blockscout** (API daftar transfer NFT + transaksi wallet), kalau bisa diakses.
3. **Alchemy Transfers API**, kalau RPC 2 lu Alchemy dan network-nya didukung.
4. **Position-id walk (cuma `eth_call`)**: nomor NFT posisi selalu naik, jadi
   `nextTokenId()` di beberapa blok + `ownerOf()` massal (300 per request) nunjukin
   posisi mana punya wallet ini; blok mint/close dicari pakai binary search, terus log
   1 blok aja. Jalan juga di Alchemy free tier. Hasil "siapa dapet NFT nomor berapa"
   disimpen, jadi wallet berikutnya (mis. di Compare) nggak bayar lagi. Posisi yang
   dibuka & ditutup dalam ±2,5 menit nggak kelihatan lewat cara ini.
5. Terakhir baru **`eth_getLogs`** lewat RPC. Kalau sebuah RPC cuma ngizinin range blok
   kecil banget (Alchemy free tier: 10 blok), dia berhenti dan ngasih tau, bukannya
   ngabisin kuota.

Event LP diambil dari receipt transaksi; posisi yang angka liquidity-nya nggak balance
dicari lewat scan log pool, atau (kalau RPC-nya nggak bisa) lewat bisection
`getPositionLiquidity()` (klaim fee doang tanpa ubah liquidity nggak kelihatan lewat
cara ini). Bagian periode yang tetap nggak kebaca dilewatin dan disebut di halaman
(juga per wallet di Compare). Harga tiap event dibaca sekali dari state chain (archive
`eth_call`), terus disimpen di `data/cache.db`. Di bawah "Analysis complete" ada berapa
detik, berapa request RPC, dan sumber yang dipakai.

Catatan kuota: RH Wallet Finder yang sync terus pakai SolidRPC yang sama. Kalau kuota
harian habis, pause dulu sync-nya atau tambahin RPC kedua.

## Batasan

- Cuma posisi Uniswap V4 lewat PositionManager resmi (NFT).
- Pool tanpa USDG / ETH di salah satu sisi nggak bisa dihargai.
- Posisi yang dibuka sebelum lookback nggak dihitung (setoran awalnya nggak kebaca).
- Posisi yang seed ≥ 2% supply token = launch token, bukan trading: nggak dihitung.

## Tes

```bash
python3 -m unittest discover -s rh_lp_analyzer/tests
```
