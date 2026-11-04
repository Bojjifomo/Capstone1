// The trading key, from the environment only (never from a request):
//   DLMM_PRIVATE_KEY  base58 string (Phantom/Solflare "export private key") or a [12,34,...] array
//   DLMM_KEYPAIR      path to a solana-keygen JSON file
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { Keypair } = require("@solana/web3.js");
const bs58m = require("bs58");
const bs58 = bs58m.default ?? bs58m;

export function loadSigner() {
  const raw = (process.env.DLMM_PRIVATE_KEY || "").trim();
  if (raw) {
    const bytes = raw.startsWith("[") ? Uint8Array.from(JSON.parse(raw)) : bs58.decode(raw);
    if (bytes.length !== 64) throw new Error(`DLMM_PRIVATE_KEY decodes to ${bytes.length} bytes, expected 64`);
    return Keypair.fromSecretKey(bytes);
  }
  const path = process.env.DLMM_KEYPAIR;
  if (path) return Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(path, "utf8"))));
  return null;
}
