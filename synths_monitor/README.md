# synthmon: read-only monitor for a synths.lol trading agent

Watches the synth **@bojjibot** on Robinhood Chain. Every 60 seconds it records the
wallet balance, trades, posts, bags and the bot's own coin in SQLite. It pairs trades
into round trips, sends alerts to Discord or Telegram, and prints performance reports.

It is read-only. It never signs anything, never asks for a private key or seed phrase,
and refuses to start if the config holds anything shaped like one: a key named like
`private_key`/`mnemonic`/`seed_phrase`, or any 64-hex-character value.

## Quick start

```bash
cd synths_monitor
pip install -r requirements.txt
playwright install chromium          # only needed for `discover`

python -m synthmon setup             # asks for the wallet's PUBLIC address and a webhook
python -m synthmon discover          # records the JSON endpoints synths.lol calls
#   -> copy discovery/suggested_config.toml's [synths.endpoints] into config.toml
python -m synthmon test-alert        # check the webhook
python -m synthmon run               # poll every 60s with backoff, send alerts
python -m synthmon report --since 24h
```

For the on-chain fallback, set `ALCHEMY_API_KEY`, `chain.rpc_url` or `chain.explorer_api_url`.
With no synths endpoints configured, the monitor runs on chain data alone. Posts and
exit types need synths.lol, because posts only exist there.

## Which address to monitor

A synth has its **own account**, a contract created when the synth is registered. That
account holds the synth's ETH and trades. It isn't the wallet that created the synth.
`setup` and `find-synth` look the account up from the owner wallet, using the registry's
creation event:

```bash
python -m synthmon find-synth --owner 0xYourWallet
# @yoursynth  account 0x…  created 2026-10-13 03:06 UTC  block …  tx …
```

Put the account in `bot.wallet`, and optionally the owner in `bot.owner`.

## Commands

| command | what it does |
|---|---|
| `setup` | Prompts for the wallet address (rejects 64-hex input), handle and webhook. If the address created the synth, offers to monitor the synth's account instead. Writes `config.toml` with mode 600. |
| `find-synth --owner 0x…` | Lists the synths a wallet created, with each one's account, model and strategy prompt. |
| `discover [--headful]` | Opens synths.lol in Chromium: home, the synth's profile (clicks Posts / Bags / Trades / Launched), a coin page and the leaderboard. Records every JSON fetch/XHR into `discovery/`. |
| `poll` | One poll, then exits. Prints the sources used and any errors. |
| `run [--no-webhook]` | Polls every `poll.interval_seconds`. Refuses to start without a wallet, and without a webhook unless `--no-webhook` is passed. |
| `report --since 24h\|7d\|2026-10-01\|<unix> [--until] [--json]` | Trade count, win rate, average win vs loss, estimated fees, exits by type, own-coin stats, wallet vs baseline. |
| `roundtrips [--since]` | Lists every round trip. |
| `test-alert` | Sends one test message. |
| `reset-baseline` | Forgets the wallet baseline; the next poll sets a new one. |

## Data sources and fallback order

| data | 1st | fallback |
|---|---|---|
| trades | synths `trades` endpoint | wallet history on-chain (explorer, then RPC) |
| posts (full text) | synths `posts` | none (posts are off-chain) |
| bags | synths `bags` | on-chain token balances, valued at the last trade price |
| wallet balance | synths `profile` | `eth_getBalance` (explorer, then RPC) |
| own coin: holders, buys/sells, progress | synths `coin` (+ `holders`) | the coin's Transfer history on-chain; fills fields synths left empty |
| launched coins / own coin | synths `launched`, or `bot.own_coin` | none |
| leaderboard rank | synths `leaderboard` | none |

JSON-RPC calls are batched. On a rate limit, such as Alchemy's free tier, the batch
size halves and the call retries after a pause. That makes a long first backfill slower
but lets it finish: 5 days of a busy synth took about 5 minutes.

Each source has its own exponential backoff with jitter: base = the poll interval,
doubling up to `max_backoff_seconds`, with `Retry-After` honoured on 429. While one
endpoint is backing off, its data comes from the fallback. If a whole poll fails, the
loop also backs off.

### synths.lol endpoints

Endpoint URLs aren't hard-coded. `discover` writes templates with `{handle}`, `{wallet}`
and `{coin}` placeholders, picking the request scoped to this synth over global feeds.
Responses are parsed with tolerant field lookup: camelCase, snake_case, common synonyms
and nested `data`/`items`/`edges` lists. If a field is read wrongly, pin its path:

```toml
[synths.fields.trades]
eth_amount = "amounts.native"
```

Every distinct response body is kept in `raw_responses`, so data can be re-parsed after
a mapping fix.

### On-chain reconstruction

`wallet_activity` groups the wallet's ERC-20 and ETH transfers by transaction:

* **Explorer** (Etherscan-compatible API; Blockscout serves one at `<explorer>/api`):
  `txlist` + `txlistinternal` + `tokentx`, so sell proceeds paid as internal ETH
  transfers are exact.
* **RPC** (`alchemy_getAssetTransfers`): Alchemy doesn't index internal transfers on
  this network. A trade's ETH leg is therefore the wallet's balance change across the
  block, plus the gas it paid (from the receipt). When two wallet transactions share a
  block, only the visible external and WETH legs are counted, and the trade is flagged
  `approximate` in its `raw` column.

This is what synth trading looks like on-chain (checked on a running synth, 600 trades):
the synths backend's operator sends a call to the synth's account, which swaps through a
Uniswap v4 pool with a synths hook. ETH moves as internal transfers, so on the RPC
backend the balance-change method prices every trade, and gas shows as unknown because
the operator pays it. Synths also sell leftover dust, down to 1 wei of a token; that sell
is folded into the round trip it follows.

WETH (`0x0bd7…ad73` on Robinhood Chain) counts as ETH. One token in and ETH out is a
**buy**. Token out and ETH in is a **sell**. Token movements with no ETH leg are
`transfer_in` / `transfer_out`, for example the launch allocation.

Own-coin stats on-chain: a transfer from a contract (curve, pool or router) to a wallet
counts as a buy, and the reverse as a sell. Holders are wallets with a non-zero balance.
Only the 25 busiest counterparties are checked for contract code. A busy coin's history
is fetched 20 pages per poll, and no stats are written until it has caught up.
"Progress to open" on-chain needs `coin.curve_address`, a contract whose ETH + WETH
balance is the coin's raised ETH. Otherwise progress comes only from synths.lol.

## Round trips and exit types

A round trip opens on a buy while the bot holds none of that coin. It closes when the
position falls below 0.5% of its peak (`roundtrips.dust_fraction`).

* PnL = sell ETH − buy ETH, and % = PnL / buy ETH. Gas is tracked separately.
* Hold time = close − first buy.
* Round trips that include tokens with no priced buy (a transfer in, a sell with no
  recorded buy, missing ETH legs) are listed but left out of win rate and averages.
* If a source gives no token amounts for a coin, each sell closes the round trip.

The exit type is that of the sell that closed the trip:

1. A reason field on the trade record, if the API returns one.
2. Otherwise the closest post from 15 minutes before to 30 minutes after the sell that
   names the same coin ($SYMBOL, address or tx hash) and matches an exit pattern: take
   profit (`took profit`, `TP`, `target hit`…), stop loss (`stopped out`, `SL`, `cut
   losses`…), timer (`timer`, `max hold`, `time limit`…) or AI (`my analysis`,
   `decided to sell`, `AI`…; needs a sell word too).
3. Otherwise `unknown`.

Add your bot's own phrasing under `[exits]` once you have seen real posts.

## Alerts

| alert | fires | de-duplication |
|---|---|---|
| bot sells / moves out its own coin | any own-coin sell or transfer out | once per trade. Sells already in the data when monitoring starts are recorded but not sent. |
| own coin passes 50% / 90% to open | latest progress ≥ threshold (`progress_thresholds_pct`) | once per threshold |
| wallet down > 15% from baseline | value < baseline × 0.85 | once per episode. Re-arms after recovering to within 10%. |
| no trades for 2 hours | last trade older than `no_trade_hours` | once per quiet stretch |

The wallet value is ETH + bags when bag values are known (`wallet_metric = "total"`).
That way, ETH moving into a position doesn't count as a loss. The baseline is the first
value observed, or `alerts.baseline_eth`. Failed deliveries are retried on later polls,
up to 5 attempts. Everything is in the `alerts` table.

## SQLite tables

`wallet_snapshots`, `trades`, `posts` (full text and the parsed exit type), `bags`
(snapshots), `launched`, `own_coin_snapshots`, `coin_transfers`, `round_trips` (rebuilt
every poll), `leaderboard`, `alerts`, `polls`, `raw_responses`, `state`. Times are unix
seconds (UTC), and amounts are in ETH.

## What has and hasn't been verified

* **Tested against live Robinhood Chain mainnet** (chain id 4663, via Alchemy):
  * A public wallet that is actively trading: one poll rebuilt 91 trades into 46 round
    trips. On the 4 trades spot-checked, the ETH amounts matched the WETH transfers in
    the swap's own logs. A token with 23k transfers synced in two polls.
  * A running synth ("tenner"): 600 trades over 5 days became 145 round trips, with win
    rate, average win/loss and hold times.
  * `find-synth` found a newly created synth's registration and account from its owner wallet.
* **Not yet seen: synths.lol's real API.** This build environment's network policy
  blocked synths.lol, so `discover` has only run against a local stand-in app. The
  field heuristics are guesses until `discover` runs against the real site.
* **Explorer backend**: written to the Etherscan-compatible API and tested with fakes.
  The Robinhood Chain explorer's API URL isn't filled in; set `chain.explorer_api_url`.
* **Fees**: gas is measured on-chain, or extrapolated from the median when a source
  omits it. The platform fee uses `fees.protocol_fee_bps` (default 1%, an assumption).
  Set it to synths.lol's actual fee.

## Tests

```bash
python -m pytest -q
```
