# Fund: net worth, pengeluaran, dan saran fund manager

App kecil yang jalan di Mac, dibuka dari HP lewat Tailscale. **Terpisah dari tools LP**:
punya database sendiri (`fund.db`), port sendiri (7799), nggak manggil apa-apa dari
`rh_wallet_finder/` kecuali satu bacaan read-only buat LP (opsional, di bawah).

Python 3 standard library aja, HTML/CSS/JS biasa, nggak ada build step.

| Tab | Isinya |
| --- | --- |
| **Net worth** | Total kekayaan (Rp + ≈ USD), naik/turun 30 hari, riwayat per akhir bulan, per bagian (Safety, Saham IDX, Crypto core, Stable yield, LP aktif, Lainnya), semua akun. Tap akun → update saldo. Utang (jenis `debt`) otomatis dikurangin. Akun yang saldonya > 35 hari dikasih tanda |
| **Pengeluaran** | Catat keluar / masuk (kategori, catatan, tanggal), total bulan ini vs budget per kategori, geser bulan, hapus catatan |
| **Saran** | Rekomendasi dari fund manager (Claude) + ide lu sendiri: usulan → dijalanin → selesai (isi realisasi Rp/bulan + hasilnya) atau ditolak. Di atasnya: target vs realisasi dampak |
| **LP** | Muncul kalau `lp_wallets` diisi. Nilai posisi kebuka, USDG nganggur, arus uang 7 / 30 / 90 hari (masuk ke LP, ditarik, fee, PnL posisi yang ditutup) |

Angka-angka di tile: **runway** = saldo Safety ÷ rata-rata pengeluaran 3 bulan terakhir;
**saving rate** = (masuk − keluar) ÷ masuk, rata-rata 3 bulan.

Ketik angka bebas: `1.500.000`, `1,5jt`, `45jt`, `35rb`, `2m` (miliar).

## Pasang di Mac

```sh
cd fund
sh tools/install.sh
```

Copy ke `~/Projects/fund`, pasang launchd `com.bojjifomo.fund` (nyala sendiri tiap login),
terus nampilin alamatnya. Di HP: buka alamat Tailscale di Safari → Share → **Add to Home Screen**.

Restart: `launchctl kickstart -k gui/$(id -u)/com.bojjifomo.fund` · Log: `~/Projects/fund/logs/server.log`

### Satu pintu: lewat Guild Hall

Di Guild Hall ada tombol **💰 Fund**. Dia buka app ini di `/fund/` lewat alamat Guild Hall yang sama (https
`tailscale serve` juga), jadi di HP cukup satu app di home screen. Di Fund ada **← Guild Hall** buat balik.
Kalau udah pakai jalur ini, Fund boleh cuma dengerin localhost: isi `"bind": ["127.0.0.1"]` di `config.json`.

## LP auto-track (gratis)

Wallet Finder udah nge-index dan ngehargain semua posisi Uniswap V4 di Robinhood Chain ke
database-nya sendiri. Fund cuma **baca** file itu (SQLite read-only) buat wallet lu: **nol RPC,
nol API, nol pemakaian Claude**. Tiap `lp_every_minutes` (60) nilainya dicatat jadi saldo akun
**LP (auto)** (bagian LP aktif), jadi ikut ke net worth.

Isi di `~/Projects/fund/config.json`:

```json
{ "lp_wallets": ["0xWALLET_LP_LU"], "lp_db": "/Users/<lu>/…/Capstone1/rh_wallet_finder/data/wallets.db" }
```

`install.sh` udah ngisi `lp_db` pas install pertama. Angkanya seger sesuai worker Wallet Finder
(kalau Wallet Finder mati, LP diem di angka terakhir; tanggalnya keliatan di tab LP). Posisi yang
belum ada harganya nggak dihitung dan dikasih tau jumlahnya.

## Setting (`config.json`, nggak ke-commit)

| Kunci | Default | Artinya |
| --- | --- | --- |
| `port` | `7799` | Port web |
| `bind` | `["127.0.0.1", "tailscale"]` | localhost + IP Tailscale, nggak pernah `0.0.0.0` |
| `token` | kosong | Opsional. Buka sekali pake `…:7799/?t=<token>`, HP inget (cookie). `FUND_TOKEN` di env menang |
| `usd_idr` | `16500` | Kurs awal, sampe lu isi kurs di app (tap angka kurs) |
| `lp_wallets` | `[]` | Wallet LP lu. Kosong = tab LP mati |
| `lp_db` | sebelah repo | Database Wallet Finder |
| `lp_every_minutes` | `60` | Seberapa sering LP dicatat |

## Agent Guild Hall yang ikut mikir

Taruh agent di divisi **The Counting House** di Guild Hall → dia bisa baca `report`, kasih saran (`rec add --by
<nama>`), dan nanggepin saran agent lain (`rec note <id> "…" --stance challenge|support|question --by <nama>`).
Saldo, pengeluaran, budget, kurs: cuma lu. Di tab **Saran**, tiap saran nunjukin siapa yang ngusulin dan
semua tanggapan (setuju / nantang / nanya); lu juga bisa **Tanya** di situ. Template siap pakai di form hire
Guild Hall: **Fund analyst** (ngusulin) dan **Fund critic** (nyari kelemahan tiap usulan).

## Fund manager (Claude)

Subagent `fund-manager` (`.claude/agents/fund-manager.md`) baca dan nulis lewat CLI:

```sh
python3 ~/Projects/fund/fund.py report                  # semua angka, JSON
python3 ~/Projects/fund/fund.py rec add "judul" --body "…" --sleeve 0 --expected 150000
python3 ~/Projects/fund/fund.py rec set 3 --status done --actual 120000 --result "…"
python3 ~/Projects/fund/fund.py balance "BCA" 25000000
python3 ~/Projects/fund/fund.py spend 85000 makan --note kopi
python3 ~/Projects/fund/fund.py lp --days 30
```

Alurnya: lu catat (app) → Claude baca `report` → saran masuk tab **Saran** → lu jalanin / tolak →
pas selesai lu isi realisasinya → review bulan depan bandingin target vs realisasi.
Claude nggak pernah megang key / login; semua eksekusi lu yang ngelakuin.

## Tes

```sh
python3 -m unittest discover -s tests
```
