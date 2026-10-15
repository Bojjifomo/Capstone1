# Vault Tracker — live posisi + alert copy-trade (Hedgin / Meteora DLMM)

Tool ini memantau sebuah vault Hedgin (atau wallet Solana mana pun) yang LP atau
pasang limit order di **Meteora DLMM**, menampilkan isi vault seperti panel **Allocation** di Hedgin, dan
mengirim **alert setiap kali leader buka / tutup / ubah posisi**. Jadi kamu bisa
copy posisinya dengan **range LP kamu sendiri**.

Semua data dibaca langsung dari chain Solana. Tool ini tidak butuh API Hedgin,
jadi tetap jalan walaupun web Hedgin down atau berubah.

```
╭──────────────── Allocation — LEADER 5ZtJ…1gsQ ─────────────────╮
│ Live value $2,490.07   by position                             │
│ ███████████████████████████████████████████████████████████    │
│ ●  SOL-USDC  DLMM  in range                 $1,513.71    60.8% │
│ ●  USDC  spot  963.1285                       $963.13    38.7% │
│ ●  SOL  spot  0.1223                           $13.23     0.5% │
╰────────────────────────────────────────────────────────────────╯
🟢 [LEADER] POSISI BARU: SOL-USDC
    Range: 107.6483 – 109.7121 USDC per SOL (-0.4% / +1.5%)
    Harga: 108.0796 · bin step 10 · 20 bins · IN RANGE
    Isi: 12.99 SOL + 109.0114 USDC (≈ $1,512.02)
    📋 Range kamu (pct:-5,5): 102.6057 – 113.5046 (-5.1% / +5.0%) · 102 bins · deposit SOL + USDC
       Kamu belum punya posisi di pool ini
    🔗 Meteora: https://app.meteora.ag/dlmm/BGm1tav58oGcsQJehL9WXBFXF7D27vZsKefj4xJKD5Y
```

## Cara kerja

1. Baca histori transaksi alamat vault (`getSignaturesForAddress` + `getTransaction`).
2. Di tiap transaksi, decode event program DLMM (`PositionCreate`, `AddLiquidity`,
   `RemoveLiquidity`, `Rebalancing`, `PositionClose`, `ClaimFee`, `PlaceLimitOrder`,
   `CancelLimitOrder`, `CloseLimitOrder`). Transaksi tanpa
   event DLMM tapi saldo tokennya berubah dicatat sebagai swap (biasanya lewat Jupiter)
   atau transfer.
3. Baca akun posisi, limit order, pool, dan bin array yang masih terbuka, lalu hitung
   isi token, fee yang belum diklaim, range harga, status in/out of range, dan berapa
   persen limit order sudah terisi. Rumusnya sama dengan SDK resmi `@meteora-ag/dlmm`.
4. Ulangi tiap N detik. Kalau ada event baru, alert dikirim ke Telegram, Discord,
   dan/atau console.

Cara ini jalan di RPC biasa (Alchemy free, public RPC) karena tidak butuh
`getProgramAccounts`. Kalau RPC kamu mengizinkan `getProgramAccounts` (Helius, QuickNode),
tambahkan `--gpa` sebagai cara deteksi kedua.

## Cara termudah: lewat website (lokal)

```bash
python -m vault_tracker web
```

Browser terbuka di `http://127.0.0.1:8787`. Dari halaman itu kamu bisa mengisi
Pengaturan (alamat vault, wallet kamu, copy mode, Telegram, RPC), klik **Start/Stop**,
dan melihat Allocation, posisi & limit order, sync copy-trade, aktivitas, dan wallet
manager. Halamannya refresh sendiri tiap 2 detik. Tombol **Panduan** di kanan atas
membuka checklist "Mulai cepat" (tercentang otomatis sesuai pengaturanmu), daftar fitur,
dan panduan singkat: cari alamat vault, copy di Meteora, copy mode, arti alert, wallet
manager, akses dari HP, dan masalah umum.

- Pengaturan disimpan ke `.env`, jadi perintah CLI di bawah memakai pengaturan yang sama.
- Server web hanya bisa dibuka dari komputer ini. Untuk membuka dari HP di Wi-Fi yang
  sama, jalankan `python -m vault_tracker web --host 0.0.0.0`, lalu buka link
  `http://<ip>:8787/?token=…` yang tampil di terminal. Tanpa token, halaman menolak akses.
- "Jaga komputer tetap menyala" mencegah sleep selama tracker jalan (macOS lewat
  `caffeinate`, Windows lewat `SetThreadExecutionState`). Laptop harus dicolok dan layar
  terbuka. MacBook yang ditutup tetap tidur.
- `--start` langsung menjalankan tracker saat server dinyalakan. `--port` mengganti port.
- Website ini jalan di komputer kamu sendiri. Kalau terminalnya ditutup, website dan
  alert ikut berhenti.

## Install

```bash
cd Capstone1
pip install -r vault_tracker/requirements.txt
cp vault_tracker/.env.example .env      # lalu isi .env
```

## Cari alamat vault

Tool ini butuh **alamat on-chain** vault (base58). Slug `0xmegumii-9` tidak bisa dipakai.
Ada beberapa cara mendapatkannya:

- Di halaman vault Hedgin, cari tombol copy alamat atau link Solscan untuk vault.
- Buka salah satu transaksi deposit kamu ke vault itu di Solscan. Akun vault-nya
  ada di daftar akun transaksi tersebut.
- Buka posisi DLMM vault di Meteora atau Solscan. Field **owner** di posisi itu
  adalah alamat yang dipakai.

Kalau yang kamu masukkan alamat state vault, tool otomatis ikut melacak alamat owner
posisi DLMM-nya (PDA vault). Kalau yang kamu masukkan alamat **token share** vault
(token yang kamu terima setelah deposit), tool menggantinya dengan alamat vault
(mint authority token itu) dan menyimpannya di Pengaturan.

## Pakai

```bash
# lihat allocation + posisi sekali
python -m vault_tracker snapshot --vault <ALAMAT_VAULT>

# dashboard live + alert (refresh tiap 30 detik)
python -m vault_tracker watch --vault <ALAMAT_VAULT> --copy-mode pct:-10,10

# + bandingkan dengan wallet kamu sendiri (status sudah copy / belum)
python -m vault_tracker watch --vault <ALAMAT_VAULT> --follower <WALLET_KAMU>

# riwayat open/close/rebalance/swap leader
python -m vault_tracker history --vault <ALAMAT_VAULT> --history 1000

# cari wallet manager (wallet asli yang menandatangani trade vault)
python -m vault_tracker signers --vault <ALAMAT_VAULT> --history 1000

# untuk server / VPS (tanpa dashboard, cuma alert)
nohup python -m vault_tracker watch --no-dashboard > tracker.log 2>&1 &
```

Opsi penting:

| Opsi | Fungsi |
|---|---|
| `--copy-mode` | Cara menghitung range kamu (lihat di bawah). Default `mirror`. |
| `--follower` | Wallet kamu. Dipakai untuk tabel sync dan peringatan "leader sudah close, kamu masih punya posisi". |
| `--by token` | Allocation per token, seperti tab "By token" di Hedgin. |
| `--interval` | Detik antar refresh. Default 30. |
| `--history` | Jumlah transaksi lama yang dibaca saat pertama jalan. Default 300. |
| `--min-usd` | Abaikan alert add/remove/swap yang nilainya di bawah angka ini. Default $5. Gunanya menyaring auto-compound. |
| `--alerts` | Tipe alert yang dikirim, misalnya `OPEN,CLOSE,REBALANCE`. |
| `--gpa` | Cari posisi juga lewat `getProgramAccounts` (perlu RPC yang mendukung). |
| `--show-dust` | Tampilkan semua token spot, termasuk token airdrop spam. |

### Copy mode: range LP kamu sendiri

| Mode | Contoh | Hasil |
|---|---|---|
| `mirror` | `--copy-mode mirror` | Bin dan range harga persis sama dengan leader. |
| `pct:LOW,HIGH` | `--copy-mode pct:-10,10` | Range dari -10% sampai +10% dari harga sekarang di pool yang sama. |
| `scale:F` | `--copy-mode scale:0.5` | Range leader dikecilkan atau dibesarkan F kali relatif ke harga sekarang. Bentuknya tetap, jadi kalau leader one-sided di bawah harga (beli dip), range kamu juga. |

Saran range muncul di alert dan dashboard, lengkap dengan harga bawah/atas,
jumlah bin, token yang perlu didepositkan, dan link pool di Meteora. Posisinya kamu
buka sendiri di Meteora. Tool ini **tidak** mengeksekusi transaksi dan tidak butuh
private key.

### Tipe alert

| Alert | Kapan |
|---|---|
| 🟢 `OPEN` | Leader membuka posisi DLMM baru. Alert berisi range, isi, dan saran range kamu. |
| 🔴 `CLOSE` | Leader menutup posisi. Kalau kamu masih punya posisi di pool itu, ada peringatan. |
| ➕ `ADD` / ➖ `REMOVE` | Leader menambah atau menarik likuiditas. |
| 🔁 `REBALANCE` | Range posisi leader dipindah. |
| 💱 `SWAP` | Leader beli/jual token spot. Alert berisi mint token-nya. |
| ⚠️ `OUT_OF_RANGE` / ✅ `IN_RANGE` | Harga keluar atau masuk lagi ke range posisi (leader dan kamu). |
| 📌 `ORDER_PLACE` | Leader memasang limit order (jual/beli) di pool DLMM. Alert berisi harga order dan saran untuk kamu. |
| 🎯 `ORDER_FILL` | Limit order leader terisi, dicek tiap kenaikan 5% dan saat penuh. |
| ✖️ `ORDER_CANCEL` / `ORDER_CLOSE` | Limit order dibatalkan/ditutup, lengkap dengan jumlah yang ditarik. |
| `CLAIM`, `TRANSFER_IN`, `TRANSFER_OUT` | Mati secara default. Nyalakan lewat `--alerts`. |

## Wallet manager (wallet asli di balik vault)

Setiap transaksi trade vault Hedgin (DLMM, swap) ditandatangani wallet manager.
Perintah `signers` mengurutkan semua penanda tangan transaksi vault berdasarkan
jumlah transaksi trade yang mereka tanda tangani. Urutan teratas hampir pasti
manager-nya. Dashboard `watch` juga menampilkan baris ini.

Wallet itu bisa kamu lacak di terminal kedua:
`python -m vault_tracker watch --vault <WALLET_MANAGER> --name MANAGER` (alertnya jadi
bertanda `[MANAGER]`, bukan `[LEADER]`). Kalau manager juga trading
pakai dana pribadi, posisinya muncul di situ. Tapi tidak ada jaminan: wallet signer
bisa saja hot wallet khusus vault yang tidak punya posisi pribadi.

## Wallet kembar (untuk tools copytrade lain)

Banyak tools copytrade hanya bisa mengikuti wallet biasa, bukan vault. Perintah ini
mencari wallet yang membuka posisi di pool dan range yang sama dengan vault, di sekitar
waktu vault membuka:

```bash
python -m vault_tracker mirrors --window 600 --events 15
```

Untuk tiap posisi yang dibuka vault, tool membaca transaksi pool ±`--window` detik,
mengumpulkan posisi yang dibuat wallet lain, lalu menilai tiap wallet: berapa kali cocok,
berapa kali range-nya sama persis, overlap range rata-rata, dan jeda median dari waktu
vault membuka. Wallet yang konsisten diberi label:

- **SETELAH vault**: kemungkinan orang lain yang sudah copy vault ini.
- **SEBELUM vault**: membuka lebih dulu. Bisa jadi sumber sinyal atau wallet pribadi manager.
- **manager**: wallet itu juga menandatangani transaksi vault.

Kecocokan sekali dengan overlap kecil disembunyikan (`--all` untuk melihat). Di website,
fitur ini ada di kartu "Wallet kembar".

## Bot copytrade (otomatis)

Bot ini membuka posisi DLMM di **wallet bot kamu** setiap vault membuka posisi baru
(memakai "Range kamu" dari copy mode), lalu menutupnya saat vault menutup posisi itu.

Mode `COPY_RUN`:

| Mode | Yang terjadi |
|---|---|
| `off` (default) | Bot mati. |
| `dry` | Bot membuat rencana (ukuran, range, swap yang dibutuhkan) dan menyimulasikan transaksinya di chain. Tidak ada yang ditandatangani atau dikirim. |
| `live` | Bot swap lewat Jupiter, membuka posisi, dan menutupnya **tanpa menunggu persetujuan**. |

Mulai:

```bash
python -m vault_tracker copy-wallet new      # buat wallet bot baru (atau: copy-wallet import)
python -m vault_tracker copy-wallet show     # alamat + saldo
```

Kirim SOL secukupnya ke alamat wallet bot: modal, ditambah sekitar 0.2 SOL untuk fee dan rent
(rent posisi ~0.057 SOL kembali saat posisi ditutup). Lalu di website, buka kartu
**Copytrade bot**, pilih **DRY RUN**, Simpan, dan Start. Kalau rencana dan simulasi di log
bot sudah masuk akal, baru ganti ke **LIVE**. Bisa juga lewat terminal:
`python -m vault_tracker watch --copy dry`.

Pengaman yang selalu aktif:

- Ukuran per posisi (`COPY_SIZE_USD`, atau `COPY_SIZE_RATIO` × nilai posisi vault) dan batas
  maksimal `COPY_MAX_USD`.
- Maksimal posisi bot terbuka `COPY_MAX_OPEN`, cadangan SOL `COPY_RESERVE_SOL` yang tidak dipakai.
- Batal kalau harga sudah bergerak lebih dari `COPY_MAX_PRICE_MOVE` % sejak vault membuka, atau
  posisi vault dibuka lebih dari `COPY_MAX_EVENT_AGE` detik lalu (misalnya setelah laptop mati).
- Batal kalau perlu membuat lebih dari `COPY_MAX_NEW_BIN_ARRAYS` bin array baru (~0.07 SOL
  masing-masing), kalau range kamu lebih dari 70 bins, atau kalau token memakai transfer hook.
- Bot hanya menutup posisi yang dibukanya sendiri.
- Tiga kegagalan berturut-turut: bot dijeda sampai kamu klik **Lanjutkan bot**
  (atau `python -m vault_tracker copy-resume`).

Yang tidak di-copy otomatis: limit order, rebalance, dan tambah/tarik likuiditas sebagian. Bot
mengirim pemberitahuan supaya kamu bisa mengikutinya manual.

Perintah lain: `copy-status` (posisi + log bot), `copy-close <alamat_posisi>` (tutup satu posisi bot).

Risiko: bot ikut masuk ke token apa pun yang dibeli vault, termasuk token yang bisa langsung jatuh.
Pakai wallet khusus dengan dana yang siap hilang. Private key hanya disimpan di file keypair
(izin 600) dan tidak pernah dikirim ke mana pun, termasuk ke halaman web. Swap Jupiter dan
pengiriman transaksi live belum pernah diuji dengan dana sungguhan oleh pembuat tool ini.
Jalankan DRY RUN dulu, lalu LIVE dengan ukuran kecil.

## Alert Telegram

1. Chat [@BotFather](https://t.me/BotFather), kirim `/newbot`, lalu salin token-nya ke `TELEGRAM_BOT_TOKEN`.
2. Buka bot kamu di Telegram dan kirim `/start`. Di halaman web, klik **Cari otomatis** di
   samping Telegram chat id, lalu Simpan. Tanpa web: buka
   `https://api.telegram.org/bot<TOKEN>/getUpdates` dan salin angka `chat.id` ke `TELEGRAM_CHAT_ID`.
   Chat id itu angka (misalnya `123456789`), bukan nama atau username bot.
3. Klik **Tes alert** di web, atau jalankan `python -m vault_tracker test-alert`.

Untuk Discord, isi `DISCORD_WEBHOOK_URL`. Semua alert juga dicatat di
`.vault_tracker/alerts.jsonl`.

## RPC

- Paling gampang pakai RPC gratis dari [Helius](https://helius.dev) atau Alchemy,
  lalu isi `SOLANA_RPC_URL`. Public RPC (`api.mainnet-beta.solana.com`) bisa dipakai
  tapi cepat kena rate limit.
- Run pertama membaca `--history` transaksi, jadi bisa makan 1–3 menit di RPC
  gratis. Run berikutnya melanjutkan dari state di `.vault_tracker/`.

## Batasan

- Tanpa `--gpa`, posisi lama yang **tidak tersentuh sama sekali** di N transaksi
  terakhir tidak akan ketemu. Kalau vault-nya ramai deposit/withdraw, naikkan `--history`.
- Harga USD diambil dari Jupiter Price API. Kalau Jupiter tidak bisa diakses, harga
  SOL diambil dari pool DLMM SOL-USDC, dan token lain dari pool DLMM tempat token itu
  di-LP. Token spot tanpa pool ditampilkan `$?`.
- Nilai di sini bisa sedikit beda dengan Hedgin, karena NAV Hedgin dihitung
  off-chain oleh keeper mereka.
- Alert datang setelah transaksi leader final (sekitar 15–30 detik ditambah interval
  polling). Harga di pool bisa sudah bergerak saat kamu copy.
- Ini alat bantu monitoring, bukan saran finansial. DLMM bisa kena impermanent loss.

- Limit order baru diuji dengan data sintetis yang mengikuti layout IDL dan rumus SDK
  Meteora. Perhitungan nilai limit order yang terisi sebagian belum dicek terhadap
  order asli. Bandingkan dengan angka di Hedgin saat pertama kali dipakai.

## Test

```bash
python -m unittest discover -s vault_tracker/tests -t .
```
