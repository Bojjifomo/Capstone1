// Sell a token for SOL: Jupiter first, then straight into the token's Meteora DLMM SOL pool when
// Jupiter cannot be reached or has no route (rate limit, blocked network, a token it does not list yet).
// Both refuse a sale paying more than `maxLossPct` % under the fair price:
//   Jupiter: price impact it reports, and (when a pool is known) the pool's active price.
//   DLMM:    the pool's active price vs. what the swap quote returns.
// Used by reenter.mjs (Close+swap / Re-entry / auto exit) and cleanup.mjs.
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const DLMMmod = require("@meteora-ag/dlmm");
const { PublicKey, VersionedTransaction, ComputeBudgetProgram, sendAndConfirmTransaction } = require("@solana/web3.js");
const BN = require("bn.js");
const DLMM = DLMMmod.default ?? DLMMmod;

export const WSOL = "So11111111111111111111111111111111111111112";
const DATAPI = "https://dlmm.datapi.meteora.ag";
const jupBase = () => (process.env.JUPITER_API_KEY ? "https://api.jup.ag/swap/v1" : "https://lite-api.jup.ag/swap/v1");
const jupHeaders = extra => ({ ...(process.env.JUPITER_API_KEY ? { "x-api-key": process.env.JUPITER_API_KEY } : {}), ...(extra || {}) });
const sleep = ms => new Promise(r => setTimeout(r, ms));

// JSON with the real reason when it fails ("HTTP 429: Rate limit exceeded"), retrying 429 / 5xx / network errors.
async function getJson(url, init, tries = 3) {
  let last = "";
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, { ...(init || {}), signal: AbortSignal.timeout(15000) });
      const text = await r.text();
      let j = null;
      try { j = JSON.parse(text); } catch {}
      if (r.ok && j) return j;
      last = `HTTP ${r.status}: ${(j && (j.error || j.message || j.errorCode)) || text.slice(0, 120).replace(/\s+/g, " ")}`;
      if (r.status !== 429 && r.status < 500) break;
    } catch (e) {
      last = String(e.cause?.code || e.message || e).slice(0, 120);
    }
    await sleep(1500 * (i + 1));
  }
  throw new Error(last || "no answer");
}

export async function jupQuote(mint, rawAmount, slippageBps = 300) {
  const q = await getJson(`${jupBase()}/quote?inputMint=${mint}&outputMint=${WSOL}&amount=${rawAmount}&slippageBps=${slippageBps}`, { headers: jupHeaders() });
  if (!q.outAmount) throw new Error("Jupiter: no route" + (q.error ? ` (${q.error})` : ""));
  return q;
}

// The token's deepest Meteora DLMM pool against SOL (either side), or null.
export async function bestDlmmPool(mint) {
  let best = null;
  for (const f of [`token_x=${mint} && token_y=${WSOL}`, `token_x=${WSOL} && token_y=${mint}`]) {
    try {
      const d = await getJson(`${DATAPI}/pools?filter_by=${encodeURIComponent(f)}&sort_by=tvl:desc&page_size=1`, {}, 2);
      const p = (d.data || [])[0];
      if (p && (!best || (p.tvl || 0) > (best.tvl || 0))) best = p;
    } catch {}
  }
  return best ? best.address : null;
}

// What the pool says the amount is worth in SOL right now, and its quote for selling it there.
async function dlmmQuote(connection, pool, mint, rawAmount) {
  const dlmm = await DLMM.create(connection, new PublicKey(pool));
  const x = dlmm.lbPair.tokenXMint.toBase58(), y = dlmm.lbPair.tokenYMint.toBase58();
  if (![x, y].includes(mint) || ![x, y].includes(WSOL)) throw new Error("pool is not TOKEN/SOL");
  const swapForY = x === mint;                              // selling X for Y when the token is X
  const binArrays = await dlmm.getBinArrayForSwap(swapForY, 8);
  const q = dlmm.swapQuote(new BN(String(rawAmount)), swapForY, new BN(300), binArrays, true);
  // fair: the active bin's price (Y per X, in lamports per lamport) times the amount
  const active = await dlmm.getActiveBin();
  const pricePerLamport = Number(active.price);              // Y lamports per X lamport
  const fairLamports = swapForY ? Number(rawAmount) * pricePerLamport : Number(rawAmount) / pricePerLamport;
  return { dlmm, q, swapForY, fairLamports };
}

// Guard numbers in SOL: how much the sale pays under the pool price.
const under = (gotLamports, fairLamports) => (fairLamports > 0 ? (1 - gotLamports / fairLamports) * 100 : 0);

/**
 * Sell `rawAmount` of `mint` for SOL.
 * opts: {connection, signer (Keypair, null = dry run), owner (PublicKey), maxLossPct, pool (hint), dry}
 * Returns {ok, via: "jupiter"|"dlmm", sol, underPct, signature?, dryRun?, tried: [reasons]} or {ok:false, error, tried}.
 */
export async function sellToSol(mint, rawAmount, opts) {
  const { connection, signer, owner, maxLossPct = 5, dry } = opts;
  const tried = [];
  let pool = opts.pool || null;
  let fair = null;                                           // lamports at the pool price, when a pool is known
  // 1) Jupiter
  try {
    const q = await jupQuote(mint, rawAmount);
    const impact = Number(q.priceImpactPct || 0) * 100;
    if (!pool) pool = await bestDlmmPool(mint);
    if (pool) { try { fair = (await dlmmQuote(connection, pool, mint, rawAmount)).fairLamports; } catch {} }
    const u = fair ? under(Number(q.outAmount), fair) : impact;
    if (u > maxLossPct) throw new Error(`Jupiter pays ${u.toFixed(1)}% under the ${fair ? "pool price" : "market (price impact)"} (max ${maxLossPct}%)`);
    if (dry) return { ok: true, via: "jupiter", pool, sol: Number(q.outAmount) / 1e9, underPct: u, dryRun: true, tried };
    const sw = await getJson(`${jupBase()}/swap`, { method: "POST", headers: jupHeaders({ "content-type": "application/json" }),
      body: JSON.stringify({ quoteResponse: q, userPublicKey: owner.toBase58(), wrapAndUnwrapSol: true, dynamicComputeUnitLimit: true,
        prioritizationFeeLamports: { priorityLevelWithMaxLamports: { maxLamports: 2_000_000, priorityLevel: "high" } } }) }, 2);
    if (!sw.swapTransaction) throw new Error("Jupiter swap build failed");
    const tx = VersionedTransaction.deserialize(Buffer.from(sw.swapTransaction, "base64"));
    tx.sign([signer]);
    const sig = await connection.sendRawTransaction(tx.serialize(), { maxRetries: 3 });
    const c = await connection.confirmTransaction(sig, "confirmed");
    if (c.value.err) throw new Error("Jupiter swap failed on chain: " + JSON.stringify(c.value.err));
    return { ok: true, via: "jupiter", pool, sol: Number(q.outAmount) / 1e9, underPct: u, signature: sig, tried };
  } catch (e) {
    tried.push(String(e.message || e).slice(0, 160));
    if (/^Jupiter pays/.test(tried[tried.length - 1])) {
      // Jupiter's best route is already too far under: the pool alone will not do better.
      return { ok: false, error: tried.join(" · "), tried };
    }
  }
  // 2) the Meteora pool directly
  try {
    if (!pool) pool = await bestDlmmPool(mint);
    if (!pool) throw new Error("no Meteora DLMM SOL pool for this token");
    const { dlmm, q, fairLamports } = await dlmmQuote(connection, pool, mint, rawAmount);
    const got = Number(q.outAmount.toString()), u = under(got, fairLamports);
    if (q.consumedInAmount.lt(new BN(String(rawAmount)))) throw new Error("the pool cannot take the whole amount");
    if (u > maxLossPct) throw new Error(`the pool pays ${u.toFixed(1)}% under its price (max ${maxLossPct}%)`);
    if (dry) return { ok: true, via: "dlmm", pool, sol: got / 1e9, underPct: u, dryRun: true, tried };
    const tx = await dlmm.swap({ inToken: new PublicKey(mint), outToken: new PublicKey(WSOL), inAmount: new BN(String(rawAmount)),
      minOutAmount: q.minOutAmount, lbPair: dlmm.pubkey, user: owner, binArraysPubkey: q.binArraysPubkey });
    tx.instructions.unshift(ComputeBudgetProgram.setComputeUnitPrice({ microLamports: 50_000 }));
    const sig = await sendAndConfirmTransaction(connection, tx, [signer], { commitment: "confirmed" });
    return { ok: true, via: "dlmm", pool, sol: got / 1e9, underPct: u, signature: sig, tried };
  } catch (e) {
    tried.push("DLMM: " + String(e.message || e).slice(0, 160));
    return { ok: false, error: tried.join(" · "), tried };
  }
}
