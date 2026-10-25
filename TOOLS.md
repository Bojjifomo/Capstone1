# Tools di repo ini

Semua tools yang udah dibikin di `Capstone1`, mana yang kepake, gimana nyambungnya, dan
agent **orchestrator** yang ngejalanin semuanya dari satu tempat.

## Yang kepake

| Tool | Folder | Isinya | Jalan di |
| --- | --- | --- | --- |
| **LP analysis wallet data** (RH Wallet Finder) | `rh_wallet_finder/` | Index semua posisi LP Uniswap V4 di Robinhood Chain langsung dari chain, ngehargain tiap posisi, ranking wallet jadi Multiday vs Degen, Real PnL, twin finder, Watchlist, cek LP Agent, cek vs RH Discovery. App Streamlit + worker sync di background. | Mac (`Buka Wallet Finder.command`) |
| **RH Smart LP Finder** | folder sebelah (`../RH Smart LP Finder`, bukan di repo ini) | Ranking LP satu pool, dihitung pas diklik. Wallet Finder baca database-nya (read-only): saved wallets → Watchlist, ranking per pool → kolom **Smart LP**. Kodenya: `rh_wallet_finder/smartlp.py`. | Mac, `localhost:8787` |
| **Website widget** (web showcase) | `rh_wallet_finder/web/` | Halaman read-only di Vercel: board Multiday / Degen / 24h, filter, preset, detail wallet. Diisi snapshot yang di-upload Mac (`publish.py` → `/api/publish` → Vercel Blob → `/api/snapshot`). | Vercel |

Alurnya:

```
Robinhood Chain RPC ──► Wallet Finder (Mac) ──publish──► Vercel Blob ──► web showcase
                            ▲   │
   RH Smart LP Finder ──────┘   └──► LP Agent API (cek silang)
   (data/lp-finder.sqlite)
```

## Arsip (udah nggak dipakai)

| Tool | Folder | Isinya | Jalanin |
| --- | --- | --- | --- |
| Vault Tracker | `archive/vault_tracker/` | Pantau vault Hedgin / Meteora DLMM di Solana, alert Telegram, bot copytrade. | `cd archive && python -m vault_tracker …` |
| Hospital Patient Management | `archive/hospital/` | Tugas Capstone Module 1 (Python + MySQL). | `cd archive/hospital && python app.py` |

Kodenya nggak diubah, cuma dipindah; orchestrator nggak nyentuh dua-duanya.

## Orchestrator: satu agent buat semuanya

`orchestrator/` = agent Claude yang bisa manggil semua tools di atas. Lu tanya biasa
("top 5 multiday yang lagi panas minggu ini, yang juga masuk top 10 Smart LP?"), dia
yang mutusin tool mana yang dipanggil, baca hasilnya, terus jawab.

```bash
python3 -m pip install -r orchestrator/requirements.txt   # sekali aja
export ANTHROPIC_API_KEY=sk-ant-…                         # atau tulis di Capstone1/.env
python3 -m orchestrator                                   # chat
python3 -m orchestrator ask "wallet 0x5784…4ffd gimana? mirip siapa?"
python3 -m orchestrator brief --out brief.md              # laporan harian
python3 -m orchestrator tools                             # daftar tools + risikonya
```

Jalanin di Mac yang sama dengan Wallet Finder (dia baca `rh_wallet_finder/data/wallets.db`
dan `.env` di sana). Wallet Finder nggak harus lagi jalan buat ngejawab pertanyaan;
datanya aja yang perlu udah ke-sync.

| Tool agent | Risiko | Ngapain |
| --- | --- | --- |
| `rh_status` | read | Sync / ranking terakhir, antrean harga, RPC hari ini vs batas, apa aja yang udah di-set |
| `rh_top` | read | Top wallet per kelas (JSON), bisa Real PnL / 24 jam |
| `rh_wallet` | read | Satu wallet: metrik, label, ranking Smart LP, posisi terbaru |
| `rh_lpagent` | read | Wallet yang sama dicek ke API LP Agent |
| `rh_pools` | read | Pool fee terbaik: fee APR, net APR (setelah IL), siapa yang cuan di sana |
| `rh_twin` | read | Wallet yang gaya LP-nya paling mirip |
| `rh_compare` | read | Angka kita vs baris yang di-copy dari RH Discovery |
| `rh_sql` | read | SELECT read-only ke database, buat pertanyaan yang nggak ke-cover tools lain |
| `slf_summary` | read | Pool yang udah di-scan Smart LP Finder + wallet peringkat terbaik di sebelah skor kita |
| `web_check` | read | Snapshot yang lagi tayang di web: umurnya, jumlah wallet, cocok nggak sama publish terakhir |
| `read_docs` | read | Baca README ini / README Wallet Finder |
| `rh_sync`, `rh_score` | write | Sync / hitung harga sekali jalan (makan kuota RPC) |
| `rh_import`, `slf_import` | write | Tambah wallet ke Watchlist / tarik data Smart LP Finder sekarang |
| `web_publish` | write | Upload snapshot ke web sekarang |

Tools **write** selalu nanya `y/N` dulu (`--yes` = jawab ya otomatis). Key/RPC di `.env`
nggak pernah dikirim ke Claude: tool-nya cuma ngasih tahu udah di-set atau belum.

Modelnya `claude-opus-5-5` (ganti pakai `ORCHESTRATOR_MODEL`), effort `medium`
(`ORCHESTRATOR_EFFORT=high` buat analisis yang lebih dalem). Kalau Claude nolak satu
permintaan, API-nya otomatis lempar ke model cadangan (`fallbacks: "default"`).

Pakai Claude Code? Ada juga subagent `.claude/agents/lp-orchestrator.md` dengan aturan
yang sama, jadi bisa langsung "pakai lp-orchestrator, cek board hari ini" tanpa API key
terpisah.

Perintah baru di Wallet Finder yang dipakai agent (bisa juga dipakai manual):

```bash
cd rh_wallet_finder
python3 finder.py status              # state sync / ranking / RPC, JSON
python3 finder.py top --json -n 20    # top wallet, JSON
python3 finder.py check 0x… --json    # satu wallet + posisinya, JSON (tanpa LP Agent)
python3 finder.py twin 0x…            # wallet yang paling mirip, JSON
python3 finder.py smart-lp            # ringkasan Smart LP Finder vs skor kita, JSON
python3 finder.py web-check           # snapshot live di web vs publish terakhir, JSON
```

Tes: `python3 -m unittest discover -s orchestrator/tests -t .` (offline, nggak manggil API).

## Di iPhone

- **Widget home screen** (app Scriptable): pool fee terbaik + top wallet, dari `/api/widget`
  di web showcase, jadi tetap kebuka walau Mac tidur.
- **Ngobrol sama agent**: `caffeinate -i claude remote-control` di folder `Capstone1` di Mac,
  terus buka sesinya dari app Claude di iPhone (bagian Code).

Langkahnya: [`rh_wallet_finder/ios/README.md`](rh_wallet_finder/ios/README.md).

## Tools yang dibutuhin biar lebih improve

Urut dari yang paling ngaruh:

1. **API scan di RH Smart LP Finder.** Sekarang Smart LP Finder cuma ngitung pas lu klik,
   jadi agent cuma bisa baca hasil scan lama. Kalau server `localhost:8787` punya endpoint
   kayak `POST /api/scan {pool}` (+ `GET /api/scan/<id>` buat hasilnya), agent bisa
   nge-scan sendiri pool-pool tempat wallet top lagi aktif dan ngecek silang otomatis.
   Butuh: source code Smart LP Finder (nggak ada di repo ini) — kirim/taruh di repo, nanti
   gue tambahin.
2. **Alert wallet Watchlist ke Telegram.** Worker udah baca semua event LP tiap 5 menit;
   tinggal kirim pesan pas wallet yang di-★ buka / tutup / geser range (pool, range, size,
   link). Ini yang paling kepake buat copy manual. Butuh: bot Telegram (`TELEGRAM_BOT_TOKEN`
   + `TELEGRAM_CHAT_ID`, caranya sama kayak di `archive/vault_tracker/README.md`).
3. **Brief otomatis tiap pagi.** `python3 -m orchestrator brief` dijadwalin pakai launchd di
   Mac, hasilnya ke Telegram (nyambung ke no. 2). Butuh: Mac nyala di jam itu + `ANTHROPIC_API_KEY`.
4. **Backtest "kalau gue copy wallet X".** Dari tabel `positions` udah bisa dihitung: modal
   $Y, delay masuk N menit, slippage → PnL 30 hari kalau ngikutin wallet itu. Jadi tool
   `rh_backtest` buat agent. Nggak butuh apa-apa dari luar.
5. **Data token** (umur token, market cap, likuiditas, holder) buat nyaring wallet yang
   untungnya cuma dari satu memecoin yang udah mati. Butuh: API data token yang support
   Robinhood Chain (cek DexScreener / GeckoTerminal / Blockscout API), atau dihitung sendiri
   dari pool di chain.
6. **Endpoint `/api/health` + link per wallet di web showcase**, biar `web_check` nggak perlu
   download satu snapshot penuh dan wallet bisa di-share langsung (`…/#0x…`).
7. **MCP server** dari `orchestrator/tools.py`, biar tools ini bisa dipanggil dari Claude
   Desktop / claude.ai, bukan cuma dari terminal.
8. **CI di GitHub Actions** buat jalanin dua suite tes tiap push (sekarang belum ada CI sama
   sekali), biar perubahan nggak diem-diem ngerusak ranking.
