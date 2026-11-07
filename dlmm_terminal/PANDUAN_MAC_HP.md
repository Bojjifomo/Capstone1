# DLMM Web di Mac mini + MacBook + HP — Panduan Setup

Satu server jalan di **Mac mini**. Mac mini, MacBook, dan HP buka **halaman yang sama**. Semua yang lu atur kesimpen di Mac, jadi dua-duanya sama persis:

- ⚙ Settings (ladder, risk, gate, filter PING, exit rules)
- ★ watchlist
- 🎯 auto exit per ladder
- tampilan: ◎ SOL / $ USD, mode (ALL / V1 / PING / WATCH), fee window, sort, TVL

Private key cuma ada di Mac mini (`.env`). MacBook dan HP nggak pernah lihat key, cuma kirim perintah ke Mac mini dan ditanya **PIN**.

Port default **8891** (bukan 8790). Kalau bentrok, ganti `DLMM_WEB_PORT` di `.env` (lihat langkah 2).

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
| `DLMM_WEB_PIN=` | **Wajib buat HP / MacBook.** PIN 6+ angka, misalnya `482913`. Tanpa PIN, mereka cuma bisa lihat. |
| `DLMM_WEB_PORT=` | Kosong = **8891**. Isi angka lain (misalnya `8892`) kalau port itu kepake app lain di Mac mini. |
| `DLMM_BANKROLL_SOL=` | Modal LP lu (SOL) → nentuin maks per posisi |
| `DLMM_SIZE_SOL=` | Size default per ladder |
| `DLMM_TG_BOT_TOKEN=` / `DLMM_TG_CHAT_ID=` | Opsional: notif Telegram (auto exit, alert) |
| `JUPITER_API_KEY=` | Opsional: key dari portal.jup.ag biar swap jarang kena rate limit |

Save (Cmd+S), tutup TextEdit, balik ke jendela Terminal, tekan **Enter**.

> macOS mungkin nolak file `.command` pertama kali: **klik kanan → Open → Open**.

---

## 3. Nyalain di Mac

Double-click **`Buka DLMM Web.command`** di **Mac mini**. Browser kebuka sendiri ke **http://localhost:8891**. Jendela Terminal-nya nulis alamat buat HP / MacBook, misalnya `http://Mac-mini:8891`.

- Kalau port-nya kepake app lain, launcher-nya bilang app apa dan berhenti: isi `DLMM_WEB_PORT=8892` di `.env`, double-click lagi.
- Kalau DLMM Web ternyata udah jalan (jendela lain), dia cuma buka browser, nggak bikin server kedua.

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
4. Di **Safari HP** buka: **http://mac-mini:8891** (atau **http://100.x.y.z:8891**). Ganti 8891 kalau lu ubah `DLMM_WEB_PORT`.
5. Biar kayak app: tombol **Share → Add to Home Screen** → muncul ikon **DLMM Web**.

Pertama kali lu ngubah sesuatu dari HP (zap, close, ★, ⚙, auto exit), HP **nanya PIN** (`DLMM_WEB_PIN`). Sekali per sesi.

---

## 5. Buka dari MacBook

MacBook **nggak jalanin server sendiri**. Dia cuma buka halaman dari Mac mini, sama kayak HP.

1. Install **Tailscale** di MacBook (tailscale.com/download), login **akun yang sama**, nyalain.
2. Buka browser di MacBook: **http://mac-mini:8891** (nama dari app Tailscale, atau IP `100.x.y.z`).
3. Biar kayak app di Dock: Safari → **File → Add to Dock** (macOS Sonoma ke atas). Chrome: ⋮ → **Cast, save, and share → Install page as app**.
4. Pertama kali ngubah sesuatu (zap, close, ★, ⚙, auto exit) MacBook nanya **PIN**.

Kalau MacBook lagi di Wi-Fi rumah yang sama, `http://Mac-mini.local:8891` juga bisa tanpa Tailscale. Di luar rumah tetap pakai Tailscale.

> **Jangan jalanin `Buka DLMM Web.command` di MacBook juga** buat wallet yang sama: dua server = dua pengecek auto exit, bisa nyoba nutup posisi yang sama dua kali, dan setting/watchlist-nya kepisah. Satu server di Mac mini aja.

---

## 6. Apa yang sama di Mac mini, MacBook, dan HP

| Yang lu ubah | Kesimpen di Mac | Kelihatan di |
|---|---|---|
| ⚙ Settings | `data/settings.json` | semua, juga `monitor` / `signal` di Terminal |
| ★ watchlist | `data/watchlist.json` | semua |
| 🎯 auto exit per ladder | `data/auto_exit.json` | semua (yang ngecek: server di Mac mini) |
| ◎/$, mode, window, sort, TVL, tab | `data/ui_prefs.json` | semua (refresh halaman buat ambil yang terbaru) |
| 🔔 notifikasi browser | per device | nyalain di masing-masing |

Refresh data otomatis tiap **15 menit** (⚙ Settings → Web). Tap chip **● … ago** buat refresh sekarang.

---

## 7. Update ke versi baru

Tutup jendela Terminal server, lalu:

```bash
cd ~/Capstone1 && git pull
```

```bash
cd ~/Capstone1/dlmm_terminal/executor && npm install
```

Double-click `Buka DLMM Web.command` lagi (di Mac mini). Di HP / MacBook: refresh halaman.

---

## 8. Kalau ada masalah

| Masalah | Coba |
|---|---|
| "Port 8891 kepake app lain" | Isi `DLMM_WEB_PORT=8892` (atau angka lain) di `.env`, double-click lagi. Di HP / MacBook pakai port baru itu. |
| Dulu pakai 8790 | Bookmark lama ganti ke `:8891` (atau `DLMM_WEB_PORT` lu). |
| HP / MacBook: halaman nggak kebuka | Tailscale nyala di dua-duanya, akun sama? Server di Mac jalan (jendela Terminal kebuka)? Coba pakai IP `100.x.y.z`. |
| HP / MacBook: "trading cuma dari Mac ini" | `DLMM_WEB_PIN` belum diisi. Isi, restart server. |
| HP / MacBook: "PIN salah" | Ketik ulang PIN yang ada di `.env`. |
| 🔒 read-only | `DLMM_PRIVATE_KEY` kosong / salah. Isi, restart server. |
| Pool kosong / error SSL | `python3 -m pip install --user certifi`, restart. |
| Cleanup / Close+swap: "HTTP 429" | Jupiter rate limit: penjualan otomatis lewat pool Meteora. Kalau sering: isi `JUPITER_API_KEY`. |
| Auto exit nggak jalan | Server harus nyala dan Mac nggak tidur. Statusnya kelihatan di baris Portfolio. |

## Keamanan

- `.env` cuma di Mac, jangan dikirim ke siapa pun. Kalau curiga bocor: pindahin dana ke wallet baru.
- Pakai PIN yang nggak gampang ditebak. Server juga kebuka di Wi-Fi rumah (siapa pun di Wi-Fi yang sama bisa lihat halaman, tapi nggak bisa trading tanpa PIN).
- Jangan share link Tailscale / PIN.
