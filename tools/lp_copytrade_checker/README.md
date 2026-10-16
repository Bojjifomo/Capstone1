# LP Copy-Trade Checker (Uniswap V4)

Find out whether bots are **copying a wallet's LP opens and closes**, then dumping the token right after it closes.
Works as a **web app (Streamlit)** or from the **command line**.

## How it works

1. Grab the wallet's most recent LP opens/closes (Uniswap V4 position NFT mints and burns).
2. For each one, scan the PoolManager's `ModifyLiquidity` events for the next few seconds (the "window").
3. Any other wallet that opens/closes in the **same pool with the exact same tick ranges** is recorded as a follower, along with how many seconds later it acted.
4. After the wallet closes, count how much of the pool token (stablecoins and ETH excluded) each follower moves out during the window.

Follower status:

| Status | Meaning |
|---|---|
| `Copier` | Mirrored 2 or more of the wallet's LP actions (for example an open **and** a close). Almost certainly a copy-LP bot. |
| `Suspect` | Copied 2+ of the wallet's exact ranges, but only once so far. |
| `Weak` | A single matching range, once. Could be chance. |

## Web app (Streamlit)

Run it locally from the **repo root** so the dark theme in `.streamlit/config.toml` is picked up:

```bash
pip install -r tools/lp_copytrade_checker/requirements.txt
export ALCHEMY_API_KEY=xxxx   # optional; can also be entered in the form
streamlit run tools/lp_copytrade_checker/streamlit_app.py
```

Deploy for free on **Streamlit Community Cloud**:

1. Go to https://share.streamlit.io → sign in with GitHub → **Create app**.
2. Repo: `bojjifomo/capstone1`, the branch this folder is on, main file path
   `tools/lp_copytrade_checker/streamlit_app.py`.
3. (Optional, recommended) **Advanced settings → Secrets**:
   ```toml
   ALCHEMY_API_KEY = "your-alchemy-key"
   ```
4. Deploy. You'll get a `https://<app-name>.streamlit.app` URL.

### The Alchemy API key field

- If `ALCHEMY_API_KEY` is set in Secrets, the key field is **optional**: visitors only need to paste a wallet.
  Anyone who wants faster results, or doesn't want to wait in line, can enter their own key.
- If Secrets is empty, every visitor has to enter a key.
- A key entered in the form is only used for that check. It's never stored, logged, or shown on the page.

You can create a free Alchemy key at https://dashboard.alchemy.com. Make sure the chain you want to check
(for example **Robinhood Chain**) is enabled on that Alchemy app.

### Sharing the app

On the deployed app, click **Share** (top right) → make the app **public**, then copy the link.
If the repo is private, the app starts out private too, so this step is required.

To keep the shared key within its quota when traffic picks up:

- Results are cached for 10 minutes per wallet.
- Checks that use the Secrets key run one at a time (a queue). Visitors with their own key skip the queue.

### Theme

Colors live in `.streamlit/config.toml` (repo root) and the CSS in `streamlit_app.py`:
dark teal background, cards, and mint accents, matching the LP Web widget.

## Command line

```bash
export ALCHEMY_API_KEY=xxxx
python3 checker.py 0xWallet                         # Robinhood Chain by default
python3 checker.py 0xWallet --events 15 --window 90
python3 checker.py 0xWallet --network base-mainnet  # any other chain with Uniswap V4
```

The CLI only uses the Python standard library. Options:

- `--events`: how many recent LP txs to check (default 8)
- `--window`: seconds to watch after each action (default 60)
- `--include-full-range`: also match full-range positions (skipped by default because they're often matched by chance)
- `--rpc`: full Alchemy URL, if you'd rather not use the environment variable

## Speed

Alchemy's free tier only allows `eth_getLogs` over 10 blocks per request. Robinhood Chain produces a block about
every 0.1 s, so a 60-second window takes about 60 requests per action, and a 6-action check takes roughly
1–3 minutes. The script adjusts the chunk size automatically. With a paid (PAYG) Alchemy key, each action takes a
single request and checks are much faster.

## Limitations

- Uniswap V4 only (PoolManager `ModifyLiquidity`). V3 isn't supported yet.
- Only actions that mint or burn a position NFT are detected. Partial decreases without a burn are missed.
- Followers are identified by `tx.from`. Bots that use a relayer show up as the relayer's address.
- "Dumped" means every transfer of the pool's non-stable token out of the follower's wallet during the window.
  That's usually a sell, but it can also be a fresh LP deposit.
