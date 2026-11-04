# DLMM Terminal: filter entry + auto exit (Solana LP)

Implementasi **DLMM-Auto-Entry-Spec v1** (133 posisi closed, 23 token, Oct 25 – Nov 4 2026).
Dua bagian:

1. **`screen`**: filter pool Meteora DLMM SOL: 4 gate (wajib lolos) + 4 flag (cuma warning). Semua kandidat ke-log, termasuk yang di-skip.
2. **`monitor`**: tiap 5 menit baca PnL posisi live dari wallet lu (API Meteora, 0 RPC), log ke file, kirim alert, dan jalanin exit rule. Mode `auto` nutup posisinya sendiri (remove 100% + claim + close) lewat SDK Meteora.

Stdlib Python doang buat filter + monitor. Node cuma buat executor close.

## Pakai

```bash
python3 -m dlmm_terminal screen                    # scan semua pool SOL, tampil yang lolos gate
python3 -m dlmm_terminal screen --all              # + yang di-skip dan alasannya
python3 -m dlmm_terminal screen --pool <POOL> --size 8
python3 -m dlmm_terminal decide <POOL> enter --reason "fee tinggi, no flag"
python3 -m dlmm_terminal monitor --wallet <WALLET>             # mode alert (default)
python3 -m dlmm_terminal monitor --wallet <WALLET> --mode auto --dry-run   # simulasi close, nggak kirim tx
python3 -m dlmm_terminal followup                  # isi harga 1h/6h/24h setelah tiap kandidat (buat v2)
python3 -m unittest dlmm_terminal.tests.test_dlmm_terminal
```

Mode auto beneran (kirim tx):

```bash
cd dlmm_terminal/executor && npm install
```

`.env` (di `dlmm_terminal/` atau root repo):

```
DLMM_WALLET=...                 # default --wallet
SOLANA_RPC_URL=https://...      # buat executor
DLMM_KEYPAIR=/path/keypair.json # cuma buat --mode auto tanpa --dry-run
DLMM_TG_BOT_TOKEN=...           # opsional: alert ke Telegram
DLMM_TG_CHAT_ID=...
```

Semua angka ada di `config.json`. Bankroll default 160 SOL × loss budget 5% → max posisi **8.89 SOL** (loss budget ÷ 0.9).

## Filter (gate = wajib, flag = warning)

| Tipe | Rule | Kenapa (dari data) |
| --- | --- | --- |
| GATE | Pool DLMM pair SOL, bin step 20–250 | Semua 133 posisi kayak gini; di luar itu nggak ada bukti |
| GATE | Umur pool ≥ 60 menit | 5 entry di pool < 1h: −1.74 SOL |
| GATE | Fee APR 24h (atau sejak launch) ≥ 1.000% | 8 entry di bawah itu: −1.30 SOL |
| ~~GATE~~ | ~~Posisi ≤ 3% TVL~~ | **Dimatiin** (2026-11-04): % TVL tetap ditampilin sebagai info. Nyalain lagi: `gates.maxPositionPctOfTvl: 3` |
| RISK | Range bottom −70% (wide), bukan −50% | Wide: +9.63 SOL / 61 posisi, terburuk −6%. Standard: −0.72 SOL, terburuk −93% |
| RISK | Size = loss budget ÷ 0.9 | Satu posisi pernah −93% |
| FLAG | pump 1h ≥ 25% · drop 6h ≥ 17% · TVL < $25k · entry 00–09 WIB | Bagus di hindsight, gagal di token yang belum dilihat → di-log, nggak nge-block |

Fee APR = fee N jam ÷ TVL × 8760/N × 100. Harga selalu dihitung sebagai token dalam SOL (pool SOL-TOKEN dibalik).

## Exit / auto TP

Hasil replay 131 posisi tiap 5 menit, dibanding exit asli lu:

| Rule | Hasil | Default |
| --- | --- | --- |
| Take-profit +1 / +2 / +5 / +10% | −7.16 / −7.57 / −4.37 / −1.06 SOL | **off** (`takeProfitPct: null`) |
| Stop-loss −10 / −15 / −25% PnL | −7.60 / −4.24 / −1.35 SOL | **off** |
| Trailing 5 / 10 / 25 pts | −7.27 / −7.66 / −1.82 SOL | **off** |
| Close setelah 10 menit di bawah range | **+0.22 SOL** | **on** (`closeBelowRangeAfterMin: 10`) |
| Price stop −40% dari entry | ~impas hindsight, −6.47 SOL di token baru | off (setting) |
| Alert PnL ≤ −10% | — | on |
| Alert harga masuk 1/3 bawah range | — | on |

Kenapa TP fix rugi: posisi SOL-only di bawah harga cuma dapet dari fee, PnL biasanya naik terus sampai akhir (winner median peak di menit 25 dari 28). TP nutup posisi yang masih ngasilin. Jadi "auto TP" default-nya: **satu-satunya rule yang nambah SOL** (keluar kalau udah 10 menit di bawah range) + alert buat sisanya. TP/SL/trailing tetep bisa dinyalain di `config.json`; monitor bakal kasih warning.

`mode: "alert"` = cuma ngabarin ("exit rule hit, tutup sendiri"). `mode: "auto"` = executor nutup posisi. Sisa memecoin dari range **nggak** di-swap otomatis; jual sendiri (di data, jual sisa token makan −3.93 SOL).

## Log (buat v2)

`dlmm_terminal/data/` (di-gitignore):

| File | Isi |
| --- | --- |
| `candidates.jsonl` | Satu baris per kandidat: semua fitur, gatesPassed/Failed, flags, decision, plan |
| `decisions.jsonl` | Keputusan lu (enter / skip + alasan) |
| `pnl_log.jsonl` | PnL tiap 5 menit per posisi + event rule |
| `closes.jsonl` | Close yang dijalanin executor |
| `followup.jsonl` | Harga 1h/6h/24h setelah kandidat + dip terdalam: bukti skip nyelamatin atau nggak |

Flag dipromote jadi gate kalau (spec §7): ≥ 300 posisi closed di 50+ token (15+ yang rugi > 5%), nambah SOL di token yang belum dilihat (leave-one-token-out), skip ≤ 45% kandidat, dan log skip-nya konfirm.

## Versi HP: MET Pool + MET Portfolio (Scriptable)

`dlmm_terminal/scriptable/` = script Scriptable lu (met-pool-v7, met-portfolio-v7) + rules spec v1. Mesin DLMM-nya tetap punya mereka (open ladder, close, claim, swap Jupiter, practice mode). Yang ditambahin:

| Script | Tambahan |
| --- | --- |
| **MET Pool** | Di bawah tiap pool: `✅ v1 gates pass` atau `⛔ <gate yang gagal>` + flag `⚠️`. Default cuma nampilin pool yang lolos semua gate (tap baris filter buat lihat semua). Layar konfirmasi zap nampilin gate/flag, cap loss budget, dan warning kalau range bottom < −65%. Semua kandidat ke-log ke `met_candidates.jsonl` tiap refresh di app, plus keputusan enter/skip pas konfirmasi. |
| **MET Portfolio** | Exit rule per posisi: alert PnL ≤ −10%, alert harga di 1/3 bawah range, **exit** kalau posisi SOL-only udah ≥ 10 menit di bawah range. Notifikasi iOS (sekali per kejadian), tombol `Exit → SOL` di baris token (close + claim + swap). `EXIT_MODE: "auto"`: buka app = langsung close + swap. PnL tiap refresh ke `met_pnl_log.jsonl`. TP/SL ada tapi default `null`. |

Setting baru ada di bagian 8–9 `MET Secrets.example.js`. Secrets lama tetap jalan (yang nggak ada pakai default spec), jadi cukup tambah `BANKROLL_SOL` kalau mau cap size.

Batasan iOS: widget refresh tiap ~15 menit dan nggak boleh kirim transaksi, jadi "10 menit di bawah range" = dua refresh yang jaraknya ≥ 10 menit, dan close-nya jalan pas app dibuka (auto) atau pas lu tap notifikasi (alert). Buat yang beneran jalan sendiri tiap 5 menit: `python3 -m dlmm_terminal monitor --mode auto` di Mac.

Test rules (Node, di luar Scriptable): `node dlmm_terminal/scriptable/test_rules.mjs`

## Versi web (kayak LP WEB, buat Solana)

Di Mac mini: double-click **`dlmm_terminal/Buka DLMM Web.command`** di Finder (browser kebuka sendiri ke `http://localhost:8790`, biarin jendela Terminal-nya kebuka). Dari HP lewat Tailscale: `http://<nama-mac-mini>:8790`. Pertama kali, macOS mungkin nolak file-nya: klik kanan → Open → Open.

```bash
python3 -m dlmm_terminal web                      # http://localhost:8790
python3 -m dlmm_terminal web --host 0.0.0.0       # buka dari HP lewat Tailscale
```

- **Pilih pool**: semua pool DLMM SOL (TVL ≥ filter), kolom TVL / FEES (1h · 4h · 24h) / FEE 1 JAM / MCAP + sparkline 24h / **ENTRY CHECK** / ZAP. Sort tiap kolom, search, `v1 pass only`, `hide SKIP`. Refresh tiap 1 menit.
- **ENTRY CHECK** = spec v1: `OPEN` (gate lolos, nggak ada flag) · `CAREFUL` (gate lolos, ada flag) · `WAIT` (gate yang bisa berubah gagal: umur, fee APR) · `SKIP` (bin step / bukan SOL). Skor + alasan kayak "Volume picking up", "Buyers lead" dari DexScreener cuma konteks, **nggak** ngubah level (spec: nggak ada sinyal entry yang tahan di token baru).
- **Detail** (klik baris): harga, mcap, TVL, fee/hari, kotak verdict, alasan ✓/✗, tombol zap, chart DexScreener.
- **Zap**: modal plan (size SOL, range bottom −70%, size vs TVL, cap loss budget, flag) → `Open di Meteora`, `Copy plan`, `Log: enter / skip` (ke `data/decisions.jsonl`). Web ini **nggak pegang key**; buka posisinya di Meteora atau MET Pool di HP.
- **Portfolio**: posisi terbuka wallet (Meteora API), PnL SOL + peak, range bar (merah = 1/3 bawah), EXIT CHECK pakai rules yang sama dengan `monitor` (dicek tiap 5 menit selama halaman kebuka). Close tetap dari MET Portfolio / Meteora / `monitor --mode auto`.
- Snapshot semua kandidat ke `data/candidates.jsonl` maksimal tiap 15 menit (buat v2).

## Buka & tutup posisi (ladder RH, Spot, SOL-only)

Bentuk ladder sama persis dengan MET Pool di HP (`executor/ladder.mjs` = `MET.planLadder`): **5 slice** lebar bin-nya sama, dari harga sekarang sampai **−70%**, isi SOL **1:2:3:4:5** (slice paling dalam paling gede), Spot di tiap slice. Contoh 8 SOL di bin step 80: 0.53 / 1.07 / 1.6 / 2.13 / 2.67 SOL.

Sekali aja di Mac: `cd dlmm_terminal/executor && npm install` (butuh Node: `brew install node`).

**Key (sekali, kayak LP WEB):** bikin `dlmm_terminal/.env` dari template dan buka di TextEdit (atau double-click `Buka DLMM Web.command`, yang bikinin otomatis kalau belum ada):

```bash
cp -n dlmm_terminal/.env.example dlmm_terminal/.env && chmod 600 dlmm_terminal/.env && open -e dlmm_terminal/.env
```

Isinya, lalu save dan start ulang server:

```
DLMM_PRIVATE_KEY=<private key base58, dari Phantom: Settings → Manage Accounts → Show Private Key>
SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=<key lu>
DLMM_WEB_PIN=<opsional: PIN, biar bisa trading dari HP>
```

Pakai wallet khusus LP yang isinya cuma yang siap lu risikoin. `.env` udah di-gitignore. Key cuma dibaca proses Node (executor) di Mac; Python dan browser cuma lihat address-nya. Chip kanan atas jadi **🔑 address** kalau key kebaca, **🔒 read-only** kalau belum.

Siapa yang boleh trading: tanpa `DLMM_WEB_PIN` cuma browser di Mac yang jalanin server; dengan PIN, device lain (HP via Tailscale) juga bisa, tapi ditanya PIN. Website lain yang kebuka di browser lu nggak bisa nyuruh server ini trading (butuh header khusus).

**Dari web:**
1. Zap di pool → modal nunjukin gate v1 + tabel 5 slice → **⚡ Buka ladder** → konfirmasi.
2. Server simulasi tiap slice, lalu kirim satu-satu. Kalau satu gagal, sisanya berhenti dan tetap SOL. Wallet kurang SOL = stop sebelum kirim apa pun.
3. Gate gagal atau di atas cap loss budget = ada konfirmasi tambahan, dicatat sebagai override.
4. Portfolio: tombol **Close** per posisi (tarik 100% + klaim fee + tutup akun, rent balik). Kalau exit rule nyala, tombolnya jadi merah **Exit → close**.

**Dari Terminal:**

```bash
python3 -m dlmm_terminal open <POOL> --sol 8                 # gate → plan 5 slice → ketik y → kirim
python3 -m dlmm_terminal open <POOL> --sol 8 --dry-run --owner <WALLET>   # simulasi aja
```

Pakai key yang sama di `.env` (`DLMM_PRIVATE_KEY`, atau `DLMM_KEYPAIR=/path/keypair.json`) + `SOLANA_RPC_URL`. Opsi: `--slices`, `--bottom`, `--top`, `--strategy spot|curve|bidask`, `--force` (gate gagal), `--force-size` (lewat cap).

Sisa memecoin setelah close **belum** di-swap otomatis ke SOL: jual di Jupiter / Cleanup di MET Portfolio.
