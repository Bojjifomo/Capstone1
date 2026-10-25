# RH Wallet Finder di iPhone

Dua bagian, dua-duanya pakai yang udah ada:

| | Apa | Jalan selama |
| --- | --- | --- |
| **Widget home screen** | Pool yang fee per $ likuiditasnya paling gede (APR 24 jam + 1 jam terakhir) + top wallet Multiday / Degen, ngintip doang | Web showcase di Vercel jalan (Mac boleh tidur: widget nunjukin umur datanya) |
| **Ngobrol sama agent** | Tanya apa aja ke lp-orchestrator dari app Claude di iPhone | Mac nyala + `claude remote-control` jalan di folder `Capstone1` |

## 1. Widget (app Scriptable)

Datanya dari `https://<web lu>/api/widget`, potongan kecil dari snapshot yang di-publish Mac.
Bagian `pools` baru ikut di snapshot setelah Mac jalan pakai versi ini, jadi:

1. Di Mac: `git pull`, buka lagi `Buka Wallet Finder.command`. Publish otomatis berikutnya
   (maks 30 menit) ikut nge-scan pool dulu. Mau langsung: buka tab **Pools** sekali (itu nge-scan),
   terus Settings → **Publish now**.
2. Pastiin Vercel udah deploy versi baru (otomatis abis merge ke `main`). Cek: buka
   `https://<web lu>/api/widget` di Safari, harus keluar JSON yang ada `"pools"`.
3. Di iPhone: install **Scriptable** (gratis, App Store).
4. Scriptable → **+** → paste isi [`RH Pools Widget.js`](RH%20Pools%20Widget.js) → ganti
   `SITE` di baris atas jadi link web lu (contoh `https://rh-wallets.vercel.app`, tanpa `/`
   di akhir) → kasih nama "RH Pools" → ▶ buat nyoba.
5. Home screen: tahan → **+** → Scriptable → pilih ukuran (Large = pool + wallet, Medium /
   Small = pool doang) → Add → tahan widget-nya → **Edit Widget** → Script: **RH Pools**.

Widget refresh sendiri kira-kira tiap 15 menit (iOS yang nentuin persisnya). Tap widget =
buka web showcase. Pojok kanan atas = umur data; kuning kalau lebih dari 45 menit (Mac
lagi mati / nggak publish).

Kolom: **APR 24h** = fee pool itu 24 jam terakhir ÷ likuiditasnya, disetahunin (dari
[Pool finder](../README.md#pool-finder-fee-terbaik-buat-buka-lp), filter default: mcap ≥ $500k,
TVL ≥ $5k). **1h** = sama tapi 1 jam terakhir: hijau = lagi lebih deres dari rata-rata
harinya, merah = lagi adem. Daftar pool-nya di-scan ulang worker sekali sejam sebelum publish.

## 2. Ngobrol sama agent dari iPhone (Remote Control)

Agent-nya tetap jalan di Mac (datanya di sana), iPhone cuma jadi layar + keyboard-nya.
Pakai langganan Claude lu, nggak perlu API key.

1. Di Mac, Terminal:
   ```bash
   cd ~/…/Capstone1
   ```
   ```bash
   caffeinate -i claude remote-control
   ```
   (`caffeinate -i` = Mac nggak sleep selama ini jalan. Biarin jendela Terminal-nya kebuka.)
2. Di iPhone: buka app **Claude**, masuk ke bagian **Code**. Sesi dari Mac lu muncul di
   situ; buka.
3. Ngobrol biasa, misalnya:
   - *pakai lp-orchestrator, pool mana yang fee-nya paling bagus minggu ini dan siapa yang cuan di sana?*
   - *pakai lp-orchestrator, 3 pool dengan net APR tertinggi yang minimal 10 wallet*
   - *pakai lp-orchestrator, kasih brief hari ini*

Kalau agent mau jalanin perintah, izinnya ditanya di iPhone juga. Yang cuma baca
(`pools`, `top`, `status`, `smart-lp`) aman di-allow; `sync` / `score` / `publish` dia
bilang dulu.
