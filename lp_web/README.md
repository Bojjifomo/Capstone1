# LP Web

Versi web dari widget Scriptable lu (**RH Portfolio** dan **RH Pool**), jalan di
browser Mac. Isinya sama persis kayak di iPhone: tabel posisi, Close / Claim /
Re-enter, Close All, cari pool, tombol zap ⚡️, konfirmasi, practice mode, semuanya.

Caranya: script lu **nggak ditulis ulang**. Halaman ini nyediain "Scriptable versi
browser" (UITable, Alert, Request, FileManager, dst.), terus ngejalanin file
`RH Pool.js` / `RH Portfolio.js` yang asli. Jadi tiap lu update script di
Scriptable, versi web otomatis ikut.

## Buka di Mac

1. Pastikan iCloud Drive di Mac aktif (akun yang sama kayak iPhone). Script
   Scriptable lu ada di
   `~/Library/Mobile Documents/iCloud~dk~simonbs~Scriptable/Documents`.
2. Clone repo ini sekali:
   ```bash
   git clone https://github.com/Bojjifomo/Capstone1.git
   ```
3. Double-click **`lp_web/Buka LP Web.command`** di Finder
   (atau dari Terminal: `python3 lp_web/server.py`).
   Browser kebuka sendiri ke **http://127.0.0.1:4663**. Pilih RH Portfolio
   atau RH Pool.
4. Biarin jendela Terminal-nya kebuka selama lu pake. Tutup = webnya mati.

Pertama kali:
- Kalau macOS nanya *"Terminal would like to access files in iCloud Drive"*,
  pilih Allow.
- Kalau `.command` diblok Gatekeeper, klik kanan > Open, sekali aja.
- Kalau `python3` belum ada, macOS bakal nawarin install Command Line Tools.
  Ikutin aja.

Port 4663 sengaja diambil dari chain id Robinhood biar nggak bentrok sama app lain. Kalau tetap kepake, server otomatis pindah ke port kosong berikutnya.

Tips: bookmark `http://127.0.0.1:4663/run/RH%20Portfolio` biar langsung masuk.

## Key dan transaksi

- Key (Alchemy, private key, Blockscout, Uniswap) dibaca dari **RH Secrets** di
  folder Scriptable iCloud, sama kayak di HP. Nggak ada key yang masuk repo ini.
- `PRACTICE_MODE: true` di RH Secrets berarti semua tombol cuma simulasi, nggak
  ngirim apa-apa. Ganti ke `false` kalau udah yakin, sama kayak di HP.
- Tiap zap / close tetap lewat dialog konfirmasi script-nya. Tombol yang beneran
  ngirim transaksi warnanya merah, dan **Enter nggak bisa** mencet tombol itu,
  harus diklik.
- Server cuma dengerin `127.0.0.1` (nggak bisa diakses dari device lain), dan
  setiap request butuh token acak yang dibikin tiap kali server start.

## Kalau script-nya nggak di iCloud

Taruh `RH Pool.js`, `RH Portfolio.js`, `RH Secrets.js` di `lp_web/local/scripts/`
(folder ini di-gitignore), atau tunjuk folder lain:

```bash
LP_SCRIPTS_DIR=~/folder/script python3 lp_web/server.py
```

## Yang beda dari di HP

- Nggak ada widget home screen. Yang dibuka selalu tampilan tabel interaktifnya.
- Cache script (data pool, snapshot, dll.) disimpan di `lp_web/local/files/`,
  bukan di HP, jadi run pertama di Mac mulai dari nol dan agak lama.
- Link `scriptable:///run/...` dibuka sebagai halaman web ini juga. Link lain
  (DexScreener, LP Agent) kebuka di tab baru.
- Tombol **Log** di pojok kanan atas nunjukin `console.log` dari script.

## Isi folder

| File | Fungsi |
| --- | --- |
| `server.py` | Server lokal (Python standar, tanpa install apa-apa). Nyajiin halaman, baca script dari iCloud, dan nerusin request API script (browser nggak bisa langsung manggil DexScreener/RPC karena CORS). |
| `static/scriptable.js` | API Scriptable versi browser. |
| `static/run.html`, `static/index.html`, `static/style.css` | Halaman. |
| `Buka LP Web.command` | Double-click untuk start. |
| `build_bundle.py` | Bikin ZIP siap kirim (lihat bagian di atas). |
| `tests/test_server.py` | Tes guard server: `python3 -m unittest lp_web/tests/test_server.py` |

## Bikin paket buat dikirim ke orang lain

```bash
python3 lp_web/build_bundle.py \
  --scripts "path/RH Pool.js" "path/RH Portfolio.js" \
  --secrets "path/RH Secrets.js" --out "LP Web.zip"
```

Hasilnya satu ZIP berisi web + script + `RH Secrets.js`. Penerima tinggal unzip,
isi `RH Secrets.js`, terus double-click `Buka LP Web.command` (Mac) atau
`Buka LP Web.bat` (Windows). Script di dalam ZIP (`scripts/`) yang dipake, bukan
iCloud. Build-nya nolak jalan kalau ada key yang keisi di secrets atau di script,
atau kalau `PRACTICE_MODE` di secrets bukan `true`.
