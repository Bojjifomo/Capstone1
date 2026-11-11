# RPC Monitor

Satu halaman buat ngecek **RPC mana yang udah mau limit**, dari semua bot sekaligus. Dibuka dari
Mac atau HP (Tailscale), sama kayak Power Meter.

```
sh rpc_monitor/tools/install.sh        # sekali, abis itu jalan sendiri tiap Mac nyala
python3 rpc_monitor/server.py --once   # cek sekali di Terminal, tanpa dashboard
```

Mac: `http://127.0.0.1:7795` · HP: `http://<ip-tailscale-mac>:7795`

## Yang ditampilin

**Per RPC** (dikelompokin per key: kalau Wallet Finder, LP Call Bot, sama RH Terminal pakai key
Alchemy yang sama, itu jadi **satu** baris, karena kuotanya juga satu):

| Status | Artinya |
| --- | --- |
| 🟢 aman | normal |
| 🟡 waspada | proyeksi akhir bulan ≥ 70% kuota, atau menit tersibuk ≥ 70% batas req/detik, atau ada 429 di 1 jam terakhir, atau probe gagal |
| 🟠 hampir limit | proyeksi ≥ 90%, batas harian ≥ 90%, ≥ 10 kali 429 dalam 1 jam, atau probe kena 429 |
| 🔴 LIMIT | provider bilang kuota habis (meter, log, atau probe), atau pemakaian bulan ini udah lewat kuota |

Tiap kartu: bar kuota bulan ini (garis putus-putus = proyeksi akhir bulan dengan laju sekarang),
jumlah request 1 jam / 24 jam / bulan ini, 429 & error, hasil probe, alasan statusnya, dan di
**Detail**: pemakaian per bot, header rate-limit dari provider, baris log terakhir yang kena limit.

**Per bot**: RPC apa aja yang dipakai (warna = status RPC-nya), jalan atau nggak (launchd),
kapan terakhir manggil RPC.

## Datanya dari mana

1. **Config bot**: `.env` tiap bot (Wallet Finder, LP Call Bot, RH Terminal, DLMM Terminal),
   RH Secrets di iCloud (key Alchemy), plist launchd, `.env` LP Exit Watcher, Keychain Sol Copy Bot.
   Key nggak pernah ditampilin utuh dan nggak ditulis ke file.
2. **Meter**: tiap bot sekarang ngitung request-nya sendiri ke `~/.rpc-monitor/usage-*.jsonl`
   (per menit, per bot, per RPC: ok / 429 / kuota habis / error). Yang kepasang: RH Wallet Finder
   (+ app Streamlit), LP Call Bot, RH Terminal (Python + executor Node), DLMM Terminal (executor
   Node), LP Exit Watcher (repo sendiri, `rpcmeter.py`). **Bot yang lagi jalan harus di-restart
   sekali** biar meter-nya kepake.
3. **Log bot** (dari plist launchd): baris baru yang ada `429` / `rate limit` / `quota`. Ngitung
   mulai dari monitor pertama jalan.
4. **Probe** tiap 5 menit: 1 request murah per RPC (`eth_chainId` / `getHealth`). Di Helius itu
   1 credit, ±8.600 credit/bulan (< 1% Helius Free). Matiin per RPC: `"probe": false`.

## Set kuota plan lu

Angka default = plan gratis per akhir 2026 (Helius Free 1 jt credit/bulan & 10 rps, Alchemy Free
30 jt CU/bulan & ±25 rps). Plan berubah-ubah, jadi cek dashboard provider-nya, lalu
`cp rpc_monitor/config.example.json rpc_monitor/config.json` dan edit:

- `plans`: dicocokin ke link RPC (`match`), dicek sebelum yang bawaan. `monthly` = kuota per bulan
  dalam unit provider, `per_call` = rata-rata unit per request (Alchemy: `eth_getLogs` 75 CU,
  `eth_call` 26, `eth_blockNumber` 10 → default 20), `daily` = batas request per hari, `rps`.
- `endpoints`: override satu RPC aja pakai id-nya (`host#hash`, ada di **Detail** tiap kartu).
- `RPC_CALLS_PER_DAY` di `rh_wallet_finder/.env` otomatis jadi batas harian RPC_URL-nya.

SolidRPC belum ada angka bawaan: isi `monthly` / `daily` sesuai plan lu, kalau nggak, statusnya
cuma dari 429 + probe.

## Sol Copy Bot

Kodenya nggak ada di repo, link RPC-nya di Keychain (`Sol Copy Bot`). Monitor nyoba account
`rpcs`, `rpc`, `RPC_URLS`, `SOLANA_RPC_URL` (macOS bakal nanya izin sekali, klik *Always Allow*).
Kalau nama account-nya beda, set di `config.json` → `extra_bots` → `keychain.accounts`, atau
tulis linknya langsung di `urls`. Sol Copy Bot belum pakai meter, jadi statusnya dari probe +
log 429 (isi `logs` kalau dia bukan launchd agent). Kalau dia Python, tinggal tambahin
`rpcmeter.hit("sol_copy_bot", url, outcome)` di fungsi request-nya (copy `meter.py`).

## Yang belum kehitung

- Widget Scriptable di iPhone (RH Portfolio / MET Portfolio) pakai key yang sama tapi dari HP,
  jadi nggak lewat meter. Yang kelihatan cuma efeknya (429 / kuota habis di bot lain / probe).
- Kuota asli di sisi provider (Helius / Alchemy nggak punya API publik buat sisa kuota plan
  gratis). Angka "bulan ini" = yang dihitung meter sejak dipasang, jadi bulan pertama masih kurang.

## Alert Telegram (opsional)

`cp rpc_monitor/.env.example rpc_monitor/.env`, isi `RPC_MONITOR_TELEGRAM_TOKEN` +
`RPC_MONITOR_TELEGRAM_CHAT_ID`, jalanin lagi `install.sh`. Pesan dikirim pas RPC jadi 🟠/🔴
(paling sering tiap 3 jam per RPC) dan sekali pas 🟢 lagi.

Tes: `python3 -m unittest discover -s rpc_monitor/tests` (offline).
