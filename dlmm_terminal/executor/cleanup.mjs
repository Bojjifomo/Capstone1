// Wallet cleanup: sell leftover tokens to SOL (Jupiter) and close empty token accounts (their rent comes back).
//
//   node cleanup.mjs [--dry-run --owner PK] [--maxloss 5] [--min-sol 0.005] [--no-sell] [--no-close]
//
// A token is sold only when Jupiter's route is worth at least --min-sol SOL and its price impact is at most
// --maxloss %. USDC, USDT and wSOL are never sold. One JSON line: {ok, mode, tokens:[...], emptyAccounts, ...}.
import { createRequire } from "node:module";
import { loadSigner } from "./keys.mjs";
import { tradeConnection, rpcStats } from "./rpc.mjs";

const require = createRequire(import.meta.url);
const { PublicKey, VersionedTransaction, Transaction, TransactionInstruction, sendAndConfirmTransaction } = require("@solana/web3.js");
const WSOL = "So11111111111111111111111111111111111111112";
const KEEP = new Set([WSOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"]);
const PROGRAMS = ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"];
const JUP = "https://lite-api.jup.ag/swap/v1";
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
  try {
    const q = await (await fetch(`${JUP}/quote?inputMint=${a.mint}&outputMint=${WSOL}&amount=${a.raw}&slippageBps=300`)).json();
    if (!q.outAmount) { t.status = "no route: left"; continue; }
    t.sol = Number(q.outAmount) / 1e9;
    t.impactPct = Number(q.priceImpactPct) * 100;
    if (a.frozen) { t.status = "frozen: left"; continue; }
    if (t.sol < minSol) { t.status = `dust (< ${minSol} SOL): left`; continue; }
    if (t.impactPct > maxLoss) { t.status = `price impact ${t.impactPct.toFixed(1)}% > ${maxLoss}%: left`; continue; }
    if (dry) { t.status = "would sell"; continue; }
    const sw = await (await fetch(`${JUP}/swap`, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ quoteResponse: q, userPublicKey: owner.toBase58(), wrapAndUnwrapSol: true, dynamicComputeUnitLimit: true,
        prioritizationFeeLamports: { priorityLevelWithMaxLamports: { maxLamports: 2_000_000, priorityLevel: "high" } } }) })).json();
    if (!sw.swapTransaction) { t.status = "swap build failed: left"; continue; }
    const tx = VersionedTransaction.deserialize(Buffer.from(sw.swapTransaction, "base64"));
    tx.sign([signer]);
    const sig = await connection.sendRawTransaction(tx.serialize(), { maxRetries: 3 });
    const c = await connection.confirmTransaction(sig, "confirmed");
    t.status = c.value.err ? "failed on chain: left" : "sold";
    t.signature = sig;
    if (!c.value.err && t.status === "sold") empties.push(a);   // the account is empty now
  } catch (e) { t.status = "error: " + String(e.message || e).slice(0, 80); }
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
