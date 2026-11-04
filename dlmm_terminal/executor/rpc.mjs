// One place that decides which RPC a script talks to, and counts every request, so the
// Helius free tier is only spent where it has to be.
//
//   readConnection()   read-only work (ladder plan / preview): public node first, keyed RPC
//                      (SOLANA_RPC_URL, e.g. Helius) only if the public one fails.
//   tradeConnection()  build, simulate, send, confirm: the keyed RPC (public if none is set).
//   rpcStats()         {calls, byHost: {host: n}, byMethod: {method: n}} for the JSON output;
//                      Python logs it to data/rpc_usage.jsonl (`python -m dlmm_terminal rpc`).
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { Connection } = require("@solana/web3.js");

export const PUBLIC_RPC = "https://api.mainnet-beta.solana.com";
const stats = { calls: 0, byHost: {}, byMethod: {} };

const label = url => {
  try {
    const h = new URL(url).hostname;
    return /helius/i.test(h) ? "helius" : h === new URL(PUBLIC_RPC).hostname ? "public" : h;
  } catch { return "?"; }
};

function countingFetch(url) {
  const host = label(url);
  return (input, init) => {
    let methods = [];
    try {
      const body = JSON.parse(init?.body ?? "null");
      methods = (Array.isArray(body) ? body : [body]).map(b => b?.method).filter(Boolean);
    } catch {}
    for (const m of methods.length ? methods : ["?"]) {
      stats.calls++;
      stats.byHost[host] = (stats.byHost[host] || 0) + 1;
      stats.byMethod[m] = (stats.byMethod[m] || 0) + 1;
    }
    return fetch(input, init);
  };
}

const make = url => new Connection(url, { commitment: "confirmed", fetch: countingFetch(url) });

export const keyedUrl = () => process.env.SOLANA_RPC_URL || null;
export const tradeConnection = () => make(keyedUrl() || PUBLIC_RPC);

// Run `fn(connection)` on the public node; on failure (rate limit, timeout) retry once on the keyed RPC.
export async function withReadConnection(fn) {
  try {
    return await fn(make(PUBLIC_RPC));
  } catch (e) {
    if (!keyedUrl()) throw e;
    return await fn(make(keyedUrl()));
  }
}

export const rpcStats = () => stats;
export const connectionFor = url => make(url);
