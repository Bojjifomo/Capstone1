// Prints the trading wallet's address. Never prints the key.
import { loadSigner, done, fail } from "./lib.mjs";
try {
  const s = loadSigner(null);
  done(s ? { ok: true, address: s.address.toLowerCase() } : { ok: false, error: "no ELYSIUM_PRIVATE_KEY in elysium_terminal/.env" });
} catch (e) { fail(e); }
