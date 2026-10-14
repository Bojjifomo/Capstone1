# Vault Tracker — live posisi + alert copy-trade (Hedgin / Meteora DLMM)

Tool ini memantau sebuah vault Hedgin (atau wallet Solana mana pun) yang LP di
**Meteora DLMM**, menampilkan isi vault seperti panel **Allocation** di Hedgin, dan
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
   `RemoveLiquidity`, `Rebalancing`, `PositionClose`, `ClaimFee`). Transaksi tanpa
   event DLMM tapi saldo tokennya berubah dicatat sebagai swap (biasanya lewat Jupiter)
   atau transfer.
3. Baca akun posisi, pool, dan bin array yang masih terbuka, lalu hitung isi token,
   fee yang belum diklaim, range harga, dan status in/out of range. Rumusnya sama
   dengan SDK resmi `@meteora-ag/dlmm`.
4. Ulangi tiap N detik. Kalau ada event baru, alert dikirim ke Telegram, Discord,
   dan/atau console.

Cara ini jalan di RPC biasa (Alchemy free, public RPC) karena tidak butuh
`getProgramAccounts`. Kalau RPC kamu mengizinkan `getProgramAccounts` (Helius, QuickNode),
tambahkan `--gpa` sebagai cara deteksi kedua.

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
posisi DLMM-nya (PDA vault).

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
| `CLAIM`, `TRANSFER_IN`, `TRANSFER_OUT` | Mati secara default. Nyalakan lewat `--alerts`. |

## Alert Telegram

1. Chat [@BotFather](https://t.me/BotFather), kirim `/newbot`, lalu salin token-nya ke `TELEGRAM_BOT_TOKEN`.
2. Kirim pesan apa saja ke bot kamu, lalu buka
   `https://api.telegram.org/bot<TOKEN>/getUpdates` dan salin `chat.id` ke `TELEGRAM_CHAT_ID`.
3. Jalankan `python -m vault_tracker test-alert`.

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

## Test

```bash
python -m unittest discover -s vault_tracker/tests -t .
```
