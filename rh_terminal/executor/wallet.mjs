// Prints the trading wallet's address (never the key): {"ok":true,"address":"0x..."}
import { loadSigner, done, fail } from "./lib.mjs";
try {
  const s = loadSigner(null);
  done(s ? { ok: true, address: s.address.toLowerCase() } : { ok: false, error: "no RH_PRIVATE_KEY in .env" });
} catch (e) { fail(e); }
