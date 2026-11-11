// Sell a token the wallet holds for SOL (Jupiter, else its Meteora DLMM pool), with the max-loss guard.
// Used by the auto exit to retry a sale that failed right after the close.
//
//   node sell.mjs --mint M [--pool P] [--maxloss 5] [--amount RAW]     (default amount: everything held)
//   node sell.mjs --mint M --dry-run --owner PK
// One JSON line: {ok, mint, raw, via, sol, signature} or {ok:false, error}.
import { createRequire } from "node:module";
import { loadSigner } from "./keys.mjs";
import { tradeConnection, rpcStats } from "./rpc.mjs";
import { sellToSol } from "./swap.mjs";

const require = createRequire(import.meta.url);
const { PublicKey } = require("@solana/web3.js");
const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
const out = o => { process.stdout.write(JSON.stringify({ ...o, rpc: rpcStats() }) + "\n"); process.exit(o.ok ? 0 : 1); };

const mint = arg("mint"), dry = process.argv.includes("--dry-run");
if (!mint) out({ ok: false, error: "usage: --mint M [--pool P] [--maxloss 5] [--amount RAW]" });
let signer = null, owner;
if (dry) owner = new PublicKey(arg("owner"));
else { try { signer = loadSigner(); } catch (e) { out({ ok: false, error: "trading key: " + e.message }); } if (!signer) out({ ok: false, error: "no trading key" }); owner = signer.publicKey; }
const connection = tradeConnection();

let held = 0n;
for (const programId of ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"]) {
  const r = await connection.getParsedTokenAccountsByOwner(owner, { mint: new PublicKey(mint), programId: new PublicKey(programId) });
  for (const a of r.value) held += BigInt(a.account.data.parsed.info.tokenAmount.amount);
}
const want = arg("amount") ? BigInt(arg("amount")) : held;
const raw = want < held ? want : held;
if (raw <= 0n) out({ ok: true, mint, raw: "0", note: "nothing held: already sold or moved" });
const res = await sellToSol(mint, raw.toString(), { connection, signer, owner, maxLossPct: Number(arg("maxloss", "5")), pool: arg("pool") || null, dry });
out({ mint, raw: raw.toString(), ...res });
