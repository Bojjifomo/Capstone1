# DLMM Web di Mac + HP — Panduan Setup

Satu server jalan di Mac. Mac dan HP buka **halaman yang sama**. Semua yang lu atur kesimpen di Mac, jadi dua-duanya sama persis:

- ⚙ Settings (ladder, risk, gate, filter PING, exit rules)
- ★ watchlist
- 🎯 auto exit per ladder
- tampilan: ◎ SOL / $ USD, mode (ALL / V1 / PING / WATCH), fee window, sort, TVL

Private key cuma ada di Mac (`.env`). HP nggak pernah lihat key, cuma kirim perintah ke Mac dan ditanya **PIN**.

> ⚠️ **Uang beneran.** Pakai wallet khusus LP. **Auto exit cuma jalan selama server di Mac nyala.** Mac tidur = nggak ada yang ngecek.

---

## 1. Sekali aja: siapin Mac

Buka **Terminal** (Cmd+Spasi → ketik Terminal), jalanin satu-satu:

```bash
xcode-select --install
```

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

```bash
brew install python node git
```

Ambil kodenya (kalau belum ada):

```bash
cd ~ && git clone https://github.com/Bojjifomo/Capstone1.git
```

Pasang executor (yang ngirim transaksi) dan sertifikat SSL Python:

```bash
cd ~/Capstone1/dlmm_terminal/executor && npm install
```

```bash
python3 -m pip install --user certifi
```

---

## 2. Isi `.env` (key, RPC, PIN)

Double-click **`~/Capstone1/dlmm_terminal/Buka DLMM Web.command`** di Finder. Pertama kali dia bikin `.env` dan buka di TextEdit. Isi:

| Baris | Isi |
|---|---|
| `DLMM_PRIVATE_KEY=` | Private key wallet LP (Phantom → Settings → Manage Accounts → Show Private Key) |
| `SOLANA_RPC_URL=` | `https://mainnet.helius-rpc.com/?api-key=KEY-LU` (daftar gratis di dashboard.helius.dev) |
| `DLMM_WEB_PIN=` | **Wajib buat HP.** PIN 6+ angka, misalnya `482913`. Tanpa PIN, HP cuma bisa lihat. |
| `DLMM_BANKROLL_SOL=` | Modal LP lu (SOL) → nentuin maks per posisi |
| `DLMM_SIZE_SOL=` | Size default per ladder |
| `DLMM_TG_BOT_TOKEN=` / `DLMM_TG_CHAT_ID=` | Opsional: notif Telegram (auto exit, alert) |
| `JUPITER_API_KEY=` | Opsional: key dari portal.jup.ag biar swap jarang kena rate limit |

Save (Cmd+S), tutup TextEdit, balik ke jendela Terminal, tekan **Enter**.

> macOS mungkin nolak file `.command` pertama kali: **klik kanan → Open → Open**.

---

## 3. Nyalain di Mac

Double-click **`Buka DLMM Web.command`**. Browser kebuka sendiri ke **http://localhost:8790**.

- **Biarin jendela Terminal-nya kebuka.** Tutup = server mati (dan auto exit berhenti).
- Script-nya pakai `caffeinate`, jadi Mac nggak sleep sendiri selama Terminal kebuka.
- Chip kanan atas harus **🔑 4rEq…** (key kebaca). Kalau **🔒 read-only**: cek `.env`, lalu tutup Terminal dan double-click lagi.

**Biar nyala sendiri tiap Mac hidup (opsional):** System Settings → General → **Login Items** → **+** → pilih `Buka DLMM Web.command`.

**Biar Mac nggak tidur (Mac mini):** System Settings → **Energy** → nyalain *Prevent automatic sleeping when the display is off*. MacBook: jangan tutup lid (atau pakai monitor luar + charger).

---

## 4. Buka dari HP (Tailscale)

Tailscale = jaringan pribadi gratis antara Mac dan HP lu, jalan dari mana aja (4G, Wi-Fi lain). Halamannya nggak kebuka ke internet umum.

1. **Mac:** download Tailscale dari **tailscale.com/download** (atau App Store), login (Google/Apple), nyalain.
2. **iPhone:** install **Tailscale** dari App Store, login **akun yang sama**, nyalain (izinin VPN).
3. Di app Tailscale (HP atau Mac), lihat nama Mac lu, misalnya `mac-mini`, dan IP-nya `100.x.y.z`.
4. Di **Safari HP** buka: **http://mac-mini:8790** (atau **http://100.x.y.z:8790**).
5. Biar kayak app: tombol **Share → Add to Home Screen** → muncul ikon **DLMM Web**.

Pertama kali lu ngubah sesuatu dari HP (zap, close, ★, ⚙, auto exit), HP **nanya PIN** (`DLMM_WEB_PIN`). Sekali per sesi.

---

## 5. Apa yang sama di Mac dan HP

| Yang lu ubah | Kesimpen di Mac | Kelihatan di |
|---|---|---|
| ⚙ Settings | `data/settings.json` | Mac, HP, juga `monitor` / `signal` di Terminal |
| ★ watchlist | `data/watchlist.json` | Mac + HP |
| 🎯 auto exit per ladder | `data/auto_exit.json` | Mac + HP (yang ngecek: server di Mac) |
| ◎/$, mode, window, sort, TVL, tab | `data/ui_prefs.json` | Mac + HP (refresh halaman buat ambil yang terbaru) |
| 🔔 notifikasi browser | per device | nyalain di masing-masing |

Refresh data otomatis tiap **15 menit** (⚙ Settings → Web). Tap chip **● … ago** buat refresh sekarang.

---

## 6. Update ke versi baru

Tutup jendela Terminal server, lalu:

```bash
cd ~/Capstone1 && git pull
```

```bash
cd ~/Capstone1/dlmm_terminal/executor && npm install
```

Double-click `Buka DLMM Web.command` lagi. Di HP: refresh halaman.

---

## 7. Kalau ada masalah

| Masalah | Coba |
|---|---|
| HP: halaman nggak kebuka | Tailscale nyala di dua-duanya, akun sama? Server di Mac jalan (jendela Terminal kebuka)? Coba pakai IP `100.x.y.z`. |
| HP: "trading cuma dari Mac ini" | `DLMM_WEB_PIN` belum diisi. Isi, restart server. |
| HP: "PIN salah" | Ketik ulang PIN yang ada di `.env`. |
| 🔒 read-only | `DLMM_PRIVATE_KEY` kosong / salah. Isi, restart server. |
| Pool kosong / error SSL | `python3 -m pip install --user certifi`, restart. |
| Cleanup / Close+swap: "HTTP 429" | Jupiter rate limit: penjualan otomatis lewat pool Meteora. Kalau sering: isi `JUPITER_API_KEY`. |
| Auto exit nggak jalan | Server harus nyala dan Mac nggak tidur. Statusnya kelihatan di baris Portfolio. |

## Keamanan

- `.env` cuma di Mac, jangan dikirim ke siapa pun. Kalau curiga bocor: pindahin dana ke wallet baru.
- Pakai PIN yang nggak gampang ditebak. Server juga kebuka di Wi-Fi rumah (siapa pun di Wi-Fi yang sama bisa lihat halaman, tapi nggak bisa trading tanpa PIN).
- Jangan share link Tailscale / PIN.
