# DLMM Terminal di iPhone — Panduan Install

Versi iPhone dari DLMM Terminal jalan di **Scriptable** (app gratis di App Store). Isinya sama dengan versi web:

- **Pilih pool**: semua pool Meteora DLMM pair SOL, dinilai filter v1 (OPEN / CAREFUL / WAIT / SKIP), mode **ALL / V1 / 📡 PING / ★ watchlist**, fees 1h / 4h / 24h.
- **📡 PING** = filter signal entry yang sama dengan Mac: umur token 1h–250h, mcap $400k–$10M, vol 24h ≥ $50k, total fees ≥ 50 SOL, tanpa pool mati→hidup, top 10 urut 1h Vol/TVL.
- **◎ SOL / $ USD / Rp**: semua angka (TVL, fees, mcap, posisi, PnL) bisa dibaca dalam SOL, dolar, atau rupiah.
- **⚙ Settings** di dalam app: ladder, risk, gate, flag, filter PING, exit rules. Nggak perlu edit `MET Secrets` lagi.
- **Buka posisi**: ladder **Spot SOL-only 5 slice** sampai −70% (isi 1:2:3:4:5).
- **Portfolio**: PnL, alert exit, tombol close + swap ke SOL.
- **Widget** di home screen.

Private key lu cuma dipakai di HP lu sendiri buat nandatanganin transaksi. Nggak ada server.

> ⚠️ **Ini uang beneran.** Pakai wallet khusus LP yang isinya cuma sebanyak yang siap lu risikoin. Awalnya biarin **Practice Mode** nyala (semua tombol cuma simulasi). Bukan saran finansial.

---

## Yang lu butuh

| Apa | Dari mana |
|---|---|
| **Scriptable** | App Store (gratis) |
| **4 file script** | Paket `DLMM-iPhone.zip` |
| **RPC Helius (gratis)** | https://dashboard.helius.dev → daftar → copy **API key** (cuma bagian setelah `api-key=`) |
| **Wallet khusus LP** | Phantom: bikin akun baru, isi SOL secukupnya (buat ladder + ±0.21 SOL rent per 5 slice yang balik pas close) |

---

## Langkah 1 — Pasang Scriptable dan pindahin 4 file

1. Install **Scriptable** dari App Store, buka sekali.
2. Terima file `DLMM-iPhone.zip` di iPhone (AirDrop, WhatsApp, atau iCloud Drive). Di app **Files**, **tap ZIP-nya** sekali: otomatis ke-unzip jadi folder `DLMM-iPhone`.
3. Di dalamnya ada folder **`Scriptable`** berisi 4 file: `MET Secrets.js`, `DLMM Rules.js`, `MET Pool.js`, `MET Portfolio.js`.

4. Pindahin **keempat file itu** ke folder Scriptable:

    - Di Files, buka folder `Scriptable`, tekan **Select** → pilih 4 file → **Move** (atau tahan lama → Move) → **iCloud Drive › Scriptable** (atau **On My iPhone › Scriptable** kalau lu nggak pakai iCloud buat Scriptable).

5. Buka app **Scriptable**. Keempat script harus muncul di daftar. **Nama script harus persis begitu**, jangan diubah (huruf besar-kecil dan spasi ngaruh).

> Cara lain lewat Mac: taruh keempat file di `iCloud Drive › Scriptable` lewat Finder, nanti muncul sendiri di HP.

> Nama file ikut jadi nama script. Kalau iOS nambahin " 2" atau "(1)" di belakangnya, ganti namanya jadi persis seperti di atas (di Scriptable: tahan script → **Rename**).

---

## Langkah 2 — Isi `MET Secrets` (di HP, bukan di Mac)

Isi key **langsung di HP** supaya private key nggak pernah lewat komputer.

1. Di Scriptable, buka editor `MET Secrets`: **tap ikon ⋯ (tiga titik) di pojok kartu `MET Secrets`**. (Tap di kartunya langsung = *menjalankan* script; buat edit pakai ⋯.)
2. Cari baris-baris ini dan isi nilainya (di dalam tanda kutip, tanpa spasi, jangan hapus koma di ujung):

        HELIUS_API_KEY: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        SOLANA_PRIVATE_KEY: "<private key base58 dari Phantom>",
        PRACTICE_MODE: true,
        MET_ZAP_PRESETS: [],
        BANKROLL_SOL: 20,
        DEFAULT_SIZE_SOL: 1,

    - **`HELIUS_API_KEY`**: cuma key-nya, bukan seluruh URL.
    - **`SOLANA_PRIVATE_KEY`**: di app Phantom → ⚙️ **Settings → Manage Accounts →** pilih akun LP → **Show Private Key** → copy → paste di sini. Pakai akun LP khusus, bukan akun utama.
    - **`PRACTICE_MODE: true`**: semua tombol cuma simulasi. Jangan diubah dulu.
    - **`MET_ZAP_PRESETS: []`**: kosong = pakai ladder terminal (Spot, 5 slice, −70%). Kalau diisi preset sendiri, itu yang dipakai.
    - **`BANKROLL_SOL`**: total modal LP lu (SOL). Ini nentuin **maksimal per posisi** = bankroll × 5% ÷ 0.9. Contoh: 20 SOL → maks ◎1.11.
    - **`DEFAULT_SIZE_SOL`**: ukuran tombol besar (tombol kecil = setengahnya). Dibatasi maksimal di atas.

3. Tap **Done** buat nyimpen.

> Catatan: kalau iCloud nyala buat Scriptable, file script (termasuk `MET Secrets`) ikut kesimpen di iCloud Drive lu. Makanya wallet-nya harus wallet khusus LP dengan dana secukupnya. Jangan screenshot atau kirim isi file ini ke siapa pun.

---

## Langkah 3 — Coba dalam Practice Mode

- **Jalanin:** di Scriptable, **tap kartu `MET Pool`** (tap kartu = jalanin). Load pertama ±10–30 detik.
- **Yang harus muncul:** header (jumlah pool, harga SOL, wallet lu + saldonya); tombol **ALL · V1 · 📡PING · ★**; baris **◎ SOL · $ USD · Rp · ⚙ Settings**; baris **Fees window** 1h · 4h · 24h; lalu daftar pool, tiap pool dengan badge **● OPEN / CAREFUL / WAIT / SKIP** + alasan. **Tap baris badge** buat lihat semua alasan.
- **Coba 📡PING:** tap **📡PING** buat lihat top 10 pool yang lolos filter signal entry (sama dengan Mac). Baris di atas tabel nulis filternya ("Filtered by: …"). Tiap pool PING dapat nomor `📡1`, `📡2`, … di namanya, juga di MET Portfolio kalau lu punya posisi di situ.
- **★ Watchlist:** tap baris **● verdict** di bawah pool → **☆ Tambah ke watchlist**. Lihat semua di mode **★**.
- **◎ SOL / $ USD / Rp:** tombol di bawah mode. Pilihan ini juga dipakai MET Portfolio dan widget.
- **⚙ Settings:** tap **⚙ Settings** → tap baris yang mau diubah (size, slice, bottom, bentuk, bankroll, gate, flag, filter PING, exit rules) → isi → **Simpan**. Tanda • = beda dari `MET Secrets`. **Balik ke MET Secrets** / **Reset semua** buat balikin. Setting ini dipakai MET Pool dan MET Portfolio.
- **Tes zap:** tap tombol **⚡** di satu pool → layar konfirmasi nunjukin gate, flag, cap loss budget, dan tabel slice → pilih bentuk **Practice · Spot ★**. Hasilnya cuma simulasi: nggak ada yang dikirim.
- **Portfolio:** tap kartu **`MET Portfolio`** → harus nunjukin wallet lu (kosong kalau belum punya posisi).

Kalau semua tampil normal tanpa error, lanjut.

---

## Langkah 4 — Pasang widget (opsional)

1. Home screen → tahan lama area kosong → **+** → cari **Scriptable** → pilih ukuran **Medium** atau **Large** → **Add Widget**.
2. Tap widget-nya → **Script: `MET Pool`** → **When Interacting: Run Script**.
    - Parameter (opsional): `300K` = cuma tampilin token dengan mcap ≥ $300K.
3. Ulangi buat **`MET Portfolio`** (widget kedua). Parameter opsional: alamat wallet buat dipantau.

Widget refresh sendiri tiap ±15 menit (iOS yang ngatur). Widget **nggak pernah** ngirim transaksi.

**Notifikasi exit:** buka iOS **Settings → Notifications → Scriptable → Allow Notifications**. MET Portfolio kirim notif sekali tiap kejadian (PnL ≤ −10%, harga di 1/3 bawah range, atau 10 menit di bawah range). Tap notif buat buka konfirmasi close + swap.

---

## Langkah 5 — Trading beneran

Kalau Practice Mode udah lu coba dan hasilnya masuk akal:

1. Pastiin wallet LP punya SOL: ukuran ladder + ±0.21 SOL buat rent 5 posisi (balik pas close) + sedikit reserve.
2. Di `MET Secrets`, ubah `PRACTICE_MODE: true` jadi `false`, **Done**.
3. Mulai dari **size kecil** (misal ◎0.5). Zap pool yang OPEN/CAREFUL → konfirmasi → ladder kebuka. Tiap slice jadi posisi sendiri; hasilnya kelihatan di `MET Portfolio`.
4. **Re-enter** di `MET Portfolio` sekarang juga jalan saat harga udah masuk ladder (in range): posisi di-close, token yang keluar dijual ke SOL, lalu ladder dibuka lagi di harga sekarang. Catatan: di HP satu posisi maksimal 70 bin, jadi kalau range-nya butuh lebih, jumlah slice dinaikin dan konfirmasi zap kasih peringatan (Mac bisa buka 1 slice lebar).
5. Tutup posisi dari `MET Portfolio` (**Close**, atau **Exit → SOL** kalau exit rule nyala). **Close and swap to SOL** jual sisa token, nolak kalau harganya lebih dari 5% di bawah harga pool.

Aturan yang dipakai (sama dengan Mac): gate = pair SOL, bin step 20–250, umur pool ≥ 60 menit, fee APR 24h ≥ 1.000%. Flag (warning aja) = pump 1h ≥ 25%, drop 6h ≥ 17%, TVL < $25k, entry jam 00–09 WIB. Take-profit dan stop-loss otomatis **sengaja mati**: di data 133 posisi yang jadi dasarnya, semuanya bikin hasil lebih jelek dibanding exit manual.

---

## Kalau ada masalah

| Masalah | Coba |
|---|---|
| `Script "DLMM Rules" not found` | Nama script harus persis `DLMM Rules`. Cek spasi dan huruf besar. |
| `Open buttons are off` (tombol ⚡ nggak ada) | `HELIUS_API_KEY` dan `SOLANA_PRIVATE_KEY` di `MET Secrets` belum keisi atau salah. Key Helius = cuma key-nya. |
| Daftar kosong / `Couldn't load` | Cek internet, tap baris status buat refresh. Layar tetap nampilin data terakhir (ditandai "stale"). |
| `V1 PASS 0` | Lagi nggak ada pool yang lolos. Cek `ALL` buat lihat alasannya. |
| `📡PING 0` | Normal kalau nggak ada pool yang cocok saat ini. Tap status buat scan ulang beberapa menit lagi, atau longgarin filter di ⚙ Settings. |
| Setting nggak kepakai | Setting disimpan di HP (`dlmm_settings.json`). Kalau mau balik ke `MET Secrets`: ⚙ Settings → **Reset semua**. |
| Widget "No data yet" | Buka `MET Pool` sekali di app biar ada data, baru widget keisi. |
| "wallet holds X SOL, needs Y" | SOL di wallet kurang buat size + rent + reserve. |
| Tombol ⚡ tampil `◎0.5 BA` / `3×` (preset lama) | Di `MET Secrets`, set `MET_ZAP_PRESETS: []`. |
| Layar aneh atau error merah | Screenshot (tutup bagian key dulu) dan kirim ke yang ngasih paket ini. |

---

## Update ke versi baru

Pindahin file `DLMM Rules.js`, `MET Pool.js`, `MET Portfolio.js` yang baru ke folder Scriptable, **timpa** yang lama. **Jangan timpa `MET Secrets`**: key dan setting lu ada di situ.

## Keamanan

- Private key cuma ada di `MET Secrets` di HP lu. Jangan kirim file itu ke siapa pun.
- Kalau HP hilang atau curiga key bocor: pindahin semua dana ke wallet baru dari Phantom di perangkat lain.
- Pakai wallet khusus LP dengan dana secukupnya. Ini kode yang ngirim transaksi pakai key lu; baca dulu sebelum dipercaya.
