// Sell a token for the pool's quote (USDG or ETH) through the Universal Router, on an exact PoolKey.
//   node swap.mjs --pool 0x<poolId> --amount <raw | all> [--maxloss 5] [--dry-run --owner 0x..]
// minOut = pool price x (1 - fee) x (1 - maxloss): a sale that would land further below the pool's
// own price than that reverts in simulation and is never sent.
import { pathToFileURL } from "node:url";
import { args, done, fail, pickTradeProvider, loadSigner, poolKey, poolIdOf, slot0, quoteSide, balanceOf, approvalTxs,
  swapCalldata, sendTx, deadline, rawPrice01, receivedFromLogs, ethDeltaAt, UNIVERSAL_ROUTER, ZERO } from "./lib.mjs";

// partial: when the whole amount would land more than maxLoss under the pool price (a thin pool,
// a big ladder), sell the largest part that stays inside it and leave the rest in the wallet.
export async function sellToken({ signer, owner, pool, key, amount, maxLossPct = 5, dry = false, partial = true }) {
  const pr = signer ? signer.provider : (await pickTradeProvider());
  key = key || await poolKey(pr, pool);
  pool = pool || poolIdOf(key);
  const q = quoteSide(key);
  if (q === null) throw new Error("pool has no USDG / ETH side");
  const token = q === 0 ? key.currency1 : key.currency0;
  const quote = q === 0 ? key.currency0 : key.currency1;
  if (amount === "all") amount = await balanceOf(pr, token, owner);
  amount = BigInt(amount);
  if (amount <= 0n) return { skipped: "nothing to sell", steps: [] };
  const s0 = await slot0(pr, pool);
  const zeroForOne = token === key.currency0;          // selling currency0 -> currency1
  const p01 = rawPrice01(s0.sqrtPriceX96);
  const pf = zeroForOne ? s0.protocolFee & 0xfff : (s0.protocolFee >> 12) & 0xfff;
  const feeFrac = (s0.lpFee + pf) / 1e6;
  const quoteFor = amt => {
    const expected = zeroForOne ? Number(amt) * p01 : Number(amt) / p01;
    return { expected, minOut: BigInt(Math.floor(expected * (1 - feeFrac) * (1 - maxLossPct / 100))) };
  };
  const { expected, minOut } = quoteFor(amount);
  if (minOut <= 0n) throw new Error("sale too small to price");
  const steps = [];
  for (const t of await approvalTxs(pr, owner, token, UNIVERSAL_ROUTER, amount)) {
    steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
  }
  if (dry && steps.length) return { steps, minOut, expected, note: "approvals missing, so the sale itself was not simulated" };
  const call = amt => { const m = quoteFor(amt).minOut; return { ...swapCalldata(key, zeroForOne, amt, m, deadline()), to: UNIVERSAL_ROUTER, minOut: m }; };
  let sell = amount, tx = call(amount), r;
  try {
    // retries: the token may have landed one block ago (close + sell), and the node can lag
    r = await sendTx(signer, { ...tx, label: "sell token" }, { dryRun: dry, from: owner, retries: 3 });
  } catch (e) {
    if (!partial || !/would revert/.test(e.message)) throw e;
    // biggest amount whose sale stays inside maxLoss: binary search on simulations (calls, nothing sent)
    let lo = 0n, hi = amount;
    for (let i = 0; i < 8 && hi - lo > amount / 200n; i++) {
      const mid = (lo + hi) / 2n;
      if (mid <= 0n) break;
      const t = call(mid);
      try { await pr.call({ from: owner, to: t.to, data: t.data, value: t.value || 0n }); lo = mid; } catch { hi = mid; }
    }
    if (lo <= 0n || quoteFor(lo).minOut <= 0n) {
      throw new Error(`sell token: semua ukuran jatuh > ${maxLossPct}% di bawah harga pool (pool terlalu tipis), token tetap di wallet`);
    }
    sell = lo;
    tx = call(sell);
    r = await sendTx(signer, { ...tx, label: "sell token (sebagian)" }, { dryRun: dry, from: owner, retries: 2 });
  }
  steps.push(r);
  // from the receipt, never a balance read from a lagging node; ETH: gas added back
  const got = dry ? null : quote === ZERO ? (await ethDeltaAt(pr, owner, r.block)) + (r.fee || 0n)
    : receivedFromLogs(r.logs, owner, [quote])[quote];
  const left = amount - sell;
  return { steps, hash: r.hash, minOut: tx.minOut, expected, received: got, quote, sold: sell, left,
    note: left > 0n ? `jual ${Number(sell * 1000n / amount) / 10}% (sisanya bakal jatuh > ${maxLossPct}% di bawah harga pool); sisa token di wallet, pakai Cleanup / Close+swap lagi nanti` : undefined };
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const a = args();
  try {
    const dry = !!a["dry-run"];
    const signer = dry ? null : loadSigner((await pickTradeProvider()));
    if (!dry && !signer) throw new Error("no RH_PRIVATE_KEY in .env");
    const owner = dry ? a.owner : signer.address;
    const r = await sellToken({ signer, owner, pool: String(a.pool).toLowerCase(), amount: a.amount || "all",
      maxLossPct: Number(a.maxloss || 5), dry });
    done({ ok: true, dryRun: dry, ...r });
  } catch (e) { fail(e); }
}
