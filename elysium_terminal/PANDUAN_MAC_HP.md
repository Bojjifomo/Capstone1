# Elysium Terminal di Mac mini + iPhone + MacBook

Satu server di **Mac mini**. iPhone dan MacBook buka halaman yang sama lewat **Tailscale**.

```
iPhone ─┐
        ├── Tailscale ──► Mac mini  (Elysium Terminal, port 8893) ──► Elysium (99901)
MacBook ┘                     └── key cuma di sini
```

RH Terminal boleh tetap nyala di port **8892**. Dua-duanya nggak saling baca key: Elysium cuma baca `ELYSIUM_PRIVATE_KEY` di `elysium_terminal/.env`.

> Uang beneran kalau practice dimatiin. Pakai wallet LP khusus Elysium. **Auto exit cuma jalan selama Mac mini nyala dan servernya jalan.**

---

## 1. Sekali aja

Kalau Mac mini udah dipakai buat DLMM atau RH Terminal, lewatin install.

```bash
xcode-select --install
brew install python node git
python3 -m pip install --user certifi
```

Kode:

```bash
cd ~ && git clone https://github.com/Bojjifomo/Capstone1.git
cd ~/Capstone1/elysium_terminal/executor && npm install
```

---

## 2. Nyalain pertama kali

Finder → `~/Capstone1/elysium_terminal/` → double-click **`Buka Elysium Terminal.command`**.

Pertama kali macOS nolak? **Klik kanan → Open → Open.**

Dia bikin `elysium_terminal/.env` dan buka TextEdit.

| Baris | Isi |
| --- | --- |
| `ELYSIUM_PRIVATE_KEY=` | Private key wallet LP **Elysium** (64 hex, boleh `0x`). Jangan isi key Robinhood. |
| `ALCHEMY_API_KEY=` | Key Alchemy yang chain Elysium-nya nyala. Kosong = RPC publik (gampang ke-limit). |
| `ELYSIUM_PRACTICE_MODE=` | Biarin **`true`**. Tombol cuma simulasi. |
| `ELYSIUM_WEB_PIN=` | Wajib kalau iPhone / MacBook mau trading. 6+ angka. |
| `ELYSIUM_WEB_PORT=` | Kosong = **8893**. |
| `ELYSIUM_BANKROLL_USD=` / `ELYSIUM_SIZE_USD=` | Modal dan size default. |

Save, Enter di Terminal. Browser kebuka ke **http://localhost:8893**.

Chip kanan atas:

| Chip | Artinya |
| --- | --- |
| **🔑 0x…** | Key kebaca. **🔒 read-only** = klik, dia bilang kenapa. |
| **PRACTICE** | Masih simulasi. |
| **⛓ … pool** | Index lagi baca chain. Pertama kali bisa sampai semenit (v4-nya rame). |

Jendela Terminal jangan ditutup. `caffeinate` nahan Mac biar nggak sleep.

Cek tanpa nunjukin key:

```bash
cd ~/Capstone1 && python3 -m elysium_terminal doctor
```

---

## 3. Mac mini tetap nyala

1. System Settings → Energy → *Prevent automatic sleeping when the display is off*.
2. *Start up automatically after a power failure*.
3. Login Items → tambah `Buka Elysium Terminal.command`.

---

## 4. Tailscale

Kalau udah kepake buat DLMM / RH Terminal, lewatin. Kalau belum: install di Mac mini, catat nama dan IP `100.x.y.z`.

---

## 5. iPhone dan MacBook

1. Tailscale, akun yang sama, nyalain.
2. Safari → `http://<nama-mac>:8893` (atau `http://100.x.y.z:8893`).
3. iPhone: Share → Add to Home Screen. MacBook: Add to Dock. **Jangan** double-click `.command` di MacBook: satu server aja, di Mac mini.

Pertama kali mau ubah sesuatu dari HP, dia minta PIN.

Di Wi-Fi rumah, `http://<nama>.local:8893` juga bisa. Di luar rumah tetap Tailscale.

---

## 6. Coba dulu

PRACTICE nyala:

1. Tab Pool → ⚡ zap → **Simulasi ladder** → harus ada "simulasi lolos", atau alasan yang jelas (saldo 0, perlu approve).
2. Portfolio → Close / Claim / Close+swap cuma simulasi.
3. Tab Tight cuma nampilin peluang dan review. Auto-rebalance mati. Jangan dinyalain kalau belum baca catatannya di README: itu bukan janji profit, dan live butuh ketikan plus practice dimatikan.

Yakin? Di `.env`:

```
ELYSIUM_PRACTICE_MODE=false
```

Save, klik chip 🔑 (dia baca ulang, nggak perlu restart). Mulai dari size kecil.

---

## 7. Yang kesimpen di Mac mini

| Yang lu ubah | File | Kelihatan di |
| --- | --- | --- |
| Settings | `elysium_terminal/data/settings.json` | semua device |
| Watchlist | `data/watchlist.json` | semua |
| Auto exit | `data/auto_exit.json` | yang ngecek: server di Mac mini |
| Tight / auto-rebalance | `data/tight_config.json` | server di Mac mini. Tab boleh ditutup, proses Python harus tetap hidup. Mati secara default. |
| $ / HYPE, tab, sort | `data/ui_prefs.json` | semua, habis refresh |

Refresh pool + portfolio tiap 2 menit (⚙ → Web).

---

## 8. Update

Tutup jendela server, lalu:

```bash
cd ~/Capstone1 && git pull && cd elysium_terminal/executor && npm install
```

Double-click lagi. Refresh halaman di HP.

---

## 9. Kalau ada masalah

| Masalah | Coba |
| --- | --- |
| HP nggak kebuka | Tailscale nyala di dua-duanya? Jendela Terminal di Mac mini masih kebuka? Coba IP `100.x`. |
| "trading cuma dari Mac ini" | `ELYSIUM_WEB_PIN` kosong. |
| 🔒 read-only | Klik chipnya, atau `python3 -m elysium_terminal doctor`. |
| Port kepake | `ELYSIUM_WEB_PORT=8895`, double-click lagi. |
| Pool kosong / chip merah | Tunggu index. Error SSL: `python3 -m pip install --user certifi`. |
| Node / npm | `brew install node` lalu `cd ~/Capstone1/elysium_terminal/executor && npm install`. |
| Close+swap revert | Harga jual lebih jelek dari max loss (⚙ Risk, default 5%). Token tetap di wallet. |

---

## Keamanan

- Key cuma di Mac mini. Jangan dikirim. Curiga bocor → pindahin dana.
- Siapa pun di Wi-Fi rumah bisa **lihat** halaman. Transaksi butuh PIN (atau cuma dari Mac itu sendiri kalau PIN kosong).
- Jangan share Tailscale / PIN.
- Semua tx disimulasikan dulu.
