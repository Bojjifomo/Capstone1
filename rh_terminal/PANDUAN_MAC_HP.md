# RH Terminal di Mac mini + iPhone + MacBook Pro — Panduan Setup

Satu server jalan di **Mac mini**. iPhone dan MacBook Pro buka **halaman yang sama** dari Mac mini, dari mana aja (rumah, 4G, kantor) lewat **Tailscale**.

```
iPhone ─┐
        ├── Tailscale ──► Mac mini  (RH Terminal, port 8892)  ──► Robinhood Chain
MacBook ┘                     └── key cuma di sini
```

Semua yang lu atur kesimpen di Mac mini, jadi tiga-tiganya sama persis: ⚙ Settings, ★ watchlist, 🎯 auto exit, dan tampilan ($ / Ξ, mode, window fee, sort, TVL).

**Private key cuma ada di Mac mini.** iPhone dan MacBook nggak pernah lihat key. Mereka cuma kirim perintah ke Mac mini, dan cuma bisa nyambung lewat **Tailscale** (Wi-Fi doang ditolak).

> ⚠️ **Uang beneran.** Pakai wallet khusus LP yang isinya cuma yang siap lu risikoin. **Auto exit cuma jalan selama Mac mini nyala dan servernya jalan.**

> Udah jalanin **DLMM Web** di Mac mini? Bisa barengan. DLMM di port **8891**, RH Terminal di port **8892**. Tailscale yang sama dipakai buat dua-duanya, jadi langkah 5 dan 6 mungkin udah beres.

---

## 1. Sekali aja: siapin Mac mini

Buka **Terminal** (Cmd+Spasi → ketik `Terminal`). Kalau Mac mini udah dipakai buat DLMM, langkah ini udah beres, langsung ke langkah 2.

```bash
xcode-select --install
```

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

```bash
brew install python node git
```

```bash
python3 -m pip install --user certifi
```

---

## 2. Ambil kode RH Terminal

**Belum ada folder `Capstone1` di Mac mini:**

```bash
cd ~ && git clone https://github.com/Bojjifomo/Capstone1.git
```

**Udah ada (dari DLMM):** lanjut aja.

Lalu ambil RH Terminal. Selama belum di-merge ke `main`, dia ada di branch ini (isinya `main` + RH Terminal, jadi DLMM tetap ada):

```bash
cd ~/Capstone1 && git fetch origin && git checkout claude/brave-euler-uj21cp && git pull
```

Pasang executor (yang tanda tangan & kirim transaksi, ±30 detik):

```bash
cd ~/Capstone1/rh_terminal/executor && npm install
```

---

## 3. Key: pilih salah satu

### Opsi A — pakai RH Secrets yang udah ada di HP (paling gampang)

RH Terminal bisa baca **RH Secrets** langsung dari folder Scriptable di iCloud Drive, jadi key-nya sama persis dengan di HP dan nggak perlu diketik ulang.

1. Mac mini login ke **Apple ID yang sama** dengan iPhone.
2. System Settings → **Apple ID → iCloud → iCloud Drive** → nyala.
3. Cek file-nya udah ada: Finder → **iCloud Drive → Scriptable** → harus ada `RH Secrets.js`.
4. Pertama kali server jalan, macOS mungkin nanya *"Terminal would like to access files in iCloud Drive"* → **Allow**.

Yang dibaca dari situ: `PRIVATE_KEY`, `ALCHEMY_API_KEY`, `PRACTICE_MODE`.

### Opsi B — isi `.env` di Mac mini

Kalau nggak mau pakai iCloud, isi di `rh_terminal/.env` (langkah 4): `RH_PRIVATE_KEY=` (64 hex) dan `ALCHEMY_API_KEY=`.

> Kalau dua-duanya diisi, **`.env` yang menang.**

---

## 4. Nyalain pertama kali

Di Finder buka `~/Capstone1/rh_terminal/` → double-click **`Buka RH Terminal.command`**.

> macOS nolak file `.command` pertama kali? **Klik kanan → Open → Open.** Sekali aja.

Pertama kali dia bikin **`rh_terminal/.env`** dan buka di TextEdit. Isi:

| Baris | Isi |
|---|---|
| `RH_PRIVATE_KEY=` | **Opsi A:** biarin kosong. **Opsi B:** private key wallet LP (64 hex, boleh pakai `0x`). |
| `ALCHEMY_API_KEY=` | Opsi A: kosong. Opsi B: key Alchemy (chain Robinhood). |
| `RH_PRACTICE_MODE=` | Biarin **`true`** dulu: semua tombol cuma simulasi di chain, nggak ada yang dikirim. |
| `RH_WEB_PIN=` | **Biarin kosong** = iPhone & MacBook lewat Tailscale langsung bisa trading tanpa PIN. Isi PIN cuma kalau mau dikunci. |
| `RH_WEB_ALLOW_LAN=` | **Biarin kosong.** Device yang cuma nyambung Wi-Fi rumah (tanpa Tailscale) ditolak. `true` = boleh. |
| `RH_WEB_PORT=` | Kosong = **8892**. Isi angka lain cuma kalau 8892 kepake app lain. |
| `RH_BANKROLL_USD=` | Modal LP lu dalam dolar. Maks per posisi = bankroll × 5% ÷ 0.9. |
| `RH_SIZE_USD=` | Size default per ladder (contoh `400`). |
| `RH_TG_BOT_TOKEN=` / `RH_TG_CHAT_ID=` | Opsional: notif Telegram (PING baru, auto exit). Kosong = pakai bot DLMM kalau ada. |

Save (**Cmd+S**), tutup TextEdit, balik ke jendela Terminal, tekan **Enter**.

Browser Mac mini kebuka ke **http://localhost:8892**. Jendela Terminal nulis alamat buat iPhone/MacBook, misalnya `http://Mac-mini:8892`.

**Cek 3 hal di pojok kanan atas:**

| Chip | Artinya |
|---|---|
| **🔑 0x7c0…** | Key kebaca. Kalau **🔒 read-only**: klik chipnya, dia bilang kenapa. |
| **PRACTICE** (kuning) | Practice mode nyala: tombol cuma simulasi. |
| **⛓ 900+ pool · 1.0h** | Index swap jalan. Pertama kali butuh ±15–30 detik buat baca 1 jam terakhir dari chain. |

> **Biarin jendela Terminal-nya kebuka.** Ditutup = server mati, auto exit berhenti. Selama kebuka, Mac nggak sleep sendiri (`caffeinate`).

Kalau ada yang nggak beres, di Terminal baru jalanin (nggak pernah nampilin key):

```bash
cd ~/Capstone1 && python3 -m rh_terminal doctor
```

---

## 5. Biar Mac mini selalu nyala

1. **Jangan tidur:** System Settings → **Energy** → nyalain *Prevent automatic sleeping when the display is off*.
2. **Nyala lagi sendiri habis mati listrik:** System Settings → **Energy** → nyalain *Start up automatically after a power failure*.
3. **Server jalan sendiri tiap login:** System Settings → General → **Login Items** → **+** → pilih `~/Capstone1/rh_terminal/Buka RH Terminal.command`.
4. (Opsional) Login otomatis: System Settings → **Users & Groups** → *Automatically log in as* → user lu. Tanpa ini, habis mati listrik server baru jalan setelah lu login.

---

## 6. Tailscale (sekali, buat iPhone & MacBook)

Tailscale = jaringan pribadi gratis antara device lu. Halaman RH Terminal **nggak kebuka ke internet umum**, cuma ke device yang login akun Tailscale lu.

1. **Mac mini:** download dari **tailscale.com/download** (atau App Store), login (Google/Apple), nyalain.
2. Di app Tailscale Mac mini, lihat **nama** Mac-nya (misalnya `mac-mini`) dan **IP**-nya (`100.x.y.z`). Catat.

> Udah pakai Tailscale buat DLMM? Lewatin langkah ini.

---

## 7. Buka dari iPhone

1. Install **Tailscale** dari App Store, login **akun yang sama**, nyalain (izinin VPN).
2. Buka **Safari** → **http://mac-mini:8892** (ganti `mac-mini` dengan nama dari langkah 6, atau pakai IP `http://100.x.y.z:8892`).
3. Biar kayak app: **Share → Add to Home Screen** → muncul ikon **RH Terminal**.

Tampilan versi HP: tab bar di bawah (**Pool · Pass · PING · Watch · Portfolio**), tiap pool / ladder jadi kartu, tap kartu = detail + chart layar penuh (**✕ Tutup** buat balik).

Kalau `RH_WEB_PIN` kosong, nggak ada PIN sama sekali. Kalau diisi, iPhone / MacBook ditanya PIN sekali per sesi (Mac mini nggak pernah).

> Script Scriptable di iPhone (**RH Pool**, **RH Portfolio**) tetap bisa dipakai kayak biasa. Dua-duanya pakai wallet yang sama, jadi posisi yang dibuka dari mana pun kelihatan di dua-duanya.

---

## 8. Buka dari MacBook Pro

MacBook **nggak jalanin server sendiri**. Dia cuma buka halaman dari Mac mini, sama kayak iPhone.

1. Install **Tailscale** di MacBook (tailscale.com/download), login **akun yang sama**, nyalain.
2. Browser → **http://mac-mini:8892** (atau `http://100.x.y.z:8892`).
3. Biar ada di Dock: Safari → **File → Add to Dock**. Chrome: ⋮ → **Cast, save, and share → Install page as app**.
4. Tanpa `RH_WEB_PIN` nggak ada PIN; langsung bisa transaksi.

Selalu buka lewat Tailscale, di rumah juga. `http://Mac-mini.local:8892` atau IP Wi-Fi (`192.168.x.x`) bakal ditolak: "cuma bisa dibuka dari Mac ini atau lewat Tailscale".

> ⛔ **Jangan double-click `Buka RH Terminal.command` di MacBook** buat wallet yang sama. Dua server = dua pengecek auto exit yang bisa nyoba nutup posisi yang sama dua kali, dan setting/watchlist-nya kepisah. **Satu server di Mac mini aja.**

---

## 9. Coba dulu, baru beneran

Dengan **PRACTICE** nyala:

1. Tab **Pool** → klik satu pool → **⚡ zap** → tombolnya **🧪 Simulasi ladder** → harus muncul *"✓ Simulasi lolos"*.
   - *"wallet holds 0 USDG"* = saldo kurang, wajar kalau wallet-nya belum diisi.
   - *"perlu approve"* = token belum di-approve. Yang beneran bakal approve otomatis dulu.
2. Tab **Portfolio** → coba **Close**, **Claim**, **Close+swap** di salah satu ladder: semuanya cuma simulasi.

Udah yakin? Ganti di `rh_terminal/.env`:

```
RH_PRACTICE_MODE=false
```

Save, lalu klik chip **🔑** di halaman (dia baca ulang `.env`, nggak perlu restart). Chip **PRACTICE** hilang = tombol sekarang **kirim transaksi beneran**. Mulai dari size kecil.

> Practice di `.env` selalu menang atas `PRACTICE_MODE` di RH Secrets, jadi HP tetap bisa practice sementara Mac udah beneran (atau sebaliknya).

---

## 10. Apa yang sama di Mac mini, MacBook, dan iPhone

| Yang lu ubah | Kesimpen di Mac mini | Kelihatan di |
|---|---|---|
| ⚙ Settings | `rh_terminal/data/settings.json` | semua |
| ★ watchlist | `data/watchlist.json` | semua |
| 🎯 auto exit per ladder | `data/auto_exit.json` | semua (yang ngecek: server di Mac mini) |
| $ / Ξ, mode, window, sort, TVL, tab, wallet | `data/ui_prefs.json` | semua (refresh halaman buat ambil yang terbaru) |
| 🔔 notifikasi browser | per device | nyalain di masing-masing |

Data pool & portfolio refresh otomatis tiap **2 menit** (⚙ Settings → Web). Tap chip **● … ago** buat refresh sekarang.

---

## 11. Update ke versi baru

Di Mac mini: tutup jendela Terminal server, lalu

```bash
cd ~/Capstone1 && git pull && cd rh_terminal/executor && npm install
```

Double-click `Buka RH Terminal.command` lagi. Di iPhone/MacBook: refresh halaman. Histori index swap nggak hilang (disimpan di disk).

---

## 12. Kalau ada masalah

| Masalah | Coba |
|---|---|
| "Gagal baca: … Connection refused / HTTP 403 / alchemy HTTP 400", chip ⛓ merah "0 pool" | Jaringan lu (biasanya DNS provider internet) ngeblok RPC resmi Robinhood dan PublicNode. Versi terbaru otomatis pindah ke RPC cadangan (Daybreak, dRPC) dan nyari posisi lewat ownerOf: update (langkah 11). Biar normal lagi: System Settings → Network → Wi-Fi/Ethernet → **Details → DNS** → ganti ke `1.1.1.1` dan `8.8.8.8`, restart RH Terminal. `python3 -m rh_terminal doctor` nunjukin RPC mana yang kejangkau. (Alchemy gratis cuma bisa baca log 10 block, jadi bukan cadangan buat itu.) |
| iPhone / MacBook: halaman nggak kebuka | Tailscale nyala di dua-duanya, akun sama? Server di Mac mini jalan (jendela Terminal kebuka)? Coba pakai IP `http://100.x.y.z:8892`. |
| "RH Terminal cuma bisa dibuka dari Mac ini atau lewat Tailscale" | Device-nya nggak lewat Tailscale (cuma Wi-Fi). Nyalain Tailscale, buka `http://<nama-mac>:8892`. Terminal Mac mini nyatet IP yang ditolak. |
| "PIN salah" padahal nggak mau PIN | Hapus isi `RH_WEB_PIN=` di `.env`, tutup Terminal, double-click lagi. |
| "PIN salah" | Ketik ulang PIN yang ada di `.env`. |
| 🔒 read-only | Klik chip 🔒: dia nulis kenapa (`.env` kosong, key bukan 64 hex, RH Secrets nggak ketemu di iCloud, file RTF). Atau `python3 -m rh_terminal doctor`. |
| RH Secrets nggak ketemu | Finder → iCloud Drive → Scriptable: `RH Secrets.js` ada? Kalau file-nya ada tanda awan, klik kanan → *Download Now*. Atau pakai Opsi B (`.env`). |
| "Port 8892 kepake app lain" | Isi `RH_WEB_PORT=8894` di `.env`, double-click lagi. Di iPhone/MacBook pakai port baru. |
| Pool kosong / chip ⛓ merah | Tunggu ±30 detik (index baca chain). Error SSL: `python3 -m pip install --user certifi`, restart. |
| Tombol transaksi: "Node.js belum ada" / "npm install" | `brew install node`, lalu `cd ~/Capstone1/rh_terminal/executor && npm install`. |
| Close+swap: "would revert" waktu jual | Harga jual bakal lebih jelek dari max loss (⚙ Risk, default 5%) di bawah harga pool. Token tetap di wallet. Naikin max loss kalau memang mau jual. |
| Auto exit nggak jalan | Mac mini harus nyala + server jalan. Statusnya kelihatan di baris ladder di Portfolio. |

---

## Keamanan

- Key (`.env` / RH Secrets) cuma di Mac mini. Jangan dikirim ke siapa pun. Curiga bocor → pindahin dana ke wallet baru.
- Device yang cuma nyambung Wi-Fi rumah lu nggak bisa buka sama sekali; yang bisa cuma Mac mini dan device di akun Tailscale lu. Jangan share akun Tailscale; kalau perlu, isi `RH_WEB_PIN`.
- Semua transaksi disimulasikan dulu. Yang bakal gagal nggak pernah dikirim.
