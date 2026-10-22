# RH LP Analyzer

App **Streamlit terpisah** (nggak nyentuh RH Wallet Finder) buat bedah wallet LP
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

## Isinya

**Single wallet**: paste 1 wallet (atau beberapa, digabung), pilih period →
- Verdict `COPY` / `WATCH` / `REJECT` (+ alasannya), kelas Multiday / Degen, gameplay.
- Net PnL, Capital ROI (+ ≈/30 hari), win rate, profit factor, modal ke-deploy
  (median & peak), posisi open + floating PnL, fee, grafik PnL harian + kumulatif.
- Risk: max drawdown, hari terburuk, out-of-range yang rugi, bisa diikutin manual atau pace bot.
- Profit quality, recommended filter (mcap × sisi deposit × pool ETH/USDG),
  position sizing ke modal lu, distribusi range / hold / mcap, tabel pool,
  detail per posisi (+ CSV), ★ simpan wallet, link bisa di-share.

**Batch & compare**: paste sampai 20 wallet → tiap wallet dianalisa sendiri,
dijejerin di satu tabel (paling bagus di atas) + kurva PnL barengan. Di bawahnya
**Split your capital**: masukin modal (default $2,500), dapet pembagian per wallet:

| Profil | Wallet yang masuk | Cash | Maks per wallet |
| --- | --- | --- | --- |
| **Safety** | COPY/WATCH, profit factor ≥ 1.3, drawdown < 25%, ≥ 10 posisi closed, bisa diikutin; bobot ke return yang mulus (Multiday dapet lebih) | 30% | 25% |
| **Balanced** | Semua yang nggak REJECT & bukan pace bot; bobot ROI × profit factor, diredam drawdown | 15% | 35% |
| **Aggressive** | 3 wallet ROI tertinggi yang profit; bobot condong ke yang terbaik | 5% | 50% |

Tiap wallet dikasih: berapa $, size per trade & maks per posisi (trade dia
diskalain ke jatah lu), perkiraan 30 hari kalau performa 30 hari terakhir
keulang, dan drawdown yang harus siap lu tahan. Jatah < $50 dibuang (nggak worth
di-mirror); wallet yang LP di pool yang sama ditandain (copy dua-duanya = taruhan
dobel). Ini cuma dari data masa lalu, bukan saran finansial.

## Cara kerjanya

1. `eth_getLogs` Transfer NFT PositionManager **yang difilter ke alamat wallet**
   (murah): mint = buka posisi, burn = tutup. Mulai dari awal period − 30 hari
   (*Lookback days* di Settings), biar posisi yang dibuka sebelum period tetap kehitung.
2. Receipt tx mint: pool + range tiap posisi.
3. `ModifyLiquidity` pool-pool itu cuma selama umur posisinya: semua add / remove / claim fee.
4. Tiap event dihargai dari state chain 1 blok sebelumnya (Multicall3 + StateView,
   harga ETH dari pool ETH/USDG); posisi open dinilai di blok terbaru.

Semua disimpen di `data/cache.db`, jadi analisa wallet yang sama lagi cuma baca
blok baru. Butuh RPC yang support **archive** `eth_call` dan `eth_getLogs`.
Analisa pertama wallet yang aktif banget (ribuan posisi) bisa makan beberapa menit
dan banyak request RPC.

## Batasan

- Cuma posisi Uniswap V4 lewat PositionManager resmi (NFT).
- Pool tanpa USDG / ETH di salah satu sisi nggak bisa dihargai.
- Posisi yang dibuka sebelum lookback nggak dihitung (setoran awalnya nggak kebaca).
- Posisi yang seed ≥ 2% supply token = launch token, bukan trading: nggak dihitung.

## Tes

```bash
python3 -m unittest discover -s rh_lp_analyzer/tests
```
