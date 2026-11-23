# HiGround Run Club

Leaderboard mileage bulanan + dashboard performa lari buat member HiGround. Member connect
**intervals.icu** sekali, habis itu tiap lari otomatis masuk (sync tiap ±30 menit). Jalan di
Mac mini, dibuka member lewat internet pakai **Tailscale Funnel** (gratis).

Python standard library aja, SQLite, frontend statis. Nggak ada `pip install`.

## Kenapa intervals.icu, bukan Strava

API Policy Strava (berlaku 1 Juni 2026) melarang app pihak ketiga menampilkan data Strava
seorang user ke user lain, dan melarang data Strava dipakai buat AI. Jadi leaderboard dari
Strava nggak boleh. intervals.icu gratis, terhubung langsung ke Garmin, COROS, Polar, Suunto,
dan Wahoo, dan punya OAuth API. Terms-nya membolehkan penggunaan apa pun yang legal.

Konsekuensinya: run yang masuk ke intervals.icu **lewat Strava** datang sebagai stub kosong.
Run itu ditampilkan di dashboard member sebagai "nggak dihitung", dengan pesan buat connect
jam langsung ke intervals.icu.

## Yang ada di versi ini

| Halaman | Isi |
| --- | --- |
| Beranda | Total km klub bulan ini, Top 3, cara gabung |
| Leaderboard | Minggu / Bulan / All time · Semua / Perempuan / Laki-laki · urut Jarak / Konsistensi (hari aktif) / Elevasi · podium · member 0 km disembunyikan, cuma dihitung jumlahnya |
| Dashboard | Jarak, run, waktu, pace · km per minggu (16 minggu) · tren pace · beban latihan (7 hari vs rata-rata 4 minggu, warning rawan cedera) · rekor · kalender konsistensi · streak mingguan · level/XP · aktivitas terakhir |
| Profil runner | Dashboard member lain (cuma run yang dihitung) |
| Pengaturan | Nama, kategori (default kosong), tampil/sembunyi dari leaderboard, sync sekarang, putuskan & hapus data |
| Admin | Antrean review anti-cheat (setujui/tolak), daftar member + status sync, sync semua |

**XP** dihitung dari konsistensi, bukan jarak doang: 30 XP per hari lari, 3 XP per km, dan
+100 per minggu dengan ≥3 hari lari. Tiap level butuh 15% lebih banyak XP dari sebelumnya.

**Anti-cheat** (`intervals.py: judge`): cuma Run / TrailRun / VirtualRun · <500 m nggak dihitung ·
pace rata-rata lebih cepat dari 2:50/km, >120 km sekali lari, atau input manual masuk antrean
review admin dan nggak dihitung sampai disetujui. Keputusan admin nggak ketimpa sync berikutnya.

## Coba dulu (demo, tanpa intervals.icu)

```bash
cd higround
python3 server.py --demo
```

Buka http://127.0.0.1:7820, lalu klik **Coba demo**. Isinya 20 runner palsu dengan run palsu
di `demo.db`. Demo login sebagai "Demo Admin", jadi halaman Admin juga bisa dicoba.

## Setup di Mac mini

### 1. Nyalakan Tailscale Funnel

Funnel bikin `https://<nama-mac>.<tailnet>.ts.net` bisa dibuka siapa aja, tanpa buka port di
router. Di admin console Tailscale, izinkan Funnel (HTTPS + atribut `funnel` di policy). Lalu:

```bash
tailscale funnel --bg 7820
```

Catat URL `https://….ts.net` yang muncul. Itu `public_url`.

### 2. Daftarkan app di intervals.icu

1. Login intervals.icu → apply OAuth app di https://intervals.icu/oauth/apply
2. Redirect URI (harus persis): `https://<nama-mac>.<tailnet>.ts.net/auth/callback`
3. Scope: `ACTIVITY:READ`
4. Tunggu app di-approve. Selama status masih *Pending*, login belum bisa jalan.
5. Simpan Client ID dan Client Secret.

### 3. Install

```bash
sh higround/tools/install.sh
```

Script ini nyalin ke `~/Projects/higround`, bikin `config.json` dari `config.example.json`, dan
pasang launchd supaya server jalan terus (dan nyala lagi sendiri habis restart). Isi
`~/Projects/higround/config.json`:

| Kunci | Isi |
| --- | --- |
| `public_url` | URL Funnel dari langkah 1 |
| `intervals_client_id` | dari langkah 2 |
| `intervals_client_secret` | dari langkah 2 (atau env `HG_INTERVALS_SECRET`) |
| `admins` | athlete id intervals.icu lu, misalnya `["i12345"]` (lihat di URL profil intervals.icu) |
| `sync_every_minutes` | default 30 (48 call per member per hari, batas intervals.icu 100) |
| `backfill_days` | seberapa jauh ke belakang sync pertama narik data (default 730 hari) |

Lalu jalankan `sh higround/tools/install.sh` sekali lagi buat restart. Selesai. Share URL-nya
ke grup klub.

## Garmin

Terms intervals.icu minta atribusi Garmin di mana pun data dari perangkat Garmin
ditampilkan. Perangkat Garmin ditulis "Garmin® <model>" di daftar aktivitas, dan footer
nampilin catatan merek dagang kalau ada data Garmin. Developer intervals.icu juga sempat bilang
akses data Garmin lewat API mungkin dibatasi. Kalau itu terjadi, member Garmin perlu jalur
lain. Pantau forum intervals.icu.

## Tes

```bash
cd higround && python3 -m unittest discover -s tests
```

## Berikutnya

Squad battle bulanan · club goal kolektif · event + auto check-in group run (dari lokasi
start) · badge · recap card buat IG Story · race goal + training plan · kategori Most Improved.
