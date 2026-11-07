# DLMM Terminal — Panduan Mulai

Terminal buat LP di **Meteora DLMM (Solana)**. Isinya:

- **Pilih pool**: semua pool DLMM pair SOL, dinilai pakai filter v1 (OPEN / CAREFUL / WAIT / SKIP), lengkap dengan chart.
- **Buka posisi**: ladder **Spot SOL-only 5 slice** dari harga sekarang sampai −70%, isi SOL 1:2:3:4:5.
- **Portfolio**: PnL tiap posisi, posisi harga di range, alert exit, dan tombol Close.

Semua jalan **di komputer lu sendiri**. Private key lu cuma disimpen di file `.env` di laptop lu, nggak dikirim ke mana-mana.

> ⚠️ **Ini uang beneran.** Pakai wallet khusus LP yang isinya cuma sebanyak yang siap lu risikoin. Buat percobaan pertama pakai size kecil (misal ◎0.5). Bukan saran finansial.

---

## 1. Yang perlu di-install (sekali aja)

| Program | Link | Catatan |
|---|---|---|
| **Python 3.9+** | https://www.python.org/downloads/ | Windows: centang **"Add python.exe to PATH"** pas install |
| **Node.js (LTS)** | https://nodejs.org/ | Pilih versi **LTS** |

Cek di Terminal (Mac) / Command Prompt (Windows):

```
python3 --version     (Windows: python --version)
node --version
```

Dua-duanya harus keluar nomor versi.

---

## 2. Siapin 2 hal

**a. Private key wallet (Phantom)**
Phantom → ⚙️ Settings → **Manage Accounts** → pilih akun → **Show Private Key** → masukin password → copy.
Saran: bikin **akun baru** khusus LP, transfer SOL secukupnya ke situ.

**b. RPC Helius (gratis)**
Daftar di https://dashboard.helius.dev → copy **RPC URL** mainnet, bentuknya:
`https://mainnet.helius-rpc.com/?api-key=xxxxxxxx`

---

## 3. Jalanin

1. **Unzip** `DLMM-Terminal.zip` ke folder mana aja (misal Documents).

2. Buka folder `DLMM-Terminal`, lalu double-click:

    - **Mac**: `Mulai DLMM Terminal.command`. Kalau muncul "can't be opened": **klik kanan → Open → Open** (sekali aja).
    - **Windows**: `Mulai DLMM Terminal.bat`. Kalau muncul "Windows protected your PC": **More info → Run anyway**.

3. Pertama kali, dia install Meteora SDK (±1 menit), lalu buka file **`.env`** di TextEdit / Notepad. Isi setelah tanda `=`, **tanpa spasi, tanpa tanda kutip**:

        DLMM_PRIVATE_KEY=<private key dari Phantom>
        SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=<key Helius lu>
        DLMM_WEB_PIN=
        DLMM_BANKROLL_SOL=<total modal LP lu dalam SOL, misal 20>
        DLMM_SIZE_SOL=<size default per posisi, misal 1>
        DLMM_WALLET=

    - `DLMM_BANKROLL_SOL` nentuin **maksimal per posisi** = bankroll × 5% ÷ 0.9. Contoh: 20 SOL → maks ◎1.11 per posisi.
    - `DLMM_WEB_PIN` dan `DLMM_WALLET` boleh kosong.

4. **Save** (Mac: Cmd+S; Windows: Ctrl+S, lalu tutup Notepad). Kalau di Mac, balik ke jendela Terminal dan tekan **Enter**.

5. Browser kebuka sendiri ke **http://localhost:8891**. Biarin jendela Terminal / Command Prompt-nya **tetap kebuka** selama dipakai.

Chip di kanan atas harus nunjukin **🔑 address wallet lu**. Kalau tulisannya **🔒 read-only**, klik chip-nya buat lihat apa yang kurang.

Lain kali tinggal double-click file yang sama lagi.

---

## 4. Cara pakai

**Pilih pool**

- Kolom **ENTRY CHECK** pakai filter v1:
    - **Gate** (wajib lolos semua): bin step 20–250, umur pool ≥ 60 menit, fee APR 24h ≥ 1.000%.
    - **Flag** (cuma warning): pump 1h ≥ 25%, drop 6h ≥ 17%, TVL < $25k, entry jam 00–09 WIB.
- Arti level:
    - 🟢 **OPEN**: semua gate lolos, nggak ada flag.
    - 🟡 **CAREFUL**: gate lolos, ada flag.
    - 🔴 **WAIT**: ada gate yang belum lolos.
    - ⚪ **SKIP**: di luar aturan.
- Centang **v1 pass only** buat nampilin yang OPEN/CAREFUL aja.
- Klik baris pool buat lihat detail + chart.

**Buka posisi**

1. Klik ⚡ (Zap) di pool.
2. Cek tabel 5 slice. Size, jumlah slice dan bottom bisa diubah.
3. Klik **⚡ Buka ladder**, lalu konfirmasi.
4. Tiap slice disimulasi dulu, baru dikirim satu-satu. Kalau SOL kurang, berhenti sebelum ada yang dikirim.

**Portfolio**

- Isinya PnL (SOL), peak, posisi harga di range, dan status exit:
    - **OK**: in range.
    - **ALERT**: PnL ≤ −10% atau harga di 1/3 bawah range.
    - **EXIT**: posisi udah 10 menit di bawah range.
- Tombol **Close** = tarik 100% likuiditas + klaim fee + tutup akun (rent balik).
- Sisa token setelah close **nggak** otomatis di-swap ke SOL. Jual sendiri (Jupiter).

**Kenapa nggak ada take-profit otomatis?** Di data 133 posisi yang jadi dasar filter ini, semua take-profit dan stop-loss otomatis malah bikin hasil lebih jelek dibanding exit manual. Yang terbukti nambah SOL cuma satu: keluar kalau posisi udah 10 menit di bawah range.

---

## 5. Kalau ada masalah

| Masalah | Coba |
|---|---|
| Stuck di "Loading pools…" | Tunggu 30 detik, lalu **Cmd/Ctrl + Shift + R**. Kalau muncul tulisan merah, screenshot. |
| Error `CERTIFICATE_VERIFY_FAILED` | Mac: jalanin `pip3 install certifi`, lalu start ulang |
| Error "Address already in use" | Server lama masih jalan. Tutup jendela Terminal lama, atau restart komputer. |
| Chip 🔒 read-only | Cek `dlmm_terminal/.env`: key dan RPC udah keisi, tanpa spasi atau tanda kutip. Start ulang. |
| "wallet holds X SOL, needs Y" | SOL di wallet kurang buat size + rent (±0.21 SOL per 5 slice, balik pas close; pool yang range-nya belum pernah dipakai kadang minta biaya bin array ±0.07 SOL yang nggak balik) |
| Mau stop | Klik jendela Terminal, tekan **Ctrl + C** |

---

## 6. Keamanan

- Private key **cuma** ada di `dlmm_terminal/.env` di komputer lu. Jangan kirim file itu ke siapa pun, dan jangan screenshot isinya.
- Trading cuma bisa dari browser di komputer itu sendiri. Mau dari HP? Isi `DLMM_WEB_PIN`, jalanin dengan `--host 0.0.0.0`, dan buka lewat Tailscale. HP bakal ditanya PIN tiap kali trading.
- Kalau curiga key bocor: pindahin semua dana ke wallet baru **sekarang juga**.
