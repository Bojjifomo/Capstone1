// Sell leftover tokens in the wallet to USDG / ETH, each through the pool given for it.
//   node cleanup.mjs --tokens '[{"token":"0x..","pool":"0x..","valueUsd":12.3}]' [--minusd 1] [--maxloss 5] [--dry-run --owner 0x..]
// The list comes from RH Terminal (tokens it has seen in your ladders, each with its deepest
// USDG/ETH pool). USDG, ETH and WETH are never sold. A token worth less than --minusd is left.
import { args, done, fail, tradeProvider, loadSigner, balanceOf, USDG, WETH, ZERO } from "./lib.mjs";
import { sellToken } from "./swap.mjs";

const a = args();
try {
  const dry = !!a["dry-run"];
  const signer = dry ? null : loadSigner(tradeProvider());
  if (!dry && !signer) throw new Error("no RH_PRIVATE_KEY in .env");
  const owner = dry ? String(a.owner || "") : signer.address;
  const list = JSON.parse(a.tokens || "[]");
  const min = Number(a.minusd || 1);
  const res = [];
  for (const t of list) {
    const tok = String(t.token).toLowerCase();
    if ([USDG, WETH, ZERO].includes(tok)) continue;
    if ((t.valueUsd || 0) < min) { res.push({ token: tok, symbol: t.symbol, status: `skip: < $${min}` }); continue; }
    try {
      const bal = await balanceOf(signer ? signer.provider : tradeProvider(), tok, owner);
      if (bal === 0n) { res.push({ token: tok, symbol: t.symbol, status: "empty" }); continue; }
      const s = await sellToken({ signer, owner, pool: String(t.pool).toLowerCase(), amount: bal, maxLossPct: Number(a.maxloss || 5), dry });
      res.push({ token: tok, symbol: t.symbol, status: dry ? "would sell" : "sold", hash: s.hash, received: s.received, note: s.note });
    } catch (e) { res.push({ token: tok, symbol: t.symbol, status: "failed: " + String(e.message || e).slice(0, 160) }); }
  }
  done({ ok: true, dryRun: dry, tokens: res });
} catch (e) { fail(e); }
