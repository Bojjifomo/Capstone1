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
import { sellToSol } from "./swap.mjs";

const require = createRequire(import.meta.url);
const DLMMmod = require("@meteora-ag/dlmm");
const { PublicKey, VersionedTransaction } = require("@solana/web3.js");
const DLMM = DLMMmod.default ?? DLMMmod;
const here = path.dirname(fileURLToPath(import.meta.url));
const WSOL = "So11111111111111111111111111111111111111112";
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

if (dry) {
  const held = view.reduce((a, v) => a + v.tokenRaw, 0);
  let sale = null;
  if (held > 0) {
    const { dec } = await tokenBalance();
    // `held` is in lamports of the mint's decimals only when the SDK reports raw amounts (it does)
    sale = { tokenRaw: held, ...(await sellToSol(tokenMint, String(Math.floor(held)), { connection, owner, maxLossPct: maxLoss, pool, dry: true })) };
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
// The token that came out. The RPC can answer with the balance from before the close for a few
// seconds, so wait until it moves (up to ~30 s) when the positions held some token; if it never
// shows, sell what the positions held, capped by what the wallet now holds.
const expected = BigInt(Math.floor(view.reduce((a, v) => a + (Number(v.tokenRaw) || 0), 0)));
let tokAfter = await tokenBalance(), gained = tokAfter.raw - tokBefore.raw, waited = 0;
while (expected > 0n && gained * 100n < expected * 95n && waited < 15) {
  await new Promise(r => setTimeout(r, 2000));
  waited++;
  tokAfter = await tokenBalance();
  gained = tokAfter.raw - tokBefore.raw;
}
let gainedNote = null;
if (gained <= 0n && expected > 0n && tokAfter.raw > 0n) {
  gained = tokAfter.raw < expected ? tokAfter.raw : expected;
  gainedNote = "balance did not move after the close; selling what the positions held";
}
let swap = null;
if (gained > 0n) {
  // Jupiter, else straight into this pool; never more than --maxloss % under the pool price.
  const res = await sellToSol(tokenMint, gained.toString(), { connection, signer, owner, maxLossPct: maxLoss, pool });
  if (!res.ok) {
    out({ ok: false, step: "swap", error: `positions closed, token NOT sold: ${res.error}. It stays in the wallet (Cleanup sells it later).`, closed, swap: res,
      leftover: { mint: tokenMint, pool, raw: gained.toString() } });
    process.exit(1);
  }
  swap = { signature: res.signature, via: res.via, tokenRaw: gained.toString(), sol: res.sol, underPct: res.underPct, note: gainedNote, waitedS: waited * 2 };
}
const solAfter = await connection.getBalance(owner);
const back = (solAfter - solBefore) / 1e9;                       // SOL + returned rent − fees
if (!reopen) { out({ ok: true, mode: "close-swap", closed, swap, solBack: back, tokenMint, expectedTokenRaw: expected.toString() }); process.exit(0); }

const rentBack = positions.length * 0.0419;
const ask = arg("sol") ? Number(arg("sol")) : Math.floor((back - rentBack) * 0.999 * 1000) / 1000;
if (!(ask >= 0.01)) { out({ ok: false, step: "reopen", error: `only ${back.toFixed(4)} SOL came back: too small to re-open`, closed, swap, solBack: back }); process.exit(1); }
const opened = child("open_ladder.mjs", ["--pool", pool, "--sol", String(ask), "--slices", arg("slices", "5"), "--bottom", arg("bottom", "70"),
  "--top", arg("top", "0"), "--strategy", arg("strategy", "spot")]);
out({ ok: !!opened?.ok, mode: "reenter", closed, swap, solBack: back, reopenSol: ask, opened, ...(opened?.ok ? {} : { step: "reopen", error: opened?.error || "re-open failed; the SOL is in the wallet" }) });
process.exit(opened?.ok ? 0 : 1);
