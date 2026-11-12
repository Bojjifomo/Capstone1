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
// --retry-slices '[{"lo":-929,"hi":-728,"lamports":"500000000"}, ...]': open exactly these slices (the ones an earlier run did not
// manage), at their old bin ranges, if the price is still above them. Without it the plan comes from the current price.
let retrySlices = null;
if (arg("retry-slices")) {
  try { retrySlices = JSON.parse(arg("retry-slices")).map(x => ({ lo: Number(x.lo), hi: Number(x.hi), width: Number(x.hi) - Number(x.lo) + 1, lamports: BigInt(x.lamports) })); }
  catch (e) { fail("--retry-slices is not valid JSON: " + e.message); }
}
if (!pool || !(sol > 0 || arg("retry-slices")) || !(slices >= 1 && slices <= 20) || !(bottom > top && bottom < 100) || strategyType == null) {
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
const lamports = retrySlices ? retrySlices.reduce((a, x) => a + x.lamports, 0n) : BigInt(Math.round(sol * 1e9));
const plan = retrySlices
  ? { slices: retrySlices.map((x, i) => ({ ...x, nearPct: 0, farPct: 0 })), bins: retrySlices.reduce((a, x) => a + x.width, 0), raised: 0, wide: retrySlices.filter(x => x.width > 70).length, realTop: 0, realEnd: 0 }
  : planLadder({ activeId: active.binId, binStep: lb.binStep, solIsX, lamports, slices, top, range: bottom, exact: !flag("split") });
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

// ---- build one slice's transactions from the pool's CURRENT state -------------------------------------------
async function buildSlice(s, posKp) {
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
  return txs;
}
// The new position account signs only the transaction that creates it; the later chunks of an extended slice don't list it.
const needsPos = (tx, kp) => tx.compileMessage().accountKeys.slice(0, tx.compileMessage().header.numRequiredSignatures).some(k => k.equals(kp.publicKey));
const sleep = ms => new Promise(r => setTimeout(r, ms));
// Failures that say nothing about the ladder (network, an expired blockhash, an RPC that is behind): just try again.
const TRANSIENT = /blockhash not found|block height exceeded|expired|timed? ?out|429|too many requests|fetch failed|ECONN|ETIMEDOUT|socket hang up|not confirmed|node is behind|503|502|504|service unavailable|rate limit/i;
async function sendWithRetry(tx, signersFor) {
  let last;
  for (let n = 0; n < 4; n++) {
    try {
      const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash("confirmed");
      tx.feePayer = owner; tx.recentBlockhash = blockhash; tx.lastValidBlockHeight = lastValidBlockHeight;
      tx.signatures = [];                                   // signed again with the new blockhash
      return await sendAndConfirmTransaction(connection, tx, typeof signersFor === "function" ? signersFor(tx) : signersFor, { commitment: "confirmed" });
    } catch (e) {
      last = e;
      if (!TRANSIENT.test(String(e.message || e))) throw e;
      await sleep(1500 * (n + 1));
    }
  }
  throw last;
}
// Is the whole slice still on SOL's side of the price? (SOL-only below the price when SOL is Y, above it when SOL is X.)
// The active bin itself may hold SOL only (the top slice starts there), so the comparison is not strict.
const sliceOk = (s, activeId) => (solIsX ? s.lo >= activeId : s.hi <= activeId);
const errText = e => String((e && e.message) || e).replace(/\s+/g, " ").slice(0, 600);

if (mode === "dry-run" || mode === "build") {
  const results = [];
  for (const [i, s] of plan.slices.entries()) {
    const posKp = Keypair.generate();
    const txs = await buildSlice(s, posKp);
    const r = { slice: i + 1, position: posKp.publicKey.toBase58(), lo: s.lo, hi: s.hi, sol: Number(s.lamports) / 1e9,
      txs: txs.length, ...(s.width > 70 ? { extended: s.width } : {}) };
    const raw = [];
    for (const [j, tx] of txs.entries()) {
      const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash("confirmed");
      tx.feePayer = owner; tx.recentBlockhash = blockhash; tx.lastValidBlockHeight = lastValidBlockHeight;
      // later transactions of an extended slice need the position to exist: only the first can be simulated
      if (j === 0) {
        const sim = await connection.simulateTransaction(tx);
        r.simulate = sim.value.err ? { err: sim.value.err, logs: (sim.value.logs || []).slice(-6) } : "ok";
      }
      if (mode === "build") {
        if (needsPos(tx, posKp)) tx.partialSign(posKp);
        raw.push(tx.serialize({ requireAllSignatures: false, verifySignatures: false }).toString("base64"));
      }
    }
    if (mode === "build") { r.tx = raw[0]; r.txs = raw; }
    results.push(r);
  }
  const okAll = results.every(r => r.simulate === "ok");
  out({ ok: okAll, mode, plan: summary, results });
  process.exit(okAll ? 0 : 1);
}

// ---- send: slice by slice, each built right before it is sent --------------------------------------------------
// Building every slice up front (as this did) leaves the later slices with a price and bin-array picture that is
// minutes old; on a fast token the program then refuses them (bin slippage) and the ladder stops after the first
// slice. Now every slice is built from fresh state, a failed transaction is retried, a failed slice does not
// block the next one, and what did not open is reported with everything needed to open it again.
const results = [], failedSlices = [];
let strikes = 0;
for (const [i, s] of plan.slices.entries()) {
  const r = { slice: i + 1, lo: s.lo, hi: s.hi, sol: Number(s.lamports) / 1e9, ...(s.width > 70 ? { extended: s.width } : {}) };
  results.push(r);
  let done = false, lastErr = null;
  for (let attempt = 1; attempt <= 3 && !done; attempt++) {
    const posKp = Keypair.generate();
    let created = false, sigs = [], txs = [];
    try {
      await dlmm.refetchStates();
      const act = await dlmm.getActiveBin();
      if (!sliceOk(s, act.binId)) throw Object.assign(new Error(`price moved: the active bin is now ${act.binId}, this slice (${s.lo}..${s.hi}) is no longer on SOL's side`), { final: true });
      txs = await buildSlice(s, posKp);
      r.position = posKp.publicKey.toBase58(); r.txs = txs.length;
      for (let j = 0; j < txs.length; j++) {
        let tx = txs[j];
        for (let tryJ = 0; ; tryJ++) {
          try {
            sigs.push(await sendWithRetry(tx, t => (needsPos(t, posKp) ? [signer, posKp] : [signer])));
            if (j === 0) created = true;
            break;
          } catch (e) {
            // A chunk of an extended slice failed: the position exists, so rebuild the chunk list from fresh state and
            // send chunk j again (amounts per bin do not depend on the price while the range is on SOL's side).
            if (j === 0 || tryJ >= 2) throw e;
            await sleep(2000);
            await dlmm.refetchStates();
            const act2 = await dlmm.getActiveBin();
            if (!sliceOk(s, act2.binId)) throw Object.assign(e, { final: true });
            const fresh = await dlmm.addLiquidityByStrategyChunkable({
              positionPubKey: posKp.publicKey, user: owner,
              totalXAmount: new BN(solIsX ? s.lamports.toString() : "0"), totalYAmount: new BN(solIsX ? "0" : s.lamports.toString()),
              strategy: { minBinId: s.lo, maxBinId: s.hi, strategyType, ...(solIsX ? { singleSidedX: true } : {}) }, slippage: slippagePct });
            if (fresh.length !== txs.length - 1) throw e;           // the chunking changed: do not guess
            tx = fresh[j - 1];
            tx.instructions.unshift(ComputeBudgetProgram.setComputeUnitPrice({ microLamports: 50_000 }));
          }
        }
      }
      r.signature = sigs[sigs.length - 1]; r.signatures = sigs; r.ok = true; r.attempts = attempt;
      done = true;
    } catch (e) {
      lastErr = e;
      r.error = errText(e);
      const funded = sigs.length > 1;                     // more than the create / the single transaction landed
      if (created && txs.length > 1) {
        if (funded) { r.partial = true; r.position = posKp.publicKey.toBase58(); break; }   // some liquidity is in: keep it, report it
        // created but empty: close it so its rent comes back, then try the slice again from scratch
        try {
          const cl = await dlmm.closePosition({ owner, position: await dlmm.getPosition(posKp.publicKey) });
          await sendWithRetry(cl, [signer]);
        } catch (ce) { r.orphan = posKp.publicKey.toBase58(); r.orphanError = errText(ce); break; }
      } else if (created) { r.orphan = posKp.publicKey.toBase58(); break; }
      if (e.final) break;
      await sleep(2500 * attempt);
    }
  }
  if (!done) {
    r.ok = false;
    r.error = (r.error || errText(lastErr)) + (r.partial ? ` (only part of it is in: position ${r.position})` : r.orphan ? ` (empty position ${r.orphan} left: close it)` : "");
    if (!r.partial) failedSlices.push({ slice: i + 1, lo: s.lo, hi: s.hi, lamports: s.lamports.toString(), width: s.width, error: r.error });
    strikes++;
    if (strikes >= 2 && i < plan.slices.length - 1) {                 // two in a row: something is wrong, stop here
      r.note = "stopped after two slices in a row failed";
      break;
    }
  } else strikes = 0;
}
// slices never tried (stopped early) count as failed too, so they can be opened again
for (let i = results.length; i < plan.slices.length; i++) {
  const s = plan.slices[i];
  failedSlices.push({ slice: i + 1, lo: s.lo, hi: s.hi, lamports: s.lamports.toString(), width: s.width, error: "not tried (an earlier slice kept failing)" });
}
const okAll = results.length === plan.slices.length && results.every(r => r.ok);
out({ ok: okAll, mode, plan: summary, results, ...(failedSlices.length ? { failedSlices } : {}) });
process.exit(okAll ? 0 : 1);
