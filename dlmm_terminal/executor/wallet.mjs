// Prints the trading wallet's address (never the key): {"ok":true,"pubkey":"..."}
import { loadSigner } from "./keys.mjs";
try {
  const s = loadSigner();
  process.stdout.write(JSON.stringify(s ? { ok: true, pubkey: s.publicKey.toBase58() } : { ok: false, error: "no DLMM_PRIVATE_KEY / DLMM_KEYPAIR" }) + "\n");
} catch (e) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e.message || e).slice(0, 200) }) + "\n");
}
