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
| GATE | Posisi ≤ 3% TVL | Guard exit pas jual sisa token |
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
