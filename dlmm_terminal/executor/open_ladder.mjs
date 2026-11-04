// Open a SOL-only ladder on a Meteora DLMM pool, RH style: `slices` positions of equal
// width in bins, from the price (or `top`% below it) down to `bottom`% below it, holding
// 1:2:...:n of the SOL (slice 1, next to the price, the smallest; the deepest the
// biggest). Each slice is a Spot position (flat inside). Same plan as MET Pool's
// MET.planLadder, so the phone and the terminal open the same ladder.
//
//   node open_ladder.mjs --pool P --sol 8 --slices 5 --bottom 70 [--top 0] [--strategy spot]
//        --plan                 print the plan only (no RPC writes, no key)
//        --dry-run --owner PK   build + simulate every slice, send nothing
//        --build --owner PK     build, sign with the new position keys only, print base64
//                               transactions for a wallet (Phantom) to sign and send
//        (default)              sign with the trading key and send, one slice after another
//
// env: SOLANA_RPC_URL (required except --plan), DLMM_PRIVATE_KEY or DLMM_KEYPAIR (default mode only).
// Output is one JSON object on stdout.
import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const DLMMmod = require("@meteora-ag/dlmm");
const { Connection, Keypair, PublicKey, sendAndConfirmTransaction, ComputeBudgetProgram } = require("@solana/web3.js");
const BN = require("bn.js");
const DLMM = DLMMmod.default ?? DLMMmod;
const { StrategyType } = DLMMmod;

const WSOL = "So11111111111111111111111111111111111111112";
const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
const flag = n => process.argv.includes(`--${n}`);
const out = o => { process.stdout.write(JSON.stringify(o, (k, v) => (typeof v === "bigint" ? v.toString() : v)) + "\n"); };
const fail = (msg, extra) => { out({ ok: false, error: msg, ...(extra || {}) }); process.exit(1); };

import { planLadder } from "./ladder.mjs";
import { loadSigner } from "./keys.mjs";

// ---- main ----
const pool = arg("pool"), sol = Number(arg("sol")), slices = Number(arg("slices", "5"));
const bottom = Number(arg("bottom", "70")), top = Number(arg("top", "0"));
const strategyName = String(arg("strategy", "spot")).toLowerCase();
const strategyType = { spot: StrategyType.Spot, curve: StrategyType.Curve, bidask: StrategyType.BidAsk }[strategyName];
const slippagePct = Number(arg("slippage", "3"));
const mode = flag("plan") ? "plan" : flag("build") ? "build" : flag("dry-run") ? "dry-run" : "send";
if (!pool || !(sol > 0) || !(slices >= 1 && slices <= 20) || !(bottom > top && bottom < 100) || strategyType == null) {
  fail("usage: --pool P --sol N --slices 1-20 --bottom 1-99 [--top 0] [--strategy spot|curve|bidask] [--plan|--dry-run|--build] [--owner PK]");
}
const rpc = process.env.SOLANA_RPC_URL || "https://api.mainnet-beta.solana.com";
const connection = new Connection(rpc, "confirmed");

let signer = null, owner = null;
if (mode === "send") {
  try { signer = loadSigner(); } catch (e) { fail("trading key: " + e.message); }
  if (!signer) fail("no trading key: set DLMM_PRIVATE_KEY (or DLMM_KEYPAIR) in .env");
  owner = signer.publicKey;
} else if (arg("owner")) {
  owner = new PublicKey(arg("owner"));
} else if (mode !== "plan") {
  fail("--owner PUBKEY is needed for --dry-run / --build");
}

const dlmm = await DLMM.create(connection, new PublicKey(pool));
const lb = dlmm.lbPair;
const solIsX = lb.tokenXMint.toBase58() === WSOL;
if (!solIsX && lb.tokenYMint.toBase58() !== WSOL) fail("not a SOL pool");
const active = await dlmm.getActiveBin();
const lamports = BigInt(Math.round(sol * 1e9));
const plan = planLadder({ activeId: active.binId, binStep: lb.binStep, solIsX, lamports, slices, top, range: bottom });
const summary = {
  pool, binStep: lb.binStep, solIsX, activeId: active.binId, strategy: strategyName, sol, bottom, top,
  bins: plan.bins, raised: plan.raised, realTop: plan.realTop, realEnd: plan.realEnd,
  slices: plan.slices.map(s => ({ lo: s.lo, hi: s.hi, width: s.width, sol: Number(s.lamports) / 1e9, nearPct: s.nearPct, farPct: s.farPct })),
};
if (mode === "plan") { out({ ok: true, mode, plan: summary }); process.exit(0); }

const balance = await connection.getBalance(owner);
const rentPerPosition = 0.0419e9; // a position account (8120 bytes), returned on close (bin arrays are not)
const need = Number(lamports) + plan.slices.length * rentPerPosition + 0.02e9;
if (balance < need && mode !== "build") {
  // build mode lets the wallet itself refuse; the others stop here
  fail(`wallet holds ${(balance / 1e9).toFixed(3)} SOL, this needs about ${(need / 1e9).toFixed(3)} with rent and a 0.02 reserve`, { plan: summary });
}

const built = [];
for (const s of plan.slices) {
  const posKp = Keypair.generate();
  const tx = await dlmm.initializePositionAndAddLiquidityByStrategy({
    positionPubKey: posKp.publicKey, user: owner,
    totalXAmount: new BN(solIsX ? s.lamports.toString() : "0"),
    totalYAmount: new BN(solIsX ? "0" : s.lamports.toString()),
    strategy: { minBinId: s.lo, maxBinId: s.hi, strategyType, ...(solIsX ? { singleSidedX: true } : {}) },
    slippage: slippagePct,
  });
  // A little priority so it lands on a busy minute.
  tx.instructions.unshift(ComputeBudgetProgram.setComputeUnitPrice({ microLamports: 50_000 }));
  built.push({ slice: s, posKp, tx });
}

const results = [];
for (const [i, b] of built.entries()) {
  const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash("confirmed");
  b.tx.feePayer = owner;
  b.tx.recentBlockhash = blockhash;
  b.tx.lastValidBlockHeight = lastValidBlockHeight;
  const r = { slice: i + 1, position: b.posKp.publicKey.toBase58(), lo: b.slice.lo, hi: b.slice.hi, sol: Number(b.slice.lamports) / 1e9 };
  if (mode === "dry-run" || mode === "build") {
    const sim = await connection.simulateTransaction(b.tx);
    r.simulate = sim.value.err ? { err: sim.value.err, logs: (sim.value.logs || []).slice(-6) } : "ok";
  }
  if (mode === "build") {
    b.tx.partialSign(b.posKp);            // the new position account signs; the wallet signs the rest
    r.tx = b.tx.serialize({ requireAllSignatures: false, verifySignatures: false }).toString("base64");
  } else if (mode === "send") {
    try {
      r.signature = await sendAndConfirmTransaction(connection, b.tx, [signer, b.posKp], { commitment: "confirmed" });
      r.ok = true;
    } catch (e) {
      r.ok = false;
      r.error = String(e.message || e).slice(0, 300);
      results.push(r);
      break;                               // the rest stays as SOL in the wallet
    }
  }
  results.push(r);
}
const okAll = mode === "send" ? results.length === built.length && results.every(r => r.ok)
  : results.every(r => r.simulate === "ok");
out({ ok: okAll, mode, plan: summary, results });
process.exit(okAll ? 0 : 1);
