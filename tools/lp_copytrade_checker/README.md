# LP Copy-Trade Checker (Uniswap V4)

Ngecek apakah ada bot yang **ngikutin open/close LP** sebuah wallet, lalu dump token setelah wallet itu close.
Bisa dipakai lewat **web (Streamlit)** atau **CLI**.

## Cara kerja

1. Ambil N tx open/close LP terakhir wallet (mint/burn NFT posisi Uniswap V4).
2. Untuk tiap aksi, scan event `ModifyLiquidity` di PoolManager selama `window` detik setelahnya.
3. Wallet lain yang open/close di **pool yang sama dengan range tick yang persis sama** dicatat sebagai follower, lengkap dengan delay-nya.
4. Setelah wallet target close, dihitung juga berapa token pool (selain stablecoin/ETH) yang follower lepas dalam window itu.

Status follower:

| Status | Arti |
|---|---|
| `COPY` | ngikutin minimal 2 aksi (misalnya open **dan** close) → hampir pasti bot copy-LP |
| `CURIGA` | baru sekali, tapi niru ≥2 range |
| `lemah` | cuma 1 range sekali, bisa jadi kebetulan |

## Web (Streamlit)

Jalanin lokal dari **root repo** (biar tema gelap di `.streamlit/config.toml` kebaca):

```bash
pip install -r tools/lp_copytrade_checker/requirements.txt
export ALCHEMY_API_KEY=xxxx   # opsional, bisa juga diisi di form
streamlit run tools/lp_copytrade_checker/streamlit_app.py
```

Deploy gratis ke **Streamlit Community Cloud**:

1. Buka https://share.streamlit.io → login pakai GitHub → **Create app**.
2. Repo: `bojjifomo/capstone1`, branch tempat folder ini berada, main file path:
   `tools/lp_copytrade_checker/streamlit_app.py`.
3. (Opsional tapi disaranin) **Advanced settings → Secrets**, isi:
   ```toml
   ALCHEMY_API_KEY = "key-alchemy-lu"
   ```
4. Deploy. Lu dapet URL `https://<nama-app>.streamlit.app`.

### Kolom Alchemy API key

- Kalau `ALCHEMY_API_KEY` ada di Secrets, kolom key di form jadi **opsional**: pengunjung tinggal isi wallet.
  Yang mau lebih cepat atau nggak mau antri bisa isi key sendiri.
- Kalau Secrets kosong, kolom key **wajib** diisi tiap pengunjung.
- Key yang diisi di form cuma dipakai buat scan itu. Nggak disimpan, nggak di-log, dan nggak ditampilin di halaman.

API key Alchemy bisa dibikin gratis di https://dashboard.alchemy.com. Pastikan network yang mau dicek
(misalnya **Robinhood Chain**) udah di-enable di app Alchemy-nya.

### Share ke orang lain

Di app yang udah ke-deploy, klik **Share** (kanan atas) → set app jadi **public**, terus copy link-nya.
Kalau repo-nya private, app defaultnya private juga, jadi pastiin setting ini diubah.

Supaya kuota key Secrets nggak jebol kalau web-nya rame:

- Hasil per wallet di-cache 10 menit.
- Scan yang pakai key Secrets jalan satu-satu (antrian). Pengunjung yang isi key sendiri nggak ikut antri.

### Tema

Warna diatur di `.streamlit/config.toml` (root repo) dan CSS di `streamlit_app.py`:
gelap hijau-teal, card, label mint.

## CLI

```bash
export ALCHEMY_API_KEY=xxxx
python3 checker.py 0xWallet                         # default Robinhood Chain
python3 checker.py 0xWallet --events 15 --window 90
python3 checker.py 0xWallet --network base-mainnet  # chain lain yang ada Uniswap V4
```

CLI cuma pakai standard library Python. Opsi:

- `--events` jumlah tx LP terakhir yang dicek (default 8)
- `--window` berapa detik setelah aksi yang di-scan (default 60)
- `--include-full-range` ikut hitung posisi full range (default diskip karena rawan kebetulan)
- `--rpc` URL Alchemy lengkap, kalau nggak mau pakai env var

## Kecepatan

Free tier Alchemy cuma ngizinin `eth_getLogs` 10 block per request. Robinhood Chain block-nya ~0,1 detik,
jadi window 60 detik = ~60 request per aksi. Satu cek 6 aksi makan kira-kira 1–3 menit. Script otomatis
nyesuaiin ukuran chunk. Dengan key Alchemy berbayar (PAYG), satu aksi cukup 1 request dan cek jadi jauh lebih cepat.

## Batasan

- Hanya Uniswap V4 (PoolManager `ModifyLiquidity`). V3 belum didukung.
- Yang dideteksi cuma aksi yang mint/burn NFT posisi. Decrease sebagian tanpa burn nggak kebaca.
- Follower diidentifikasi dari `tx.from`. Bot yang pakai relayer bisa kebaca sebagai alamat relayer-nya.
- "Dump" = semua transfer keluar token pool (non-stable) dari wallet follower dalam window. Biasanya itu jualan, tapi bisa juga add LP ulang.
