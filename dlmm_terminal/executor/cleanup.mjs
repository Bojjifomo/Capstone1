// Wallet cleanup: sell leftover tokens to SOL (Jupiter) and close empty token accounts (their rent comes back).
//
//   node cleanup.mjs [--dry-run --owner PK] [--maxloss 5] [--min-sol 0.005] [--no-sell] [--no-close]
//
// A token is sold (Jupiter, else its Meteora DLMM pool) only when it is worth at least --min-sol SOL and
// the sale pays at most --maxloss % under the pool price (Jupiter's price impact when no pool is known). USDC, USDT and wSOL are never sold. One JSON line: {ok, mode, tokens:[...], emptyAccounts, ...}.
import { createRequire } from "node:module";
import { loadSigner } from "./keys.mjs";
import { tradeConnection, rpcStats } from "./rpc.mjs";
import { sellToSol, jupQuote } from "./swap.mjs";

const require = createRequire(import.meta.url);
const { PublicKey, Transaction, TransactionInstruction, sendAndConfirmTransaction } = require("@solana/web3.js");
const WSOL = "So11111111111111111111111111111111111111112";
const KEEP = new Set([WSOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"]);
const PROGRAMS = ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"];
const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
const flag = n => process.argv.includes(`--${n}`);
const out = o => process.stdout.write(JSON.stringify({ ...o, rpc: rpcStats() }) + "\n");
const fail = (error, extra) => { out({ ok: false, error, ...(extra || {}) }); process.exit(1); };

const dry = flag("dry-run"), maxLoss = Number(arg("maxloss", "5")), minSol = Number(arg("min-sol", "0.005"));
let signer = null, owner;
if (dry) { if (!arg("owner")) fail("--owner PUBKEY is needed for --dry-run"); owner = new PublicKey(arg("owner")); }
else { try { signer = loadSigner(); } catch (e) { fail("trading key: " + e.message); } if (!signer) fail("no trading key: set DLMM_PRIVATE_KEY in .env"); owner = signer.publicKey; }
const connection = tradeConnection();

const accounts = [];
for (const programId of PROGRAMS) {
  const r = await connection.getParsedTokenAccountsByOwner(owner, { programId: new PublicKey(programId) });
  for (const a of r.value) {
    const i = a.account.data.parsed.info;
    accounts.push({ address: a.pubkey, mint: i.mint, raw: BigInt(i.tokenAmount.amount), ui: i.tokenAmount.uiAmount, program: programId,
      frozen: i.state === "frozen", lamports: a.account.lamports });
  }
}

const tokens = [], empties = [];
for (const a of accounts) {
  if (a.raw === 0n) { if (!a.frozen) empties.push(a); continue; }
  if (KEEP.has(a.mint) || flag("no-sell")) continue;
  const t = { mint: a.mint, amount: a.ui, status: "" };
  tokens.push(t);
  if (a.frozen) { t.status = "frozen: left"; continue; }
  // dust first, from one Jupiter quote (most leftovers are dust: no pool lookups for them)
  try {
    const q = await jupQuote(a.mint, a.raw.toString());
    if (Number(q.outAmount) / 1e9 < minSol) { t.sol = Number(q.outAmount) / 1e9; t.status = `dust (< ${minSol} SOL): left`; continue; }
  } catch {}
  // what it is worth and where it would sell (no send)
  const est = await sellToSol(a.mint, a.raw.toString(), { connection, owner, maxLossPct: maxLoss, dry: true });
  if (!est.ok) { t.status = "not sold: " + est.error; continue; }
  t.sol = est.sol;
  t.via = est.via;
  t.underPct = est.underPct;
  if (est.sol < minSol) { t.status = `dust (< ${minSol} SOL): left`; continue; }
  if (dry) { t.status = `would sell via ${est.via}`; continue; }
  const res = await sellToSol(a.mint, a.raw.toString(), { connection, signer, owner, maxLossPct: maxLoss, pool: est.pool });
  if (res.ok) { t.status = `sold via ${res.via}`; t.signature = res.signature; t.sol = res.sol; empties.push(a); }
  else t.status = "not sold: " + res.error;
}

// closeAccount (instruction 9): account, destination, owner
const rentBack = empties.reduce((s, a) => s + a.lamports, 0) / 1e9;
let closed = 0;
if (!flag("no-close") && !dry) {
  for (let i = 0; i < empties.length; i += 8) {
    const tx = new Transaction();
    for (const a of empties.slice(i, i + 8)) {
      tx.add(new TransactionInstruction({ programId: new PublicKey(a.program), data: Buffer.from([9]),
        keys: [{ pubkey: a.address, isSigner: false, isWritable: true }, { pubkey: owner, isSigner: false, isWritable: true }, { pubkey: owner, isSigner: true, isWritable: false }] }));
    }
    try { await sendAndConfirmTransaction(connection, tx, [signer], { commitment: "confirmed" }); closed += Math.min(8, empties.length - i); }
    catch (e) { out({ ok: false, error: "close accounts: " + String(e.message || e).slice(0, 200), tokens, closed }); process.exit(1); }
  }
}
out({ ok: true, mode: dry ? "dry-run" : "send", tokens, emptyAccounts: empties.length, rentSol: rentBack, closed });
