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
const out = o => { process.stdout.write(JSON.stringify({ ...o, rpc: rpcStats() }, (k, v) => (typeof v === "bigint" ? v.toString() : v)) + "\n"); };
const fail = (msg, extra) => { out({ ok: false, error: msg, ...(extra || {}) }); process.exit(1); };

import { planLadder } from "./ladder.mjs";
import { loadSigner } from "./keys.mjs";
import { tradeConnection, withReadConnection, rpcStats } from "./rpc.mjs";

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
// --plan is read-only and runs on every size change in the web modal: keep it off the keyed RPC
// (Helius free tier) unless the public node fails. Everything that sends uses the keyed RPC.

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

let connection, dlmm;
if (mode === "plan") {
  dlmm = await withReadConnection(c => { connection = c; return DLMM.create(c, new PublicKey(pool)); });
} else {
  connection = tradeConnection();
  dlmm = await DLMM.create(connection, new PublicKey(pool));
}
const lb = dlmm.lbPair;
const solIsX = lb.tokenXMint.toBase58() === WSOL;
if (!solIsX && lb.tokenYMint.toBase58() !== WSOL) fail("not a SOL pool");
const active = await dlmm.getActiveBin();
const lamports = BigInt(Math.round(sol * 1e9));
const plan = planLadder({ activeId: active.binId, binStep: lb.binStep, solIsX, lamports, slices, top, range: bottom, exact: !flag("split") });
const summary = {
  pool, binStep: lb.binStep, solIsX, activeId: active.binId, strategy: strategyName, sol, bottom, top,
  bins: plan.bins, raised: plan.raised, wide: plan.wide, realTop: plan.realTop, realEnd: plan.realEnd,
  slices: plan.slices.map(s => ({ lo: s.lo, hi: s.hi, width: s.width, sol: Number(s.lamports) / 1e9, nearPct: s.nearPct, farPct: s.farPct })),
};
if (mode === "plan") { out({ ok: true, mode, plan: summary }); process.exit(0); }

const balance = await connection.getBalance(owner);
const rentPerPosition = 0.0419e9; // a position account (8120 bytes), returned on close (bin arrays are not)
// A wider slice is one extended position: rent grows with its bins (about 0.0000x SOL per bin over 70).
const rentOf = s => s.width > 70 ? rentPerPosition + (s.width - 70) * 0.00060e9 : rentPerPosition;
const need = Number(lamports) + plan.slices.reduce((a, s) => a + rentOf(s), 0) + 0.02e9;
if (balance < need && mode !== "build") {
  // build mode lets the wallet itself refuse; the others stop here
  fail(`wallet holds ${(balance / 1e9).toFixed(3)} SOL, this needs about ${(need / 1e9).toFixed(3)} with rent and a 0.02 reserve`, { plan: summary });
}

const built = [];
for (const s of plan.slices) {
  const posKp = Keypair.generate();
  const params = {
    positionPubKey: posKp.publicKey, user: owner,
    totalXAmount: new BN(solIsX ? s.lamports.toString() : "0"),
    totalYAmount: new BN(solIsX ? "0" : s.lamports.toString()),
    strategy: { minBinId: s.lo, maxBinId: s.hi, strategyType, ...(solIsX ? { singleSidedX: true } : {}) },
    slippage: slippagePct,
  };
  let txs;
  if (s.width <= 70) txs = [await dlmm.initializePositionAndAddLiquidityByStrategy(params)];
  else {
    // wider than a standard position: create it extended, then fill it chunk by chunk
    const create = await dlmm.createExtendedEmptyPosition(s.lo, s.hi, posKp.publicKey, owner);
    txs = [create, ...(await dlmm.addLiquidityByStrategyChunkable(params))];
  }
  // A little priority so it lands on a busy minute.
  for (const tx of txs) tx.instructions.unshift(ComputeBudgetProgram.setComputeUnitPrice({ microLamports: 50_000 }));
  built.push({ slice: s, posKp, txs });
}

// The new position account signs only the transaction that creates it; the later chunks of an extended slice don't list it.
const needsPos = (tx, kp) => tx.compileMessage().accountKeys.slice(0, tx.compileMessage().header.numRequiredSignatures).some(k => k.equals(kp.publicKey));
const results = [];
for (const [i, b] of built.entries()) {
  const r = { slice: i + 1, position: b.posKp.publicKey.toBase58(), lo: b.slice.lo, hi: b.slice.hi, sol: Number(b.slice.lamports) / 1e9,
    txs: b.txs.length, ...(b.slice.width > 70 ? { extended: b.slice.width } : {}) };
  const sigs = [], raw = [];
  let failed = false;
  for (const [j, tx] of b.txs.entries()) {
    const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash("confirmed");
    tx.feePayer = owner;
    tx.recentBlockhash = blockhash;
    tx.lastValidBlockHeight = lastValidBlockHeight;
    if (mode === "dry-run" || mode === "build") {
      // later transactions of an extended slice need the position to exist: only the first can be simulated
      if (j === 0) {
        const sim = await connection.simulateTransaction(tx);
        r.simulate = sim.value.err ? { err: sim.value.err, logs: (sim.value.logs || []).slice(-6) } : "ok";
      }
      if (mode === "build") {
        if (needsPos(tx, b.posKp)) tx.partialSign(b.posKp);
        raw.push(tx.serialize({ requireAllSignatures: false, verifySignatures: false }).toString("base64"));
      }
    } else {
      try {
        sigs.push(await sendAndConfirmTransaction(connection, tx, needsPos(tx, b.posKp) ? [signer, b.posKp] : [signer], { commitment: "confirmed" }));
      } catch (e) {
        r.ok = false;
        r.error = String(e.message || e).slice(0, 300) + (b.txs.length > 1 ? ` (tx ${j + 1}/${b.txs.length}; position ${r.position} may exist: close it)` : "");
        failed = true;
        break;
      }
    }
  }
  if (mode === "build") { r.tx = raw[0]; r.txs = raw; }
  if (mode === "send") { r.signature = sigs[sigs.length - 1]; r.signatures = sigs; if (!failed) r.ok = true; }
  results.push(r);
  if (failed) break;                       // the rest stays as SOL in the wallet
}
const okAll = mode === "send" ? results.length === built.length && results.every(r => r.ok)
  : results.every(r => r.simulate === "ok");
out({ ok: okAll, mode, plan: summary, results });
process.exit(okAll ? 0 : 1);
