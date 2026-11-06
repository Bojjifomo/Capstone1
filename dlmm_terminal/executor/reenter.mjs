// Close ladder positions of one pool, sell the token that came out back to SOL, and (re-entry) open the
// same ladder again at the current price. Works whether the price ran away above the ladder (all SOL)
// or has come into it (SOL + token): the token is simply whatever the wallet got on top of the SOL.
//
//   node reenter.mjs --pool P --positions A,B,C [--reopen --slices 5 --bottom 70 --top 0 --strategy spot]
//        [--sol N]            re-open with N SOL instead of everything that came back
//        [--maxloss 5]        refuse to sell the token if the swap pays more than this % under the pool price
//        [--dry-run --owner PK]   read positions, quote the sale, print the plan; sends nothing
//
// Without --reopen it is "close and swap to SOL". One JSON line on stdout. Needs the trading key (not --dry-run).
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { loadSigner } from "./keys.mjs";
import { tradeConnection, rpcStats } from "./rpc.mjs";

const require = createRequire(import.meta.url);
const DLMMmod = require("@meteora-ag/dlmm");
const { PublicKey, VersionedTransaction } = require("@solana/web3.js");
const DLMM = DLMMmod.default ?? DLMMmod;
const here = path.dirname(fileURLToPath(import.meta.url));
const WSOL = "So11111111111111111111111111111111111111112";
const JUP = "https://lite-api.jup.ag/swap/v1";
const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
const flag = n => process.argv.includes(`--${n}`);
const out = o => process.stdout.write(JSON.stringify({ ...o, rpc: rpcStats() }, (k, v) => (typeof v === "bigint" ? v.toString() : v)) + "\n");
const fail = (error, extra) => { out({ ok: false, error, ...(extra || {}) }); process.exit(1); };
const lastJson = s => { const l = s.trim().split("\n").reverse().find(x => x.startsWith("{")); return l ? JSON.parse(l) : null; };
function child(script, args) {
  try { return lastJson(execFileSync("node", [path.join(here, script), ...args], { encoding: "utf8", maxBuffer: 1e8, env: process.env })); }
  catch (e) { return lastJson(String(e.stdout || "")) || { ok: false, error: String(e.stderr || e.message).slice(-300) }; }
}

const pool = arg("pool"), positions = (arg("positions") || "").split(",").filter(Boolean);
const reopen = flag("reopen"), dry = flag("dry-run"), maxLoss = Number(arg("maxloss", "5"));
if (!pool || !positions.length) fail("usage: --pool P --positions A,B [--reopen --slices 5 --bottom 70] [--maxloss 5] [--dry-run --owner PK]");
let signer = null, owner;
if (dry) { if (!arg("owner")) fail("--owner PUBKEY is needed for --dry-run"); owner = new PublicKey(arg("owner")); }
else { try { signer = loadSigner(); } catch (e) { fail("trading key: " + e.message); } if (!signer) fail("no trading key: set DLMM_PRIVATE_KEY in .env"); owner = signer.publicKey; }

const connection = tradeConnection();
const dlmm = await DLMM.create(connection, new PublicKey(pool));
const lb = dlmm.lbPair;
const solIsX = lb.tokenXMint.toBase58() === WSOL;
if (!solIsX && lb.tokenYMint.toBase58() !== WSOL) fail("not a SOL pool");
const tokenMint = (solIsX ? lb.tokenYMint : lb.tokenXMint).toBase58();
const active = await dlmm.getActiveBin();

// where each position stands now
const view = [];
for (const p of positions) {
  const pos = await dlmm.getPosition(new PublicKey(p));
  if (!pos.publicKey) fail("position not found: " + p);
  const d = pos.positionData;
  const tok = Number(solIsX ? d.totalYAmount : d.totalXAmount), solAmt = Number(solIsX ? d.totalXAmount : d.totalYAmount);
  view.push({ position: p, lo: d.lowerBinId, hi: d.upperBinId, inRange: d.lowerBinId <= active.binId && active.binId <= d.upperBinId,
    solLamports: solAmt, tokenRaw: tok, holdsToken: tok > 0 });
}

const tokenBalance = async () => {
  let raw = 0n, dec = 0;
  for (const programId of [new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"), new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")]) {
    const r = await connection.getParsedTokenAccountsByOwner(owner, { mint: new PublicKey(tokenMint), programId });
    for (const a of r.value) { const t = a.account.data.parsed.info.tokenAmount; raw += BigInt(t.amount); dec = t.decimals; }
  }
  return { raw, dec };
};

// SOL per whole token at the pool's active bin (pricePerToken is Y per X)
const poolPriceSol = () => { const p = Number(active.pricePerToken); return solIsX ? 1 / p : p; };

async function quote(amountRaw) {
  const q = await (await fetch(`${JUP}/quote?inputMint=${tokenMint}&outputMint=${WSOL}&amount=${amountRaw}&slippageBps=300`)).json();
  if (!q.outAmount) throw new Error("no Jupiter route: " + JSON.stringify(q).slice(0, 120));
  return q;
}
function guard(q, amountRaw, dec) {
  const fair = Number(amountRaw) / 10 ** dec * poolPriceSol();       // SOL at the pool price
  const got = Number(q.outAmount) / 1e9;
  const underPct = fair > 0 ? (1 - got / fair) * 100 : 0;
  return { fairSol: fair, quoteSol: got, underPct, ok: underPct <= maxLoss };
}

if (dry) {
  const held = view.reduce((a, v) => a + v.tokenRaw, 0);
  let sale = null;
  if (held > 0) {
    const { dec } = await tokenBalance();
    // `held` is in lamports of the mint's decimals only when the SDK reports raw amounts (it does)
    try { const q = await quote(Math.floor(held)); sale = { tokenRaw: held, ...guard(q, held, dec || 6), priceImpactPct: q.priceImpactPct }; } catch (e) { sale = { error: String(e.message) }; }
  }
  out({ ok: true, mode: "dry-run", pool, activeId: active.binId, tokenMint, positions: view, sale, reopen });
  process.exit(0);
}

const solBefore = await connection.getBalance(owner);
const tokBefore = await tokenBalance();
const closed = [];
for (const p of positions) {
  const r = child("close_position.mjs", ["--pool", pool, "--position", p]);
  closed.push({ position: p, ok: !!r?.ok, error: r?.error });
  if (!r?.ok) { out({ ok: false, step: "close", error: `close ${p} failed: ${r?.error || "?"}`, closed }); process.exit(1); }
}
// token that came out
const tokAfter = await tokenBalance();
const gained = tokAfter.raw - tokBefore.raw;
let swap = null;
if (gained > 0n) {
  const q = await quote(gained.toString());
  const g = guard(q, gained, tokAfter.dec);
  if (!g.ok) {
    out({ ok: false, step: "swap", error: `swap refused: pays ${g.underPct.toFixed(1)}% under the pool price (max ${maxLoss}%). The token stays in the wallet; positions are closed.`, closed, swap: g });
    process.exit(1);
  }
  const sw = await (await fetch(`${JUP}/swap`, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ quoteResponse: q, userPublicKey: owner.toBase58(), wrapAndUnwrapSol: true, dynamicComputeUnitLimit: true,
      prioritizationFeeLamports: { priorityLevelWithMaxLamports: { maxLamports: 2_000_000, priorityLevel: "high" } } }) })).json();
  if (!sw.swapTransaction) { out({ ok: false, step: "swap", error: "Jupiter swap build failed: " + JSON.stringify(sw).slice(0, 160), closed }); process.exit(1); }
  const tx = VersionedTransaction.deserialize(Buffer.from(sw.swapTransaction, "base64"));
  tx.sign([signer]);
  const sig = await connection.sendRawTransaction(tx.serialize(), { skipPreflight: false, maxRetries: 3 });
  const conf = await connection.confirmTransaction(sig, "confirmed");
  if (conf.value.err) { out({ ok: false, step: "swap", error: "swap failed on chain: " + JSON.stringify(conf.value.err), signature: sig, closed }); process.exit(1); }
  swap = { signature: sig, tokenRaw: gained.toString(), ...g };
}
const solAfter = await connection.getBalance(owner);
const back = (solAfter - solBefore) / 1e9;                       // SOL + returned rent − fees
if (!reopen) { out({ ok: true, mode: "close-swap", closed, swap, solBack: back }); process.exit(0); }

const rentBack = positions.length * 0.0419;
const ask = arg("sol") ? Number(arg("sol")) : Math.floor((back - rentBack) * 0.999 * 1000) / 1000;
if (!(ask >= 0.01)) { out({ ok: false, step: "reopen", error: `only ${back.toFixed(4)} SOL came back: too small to re-open`, closed, swap, solBack: back }); process.exit(1); }
const opened = child("open_ladder.mjs", ["--pool", pool, "--sol", String(ask), "--slices", arg("slices", "5"), "--bottom", arg("bottom", "70"),
  "--top", arg("top", "0"), "--strategy", arg("strategy", "spot")]);
out({ ok: !!opened?.ok, mode: "reenter", closed, swap, solBack: back, reopenSol: ask, opened, ...(opened?.ok ? {} : { step: "reopen", error: opened?.error || "re-open failed; the SOL is in the wallet" }) });
process.exit(opened?.ok ? 0 : 1);
