// ============================================================================
//  MET Secrets
//  ---------------------------------------------------------------------------
//  Name this script exactly "MET Secrets".
//
//  Set this up ONCE. "MET Pool" and "MET Portfolio" both read from it, so when
//  a new build of those arrives you paste it straight over the old one and
//  nothing here is touched: your keys and presets survive every update.
//
//  This file is never shared and never updated. Keep it on your phone only.
//  Anyone who reads SOLANA_PRIVATE_KEY below can spend everything in that
//  wallet.
// ============================================================================

module.exports = {

  // -- 1. Helius. Required to open, claim or close. --------------------------
  // The connection to Solana. Free at helius.dev: sign in, the dashboard shows
  // an API key straight away. Paste only the key (the part after "api-key="),
  // not the whole URL. Without it the scripts can still show pools and a
  // wallet, read-only, through the public node.
  HELIUS_API_KEY: "",

  // Optional: a full RPC URL from another provider (QuickNode, Triton, ...).
  // Used first when set; leave empty to use Helius.
  SOLANA_RPC_URL: "",

  // -- 2. Wallet private key. Required to open, claim or close. --------------
  // Phantom: Settings > Manage Accounts > your account > Show Private Key.
  // Solflare: Settings > Export Private Key. Either gives one long base58
  // string; paste it between the quotes. A [12,34,...] array from
  // solana-keygen also works.
  //
  // It signs transactions on the phone and is never sent anywhere. Even so,
  // use a wallet you keep only for this, funded with what you are willing to
  // have at risk.
  SOLANA_PRIVATE_KEY: "",

  // -- 3. Jupiter. Optional. -------------------------------------------------
  // Swaps back to SOL (close and swap, claim and swap, cleanup) go through
  // Jupiter. Without a key they use its free public API, which is enough for
  // a phone. A key from portal.jup.ag raises the rate limit.
  JUPITER_API_KEY: "",

  // -- 4. Practice mode. -----------------------------------------------------
  // true  = work out every transaction and simulate it, send nothing.
  // false = really send.
  //
  // Leave this true until you have watched one practice run of each button
  // and the numbers look like what you expected.
  PRACTICE_MODE: true,

  // -- 5. Zap presets, in SOL. -----------------------------------------------
  // The buttons MET Pool shows on every pool. Up to three; a Custom button is
  // always there too.
  //
  //   amount    SOL to deposit
  //   slices    how many positions to split it into (1-20). Each one holds up
  //             to 70 bins and locks about 0.042 SOL of rent until you close
  //             it, so more slices means more SOL parked, not more earned.
  //   top       where the ladder starts, in % below the price. 0 starts at
  //             the price itself.
  //   range     how far below the price it reaches, in % (1-99).
  //   strategy  "spot", "curve" or "bidask": the shape offered first. You can
  //             still pick another one on the confirm screen.
  MET_ZAP_PRESETS: [
    { amount: 0.5, slices: 1, top: 0, range: 50, strategy: "bidask" },
    { amount: 1, slices: 3, top: 0, range: 70, strategy: "spot" },
  ],

  // -- 6. Worst price you accept when swapping to SOL, in %. ------------------
  // Measured against the pool price, all in. A swap that would come back
  // further below it is refused and the tokens stay in your wallet.
  MAX_LOSS_PERCENT: 5,

  // -- 7. Show money in: "SOL", "USD" or "IDR". -------------------------------
  // Optional: the easy way is the "Show money in" row at the top of MET
  // Portfolio, and whatever you tap there wins over this line.
  // Everything is still counted in SOL. This only changes how net worth,
  // values, fees and PnL are shown in MET Portfolio (and the wallet line in
  // MET Pool). Deposits, rent and every confirm screen stay in SOL, because
  // that is what is sent. IDR uses a USD/IDR rate refreshed every 6 hours.
  DISPLAY_CURRENCY: "SOL",

  // -- 8. Spec v1 entry rules (MET Pool). Optional. --------------------------
  // MET Pool shows under every pool whether it passes the spec's gates, and
  // by default lists only those that do (tap the filter row for all).
  // Your bankroll in SOL sets the loss-budget cap: max position =
  // BANKROLL_SOL x LOSS_BUDGET_PCT% / 0.9. Leave 0 for no cap check.
  BANKROLL_SOL: 0,
  LOSS_BUDGET_PCT: 5,
  // Gates and flags keep the spec's numbers unless you set them here:
  // SPEC_BIN_STEP_MIN 20, SPEC_BIN_STEP_MAX 250, SPEC_MIN_POOL_AGE_MIN 60,
  // SPEC_MIN_FEE_APR_PCT 1000, SPEC_MAX_PCT_OF_TVL 3, SPEC_FLAG_PUMP_1H_PCT 25,
  // SPEC_FLAG_DROP_6H_PCT -17, SPEC_FLAG_MIN_TVL_USD 25000,
  // SPEC_FLAG_NIGHT_FROM 0, SPEC_FLAG_NIGHT_TO 9, SPEC_UTC_OFFSET 7,
  // SPEC_RANGE_BOTTOM_PCT 70.

  // -- 9. Spec v1 exit rules (MET Portfolio). Optional. ----------------------
  // "alert": a notification when a rule fires; tap it to close and swap.
  // "auto":  opening MET Portfolio closes and swaps by itself. The widget
  //          never sends anything, in either mode.
  EXIT_MODE: "alert",
  // Alert when a position's PnL falls to this % (null = off).
  EXIT_ALERT_PNL_PCT: -10,
  // Close a SOL-only position that has sat below its range this long.
  // Checked when the widget or app refreshes, so in practice: two refreshes
  // at least this far apart. null = off.
  EXIT_CLOSE_BELOW_RANGE_MIN: 10,
  // Off on purpose: in the spec's replay every fixed take-profit and
  // stop-loss lost SOL against holding. Set a number to turn one on.
  EXIT_TAKE_PROFIT_PCT: null,
  EXIT_STOP_LOSS_PCT: null,
};
