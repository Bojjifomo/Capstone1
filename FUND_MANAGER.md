# Fund Manager: infrastructure + planning workflow

Claude acts as the **portfolio manager / analyst** for all your money: it keeps the books,
measures what every sleeve really yields, watches risk, and proposes what to do next.
**You stay the custodian and the one who executes.** Claude never holds keys, seed phrases,
broker logins or exchange API keys with trade/withdraw rights; every order is a checklist you
sign yourself. That is the line between a fund manager you can trust and a bot that can
empty your wallet.

Files:

| File | What it is | In git? |
| --- | --- | --- |
| `FUND_MANAGER.md` | This doc: how the system works | yes |
| `portfolio/ips.example.md` | Investment Policy Statement template: targets, bands, limits, kill rules | yes |
| `portfolio/holdings.example.csv` | Ledger format: one row per holding | yes |
| `portfolio/ips.md`, `portfolio/holdings.csv`, `portfolio/journal/` | Your real numbers and decisions | **no** (`.gitignore`) |
| `.claude/agents/fund-manager.md` | The Claude Code subagent that runs this workflow | yes |

---

## 1. Infrastructure

```
 SOURCES (read-only)                 BOOKS                    BRAIN                    OUTPUT
 ─────────────────────               ──────────               ──────────────           ───────────────────────
 IDX broker screenshots ───┐
 CEX balances / exports ───┤
 On-chain wallets ─────────┼──► holdings.csv  ──┐
   (Alchemy RPC, read-only)│    (the ledger)    │
 Own LP positions ─────────┤                    ├──► fund-manager ──► Daily brief / alerts
   (finder.py check 0x…)   │    ips.md ─────────┤    agent             Rebalance proposals
 Prices: CoinGecko,        │    (the rules)     │      │               Order checklists ──► YOU execute
   GeckoTerminal, IDX close┘                    │      │               Monthly report        (broker app,
                                journal/ ───────┘      │                                      CEX, wallet)
 Opportunity scanners ───────────────────────────────►─┘                       │
   rh_pools, pool-results, Smart LP, LP Call Bot                               ▼
                                                                    journal/ (what was done + why)
```

### Layers

| Layer | Job | Built on | Status |
| --- | --- | --- | --- |
| **1. Ledger** | One source of truth: every holding, its sleeve, cost basis, where it lives | `portfolio/holdings.csv` | template in this PR |
| **2. Rules (IPS)** | Targets per sleeve, rebalance bands, position caps, kill rules, what needs your approval | `portfolio/ips.md` | template in this PR |
| **3. Data feeds** | Prices + balances, read-only | Screenshots (`idx-stock-check`, `portfolio-monthly-review` skills), Alchemy RPC (already connected), CoinGecko / GeckoTerminal, `finder.py check <own wallet>` | partly there |
| **4. Analytics** | Allocation vs target, drift, net yield per sleeve (after fees, IL, tax), drawdown, concentration, IDR/USD exposure | agent today; `portfolio.py` later (Phase 2) | to build |
| **5. Opportunity scan** | Where extra yield is, ranked by risk | `rh_pools`, `pool-results` (net APR after IL), Smart LP, `dca-tranche-check`, bank buy ladder | there |
| **6. Decision + approval** | Signal → IPS check → size → checklist → **you approve** → execute → log | `fund-manager` agent + journal | in this PR |
| **7. Reporting / alerts** | Daily brief, DCA days, monthly review, triggers | orchestrator `brief`, LP Call Bot / Telegram, Agent Office | partly there |

### Authority matrix

| Claude does alone | Needs your "yes" | Never |
| --- | --- | --- |
| Read balances, prices, LP data | Any buy / sell / swap | Hold private keys, seeds, broker passwords |
| Update the ledger from your screenshots | Open / close / move an LP | Withdraw or transfer funds |
| Compute allocation, drift, yields, risk | Changing the IPS targets or limits | Use leverage, borrow, or loop yield unless the IPS allows it |
| Propose rebalances and size them | Moving money between sleeves | Chase a position past a kill rule |
| Alert on triggers, write the journal | Spending RPC quota / publishing (`sync`, `publish`) | Put a holding above its cap "just this once" |

---

## 2. The sleeves (how the money is organised)

Every rupiah / dollar sits in exactly one sleeve. Each sleeve has one job, so yield is never
judged in isolation from risk.

| # | Sleeve | Job | Typical instruments | Starting target (moderate profile, **edit in your IPS**) |
| --- | --- | --- | --- | --- |
| 0 | **Safety / runway** | 6–12 months of expenses, never touched for trades | Savings, RDPU (money market funds), deposito, retail SBN (ORI / SR / ST) | 15–25% |
| 1 | **Core equity (IDX)** | Long-term compounding + dividends | BBCA, BMRI, BBRI, ASII, UNTR, PTBA; bank buy ladder | 30–40% |
| 2 | **Crypto core** | Long-term asymmetric growth | BTC (+ HYPE), biweekly DCA | 20–30% |
| 3 | **Stable yield** | Dollar yield with low price risk | Stablecoins in conservative venues only; capped per venue | 5–10% |
| 4 | **Active LP (alpha)** | High yield, high risk, actively managed | Uniswap V4 LPs on Robinhood Chain | **hard cap** 5–10% |

Rule of thumb: the further down the table, the smaller the cap and the stricter the kill rules.
Profit flows **up** the table (from LP into core), never the other way to chase losses.

---

## 3. What makes the funds perform better (the levers, ranked)

Ranked by expected impact for a portfolio like yours, highest first.

1. **Sweep LP profits into core every week.** The LP sleeve is where the yield is, but it is
   also where it disappears. Above the LP cap, profit is moved into BTC / IDX / SBN.
   Volatile alpha becomes compounding capital. Biggest single lever.
2. **Judge LP by net APR, not fee APR.** `pool-results` already gives `net_apr` (after
   impermanent loss). A 300% fee APR with −20% net is a losing pool. The agent only proposes
   pools where 7-day net APR > 0 and one wallet isn't the whole result (`top_share`).
3. **Kill idle cash drag.** Cash above the runway target goes to money market / SBN the same
   week, not "later". Uninvested cash is a guaranteed loss to inflation.
4. **Rebalance by bands, not by feeling.** Rebalance when a sleeve drifts more than ±5 pp
   (or 25% relative) from target; otherwise do nothing. Use new cash flow first, sells second
   (fewer fees, less tax).
5. **DCA with a value tilt.** Keep the biweekly BTC / HYPE tranche, but scale it (e.g. 0.5×
   when far above the long-term average, 1.5–2× in deep drawdowns). `dca-tranche-check` runs this.
6. **Bank buy ladder on IDX.** Pre-set limit prices for BBCA / BMRI / BBRI; buy only when a
   rung fires. Reinvest dividends instead of letting them sit in RDN.
7. **Position caps.** No single stock > 15% of the IDX sleeve; no single LP token > 30% of the
   LP sleeve; no single stablecoin venue > 50% of sleeve 3.
8. **Cut fees and tax leaks.** Fewer, larger trades; cheaper brokers / exchanges; track
   Indonesian rules on crypto transaction tax and dividend tax treatment (verify the current
   rules with a tax advisor — they have changed recently).
9. **Kill rules decided in advance.** LP position −X% → close; stablecoin depeg > 1% → exit;
   token under liquidity / mcap floor → close. Written in the IPS so nobody argues mid-crash.
10. **Journal + 30-day post-mortem.** Every decision logged with its thesis; reviewed after
    30 days. This is how the system gets better instead of repeating mistakes.

---

## 4. Planning workflow

### Cadence

| When | What | Who / tool | Time for you |
| --- | --- | --- | --- |
| **Daily** (morning) | Brief: net worth, sleeve drift, LP positions in/out of range, triggers fired, top pools today | `fund-manager` agent (+ `orchestrator brief`) | 2–5 min |
| **Event-driven** | Alert when a trigger fires (below) → proposal + checklist | agent → Telegram | when it happens |
| **Weekly** (Sun) | LP review: net APR per position, close losers per kill rules, **profit sweep** into core | agent proposes, you execute | 15 min |
| **Biweekly** | BTC / HYPE DCA tranche, sized by the value tilt | `dca-tranche-check` | 5 min |
| **Monthly** (1st) | Full review: update ledger from screenshots, performance per sleeve vs benchmark, rebalance if out of band, cash → yield | `portfolio-monthly-review` | 30 min |
| **Quarterly** | IPS review: targets, caps, kill rules; post-mortem on the journal | you + agent | 1 h |
| **Yearly** | Tax summary, income needs, risk profile check | you (+ advisor) | — |

### Triggers (event-driven)

| Trigger | Proposal |
| --- | --- |
| Sleeve drift > band | Rebalance plan (new cash first, then trims) |
| LP position out of range / net APR < 0 for 3 days | Re-range or close |
| LP sleeve above cap | Sweep the excess into core |
| BTC / HYPE drawdown beyond tilt threshold | Larger DCA tranche |
| IDX bank ladder rung hit | Buy order checklist |
| Stablecoin depeg > 1% / venue incident | Exit checklist |
| Cash above runway target | Move to RDPU / SBN |
| Watched wallet / LP Call Bot call | Check vs IPS before copying |

### One decision, end to end

```
Signal ─► IPS check ─► Size ─► Pre-trade checklist ─► YOUR approval ─► You execute ─► Log ─► 30-day review
         (sleeve cap,  (≤ cap,  (net APR, liquidity,   ("yes" in chat)   (broker/CEX/    (journal/
          kill rules,   risk     IL / drawdown,                           wallet)         YYYY-MM.md)
          band)         budget)  exit plan, fees/tax)
```

Every proposal the agent makes has the same shape:

> **What:** buy / sell / open / close · **Sleeve:** n · **Size:** amount + % of NW
> **Why:** the signal and numbers · **Risk:** what goes wrong + max loss · **Exit:** kill rule / target
> **IPS check:** ✓ within cap / band · **Checklist:** the exact steps for you

---

## 5. Build roadmap

| Phase | What | Result |
| --- | --- | --- |
| **1 (this PR)** | IPS + ledger templates, `fund-manager` subagent, this workflow | Claude can run the monthly / weekly / daily loop from your screenshots and the LP tools |
| **2** | `portfolio/portfolio.py`: reads `holdings.csv` + prices → allocation, drift, P&L, net yield per sleeve, as JSON; wired as an orchestrator tool | Numbers computed by code, not by hand |
| **3** | Read-only on-chain balances (Alchemy) + own LP positions via `finder.py check <your wallet>` merged into the ledger automatically | Crypto + LP sleeves update themselves |
| **4** | Triggers → Telegram (reuse the LP Call Bot / orchestrator brief on launchd) | Alerts without opening Claude |
| **5** | Backtests: DCA tilt, rebalance bands, "copy wallet X" (`rh_backtest` from TOOLS.md) | Rules tuned on your own data |

## 6. What I need from you to start

1. **Risk profile + goals:** time horizon, monthly expenses (for the runway), max drawdown you
   can sit through on the whole portfolio (e.g. −15% / −25% / −40%).
2. **Current holdings:** screenshots of broker (IDX), exchanges, wallets (addresses are enough
   for on-chain), bank / RDPU / SBN balances. Copy `portfolio/holdings.example.csv` to
   `portfolio/holdings.csv` or just send screenshots and the agent fills it.
3. **Confirm or edit the sleeve targets** in a copy of `portfolio/ips.example.md` → `portfolio/ips.md`.
4. **Monthly cash flow:** how much new money goes in each month.

Then run: `pakai fund-manager, update portofolio` (or "use fund-manager, monthly review").
