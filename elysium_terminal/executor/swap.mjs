// Sell the non-quote token of a pool back to USDC / HYPE / WHYPE.
//   node swap.mjs --pool 0x.. --amount <raw|all> [--maxloss 5] [--dry-run --owner 0x..]
// minOut = pool price x (1 - fee) x (1 - maxloss). A worse sale reverts in simulation and is not sent.
import { pathToFileURL } from "node:url";
import { args, done, fail, tradeProvider, loadSigner, describePool, slot0, quoteSide, balanceOf, approvalTxs, erc20Approval,
  receivedFromLogs, nativeDelta,
  swapCalldataV4, swapCalldataV3Router, swapCalldataV3Universal, sendTx, deadline, minOutFor, v3LpFee,
  UNIVERSAL_ROUTER, ZERO } from "./lib.mjs";

// Exact-in swap through one pool, either direction. minOut = pool price x (1 - fee) x (1 - maxLossPct).
// Returns what actually arrived (balance difference), not the quote.
export async function swapInPool({ signer, owner, desc, zeroForOne, amount, maxLossPct = 5, dry = false, label = "swap", partial = true }) {
  const pr = signer ? signer.provider : tradeProvider();
  const key = desc.key;
  const tokenIn = (zeroForOne ? key.currency0 : key.currency1).toLowerCase();
  const tokenOut = (zeroForOne ? key.currency1 : key.currency0).toLowerCase();
  amount = BigInt(amount);
  if (amount <= 0n) return { skipped: "nothing to swap", steps: [] };
  const s0 = await slot0(pr, desc.dex === "v4" ? desc.pool : key.pool, desc.dex);
  let feeFrac;
  if (desc.dex === "v4") {
    const pf = zeroForOne ? s0.protocolFee & 0xfff : (s0.protocolFee >> 12) & 0xfff;
    feeFrac = ((s0.lpFee || key.fee || 0) + pf) / 1e6;
  } else {
    feeFrac = v3LpFee(key.fee, s0.protocolFee, zeroForOne) / 1e6;
  }
  const { minOut, expected } = minOutFor(amount, zeroForOne, s0.sqrtPriceX96, feeFrac, maxLossPct);
  if (minOut <= 0n) throw new Error("swap too small to price");
  const steps = [];
  const dl = deadline();
  let tx, to;
  if (desc.dex === "v4") {
    for (const t of await approvalTxs(pr, owner, tokenIn, UNIVERSAL_ROUTER, amount)) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
    tx = swapCalldataV4(key, zeroForOne, amount, minOut, dl);
    to = UNIVERSAL_ROUTER;
  } else if (desc.swap === "router") {
    for (const t of await erc20Approval(pr, owner, tokenIn, desc.router, amount, "approve → SwapRouter")) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
    tx = swapCalldataV3Router(key, tokenIn, tokenOut, amount, minOut, owner, dl);
    to = desc.router;
  } else {
    for (const t of await approvalTxs(pr, owner, tokenIn, UNIVERSAL_ROUTER, amount)) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
    tx = swapCalldataV3Universal(key, tokenIn, tokenOut, amount, minOut, owner, dl);
    to = UNIVERSAL_ROUTER;
  }
  if (dry && steps.length) return { steps, minOut, expected, tokenOut, note: "approvals missing, so the swap itself was not simulated" };
  const build = amt => {
    const m = minOutFor(amt, zeroForOne, s0.sqrtPriceX96, feeFrac, maxLossPct).minOut;
    const t = desc.dex === "v4" ? swapCalldataV4(key, zeroForOne, amt, m, dl)
      : desc.swap === "router" ? swapCalldataV3Router(key, tokenIn, tokenOut, amt, m, owner, dl)
      : swapCalldataV3Universal(key, tokenIn, tokenOut, amt, m, owner, dl);
    return { ...t, to, minOut: m };
  };
  const nativeBefore = tokenOut === ZERO ? await balanceOf(pr, ZERO, owner) : 0n;
  let sell = amount, r;
  try {
    // retries: the token may have landed one block ago (close + sell) and the node can lag
    r = await sendTx(signer, { ...tx, to, label }, { dryRun: dry, from: owner, retries: 3 });
  } catch (e) {
    if (!partial || !/would revert/.test(e.message)) throw e;
    // The whole amount would land more than maxLoss under the pool price (thin pool, big ladder):
    // sell the biggest part that stays inside it. Binary search on simulations, nothing sent.
    let lo = 0n, hi = amount;
    for (let i = 0; i < 8 && hi - lo > amount / 200n; i++) {
      const mid = (lo + hi) / 2n;
      if (mid <= 0n) break;
      const t = build(mid);
      try { await pr.call({ from: owner, to: t.to, data: t.data, value: t.value || 0n }); lo = mid; } catch { hi = mid; }
    }
    if (lo <= 0n || build(lo).minOut <= 0n) {
      throw new Error(`${label}: semua ukuran jatuh > ${maxLossPct}% di bawah harga pool (pool terlalu tipis), token tetap di wallet`);
    }
    sell = lo;
    r = await sendTx(signer, { ...build(sell), label: `${label} (sebagian)` }, { dryRun: dry, from: owner, retries: 2 });
  }
  steps.push(r);
  const got = dry ? null : tokenOut === ZERO ? await nativeDelta(pr, owner, r, nativeBefore)
    : receivedFromLogs(r.logs, owner, [tokenOut])[tokenOut];
  const left = amount - sell;
  return { steps, hash: r.hash, minOut, expected, received: got, tokenOut, feeFrac, sold: sell, left,
    note: left > 0n ? `jual ${Number(sell * 1000n / amount) / 10}% (sisanya bakal jatuh > ${maxLossPct}% di bawah harga pool); sisa token di wallet` : undefined };
}

export async function sellToken({ signer, owner, pool, amount, maxLossPct = 5, dry = false }) {
  const pr = signer ? signer.provider : tradeProvider();
  const desc = await describePool(pr, pool);
  const key = desc.key;
  const q = quoteSide(key);
  if (q === null) throw new Error("pool has no USDC / HYPE side");
  const token = (q === 0 ? key.currency1 : key.currency0).toLowerCase();
  const quote = (q === 0 ? key.currency0 : key.currency1).toLowerCase();
  if (amount === "all" || amount == null) amount = await balanceOf(pr, token, owner);
  amount = BigInt(amount);
  if (amount <= 0n) return { skipped: "nothing to sell", steps: [] };
  const r = await swapInPool({ signer, owner, desc, zeroForOne: token === key.currency0.toLowerCase(), amount, maxLossPct, dry, label: "sell token" });
  return { ...r, quote };
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const a = args();
  try {
    const dry = !!a["dry-run"];
    const signer = dry ? null : loadSigner(tradeProvider());
    if (!dry && !signer) throw new Error("no ELYSIUM_PRIVATE_KEY in .env");
    const owner = dry ? a.owner : signer.address;
    let amount = a.amount || "all";
    if (a["cap-balance"] && amount !== "all") {
      // a leftover recorded earlier: never more than what the wallet holds now (sold by hand meanwhile)
      const pr = signer ? signer.provider : tradeProvider();
      const desc = await describePool(pr, String(a.pool));
      const q = quoteSide(desc.key), tok = q === 0 ? desc.key.currency1 : desc.key.currency0;
      const bal = await balanceOf(pr, tok, owner);
      if (BigInt(amount) > bal) amount = bal;
    }
    const r = await sellToken({ signer, owner, pool: String(a.pool), amount, maxLossPct: Number(a.maxloss || 5), dry });
    done({ ok: true, dryRun: dry, ...r });
  } catch (e) { fail(e); }
}
