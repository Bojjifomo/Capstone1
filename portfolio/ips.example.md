# Investment Policy Statement (IPS)

Copy to `portfolio/ips.md` (git-ignored) and fill in. The fund-manager agent treats this file
as the law: it never proposes anything outside it, and only you change it.

## Profile

- Base currency for reporting: IDR (also show USD)
- Horizon: ____ years
- Monthly expenses: Rp ____ → runway target = ____ months
- Monthly new money: Rp ____
- Max portfolio drawdown I accept: ____ %
- Income needed from the portfolio: none / Rp ____ per month

## Sleeve targets and bands

| Sleeve | Target % | Min % | Max % | Notes |
| --- | --- | --- | --- | --- |
| 0 Safety / runway | 20 | 15 | 25 | Never used for trades |
| 1 Core equity (IDX) | 35 | 30 | 40 | |
| 2 Crypto core (BTC, HYPE) | 25 | 20 | 30 | BTC ≥ 70% of this sleeve |
| 3 Stable yield | 10 | 5 | 10 | |
| 4 Active LP | 10 | 0 | **10 (hard cap)** | Excess swept weekly |

Rebalance when a sleeve is outside Min / Max, or drifts more than ±5 pp from target.
Use new money first, then trims.

## Position limits

- Single IDX stock ≤ 15% of sleeve 1
- Single LP token ≤ 30% of sleeve 4; single LP position ≤ ____ USD
- Single stablecoin venue ≤ 50% of sleeve 3
- No leverage, no borrowing, no looping (change only here)

## Kill rules

- LP position down ____ % from entry (incl. IL) → close
- LP pool 7-day net APR < 0 for 3 days → close or re-range
- LP token mcap < $500k or pool TVL < $5k → close
- Stablecoin off peg > 1% for 1 h → exit that stablecoin
- Any venue incident (exploit, paused withdrawals) → exit that venue

## DCA

- BTC / HYPE tranche: Rp ____ every 2 weeks, split ____ / ____
- Value tilt: 0.5× far above long-term average · 1× normal · 1.5–2× in deep drawdown

## IDX buy ladder

| Ticker | Rung 1 | Rung 2 | Rung 3 | Size per rung |
| --- | --- | --- | --- | --- |
| BBCA | | | | |
| BMRI | | | | |
| BBRI | | | | |

## Approval

Every order, LP open / close, transfer between sleeves, and any change to this file needs my
explicit "yes". The agent never holds keys or credentials that can trade or withdraw.
