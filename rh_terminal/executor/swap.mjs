// Sell a token for the pool's quote (USDG or ETH) through the Universal Router, on an exact PoolKey.
//   node swap.mjs --pool 0x<poolId> --amount <raw | all> [--maxloss 5] [--dry-run --owner 0x..]
// minOut = pool price x (1 - fee) x (1 - maxloss): a sale that would land further below the pool's
// own price than that reverts in simulation and is never sent.
import { pathToFileURL } from "node:url";
import { args, done, fail, pickTradeProvider, loadSigner, poolKey, poolIdOf, slot0, quoteSide, balanceOf, approvalTxs,
  swapCalldata, sendTx, deadline, rawPrice01, receivedFromLogs, ethDeltaAt, UNIVERSAL_ROUTER, ZERO } from "./lib.mjs";

export async function sellToken({ signer, owner, pool, key, amount, maxLossPct = 5, dry = false }) {
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
  const expected = zeroForOne ? Number(amount) * p01 : Number(amount) / p01;
  const minOut = BigInt(Math.floor(expected * (1 - feeFrac) * (1 - maxLossPct / 100)));
  if (minOut <= 0n) throw new Error("sale too small to price");
  const steps = [];
  for (const t of await approvalTxs(pr, owner, token, UNIVERSAL_ROUTER, amount)) {
    steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
  }
  if (dry && steps.length) return { steps, minOut, expected, note: "approvals missing, so the sale itself was not simulated" };
  const tx = swapCalldata(key, zeroForOne, amount, minOut, deadline());
  // retries: the token may have landed one block ago (close + sell), and the node can lag
  const r = await sendTx(signer, { ...tx, to: UNIVERSAL_ROUTER, label: "sell token" }, { dryRun: dry, from: owner, retries: 3 });
  steps.push(r);
  // from the receipt, never a balance read from a lagging node; ETH: gas added back
  const got = dry ? null : quote === ZERO ? (await ethDeltaAt(pr, owner, r.block)) + (r.fee || 0n)
    : receivedFromLogs(r.logs, owner, [quote])[quote];
  return { steps, hash: r.hash, minOut, expected, received: got, quote };
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
