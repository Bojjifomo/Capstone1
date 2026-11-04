// Close one Meteora DLMM position: remove 100% liquidity, claim fees, close the account.
//
//   node close_position.mjs --pool <POOL> --position <POSITION> [--dry-run] [--owner <PUBKEY>]
//
// env: SOLANA_RPC_URL (required), DLMM_KEYPAIR = path to a Solana keypair JSON (required unless --dry-run).
// --dry-run builds the transactions and simulates them without signing or sending.
// Leftover memecoin from the range is NOT swapped to SOL here; sell it yourself (spec: leftovers cost -3.93 SOL).
import fs from "node:fs";
import { createRequire } from "node:module";

// The SDK's ESM build has a broken directory import; its CommonJS build loads fine.
const require = createRequire(import.meta.url);
const DLMMmod = require("@meteora-ag/dlmm");
const { Connection, Keypair, PublicKey, sendAndConfirmTransaction } = require("@solana/web3.js");
const BN = require("bn.js");
const DLMM = DLMMmod.default ?? DLMMmod;

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : undefined;
}

const pool = arg("pool");
const positionAddr = arg("position");
const dryRun = process.argv.includes("--dry-run");
const rpc = process.env.SOLANA_RPC_URL;
if (!pool || !positionAddr || !rpc) {
  console.error("usage: SOLANA_RPC_URL=... node close_position.mjs --pool P --position X [--dry-run] [--owner PUBKEY]");
  process.exit(2);
}

const keyPath = process.env.DLMM_KEYPAIR;
const signer = keyPath ? Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(keyPath, "utf8")))) : null;
const owner = signer ? signer.publicKey : arg("owner") ? new PublicKey(arg("owner")) : null;
if (!owner) {
  console.error("need DLMM_KEYPAIR (or --owner with --dry-run)");
  process.exit(2);
}
if (!dryRun && !signer) {
  console.error("DLMM_KEYPAIR is required to send");
  process.exit(2);
}

const connection = new Connection(rpc, "confirmed");
const dlmm = await DLMM.create(connection, new PublicKey(pool));
const position = await dlmm.getPosition(new PublicKey(positionAddr));
if (!position.publicKey) throw new Error("position not found");
const { lowerBinId, upperBinId } = position.positionData;

const txs = await dlmm.removeLiquidity({
  user: owner,
  position: new PublicKey(positionAddr),
  fromBinId: lowerBinId,
  toBinId: upperBinId,
  bps: new BN(10_000),
  shouldClaimAndClose: true,
});

console.log(`position ${positionAddr} bins ${lowerBinId}..${upperBinId}: ${txs.length} tx(s)`);
for (const [i, tx] of txs.entries()) {
  if (dryRun) {
    tx.feePayer = owner;
    tx.recentBlockhash = (await connection.getLatestBlockhash()).blockhash;
    const sim = await connection.simulateTransaction(tx);
    const err = sim.value.err;
    console.log(`  tx ${i + 1}: simulate ${err ? "ERROR " + JSON.stringify(err) : "ok"} (${tx.instructions.length} ix)`);
    if (err) process.exitCode = 1;
  } else {
    const sig = await sendAndConfirmTransaction(connection, tx, [signer], { commitment: "confirmed" });
    console.log(`  tx ${i + 1}: ${sig}`);
  }
}
