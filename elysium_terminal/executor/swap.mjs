// Sell the non-quote token of a pool back to USDC / HYPE / WHYPE.
//   node swap.mjs --pool 0x.. --amount <raw|all> [--maxloss 5] [--dry-run --owner 0x..]
// minOut = pool price x (1 - fee) x (1 - maxloss). A worse sale reverts in simulation and is not sent.
import { pathToFileURL } from "node:url";
import { args, done, fail, tradeProvider, loadSigner, describePool, slot0, quoteSide, balanceOf, approvalTxs, erc20Approval,
  swapCalldataV4, swapCalldataV3Router, swapCalldataV3Universal, sendTx, deadline, minOutFor, v3LpFee,
  UNIVERSAL_ROUTER, ZERO, WHYPE } from "./lib.mjs";

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
  const poolAddr = desc.dex === "v4" ? desc.pool : key.pool;
  const s0 = await slot0(pr, poolAddr, desc.dex);
  const zeroForOne = token === key.currency0.toLowerCase();
  let feeFrac;
  if (desc.dex === "v4") {
    const pf = zeroForOne ? s0.protocolFee & 0xfff : (s0.protocolFee >> 12) & 0xfff;
    feeFrac = ((s0.lpFee || 0) + pf) / 1e6;
  } else {
    feeFrac = v3LpFee(key.fee, s0.protocolFee, zeroForOne) / 1e6;
  }
  const { minOut, expected } = minOutFor(amount, zeroForOne, s0.sqrtPriceX96, feeFrac, maxLossPct);
  if (minOut <= 0n) throw new Error("sale too small to price");
  const steps = [];
  const dl = deadline();
  let tx, to;
  if (desc.dex === "v4") {
    for (const t of await approvalTxs(pr, owner, token, UNIVERSAL_ROUTER, amount)) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
    tx = swapCalldataV4(key, zeroForOne, amount, minOut, dl);
    to = UNIVERSAL_ROUTER;
  } else if (desc.swap === "router") {
    for (const t of await erc20Approval(pr, owner, token, desc.router, amount, "approve token → SwapRouter")) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
    tx = swapCalldataV3Router(key, token, quote, amount, minOut, owner, dl);
    to = desc.router;
    if (token === WHYPE) tx = { ...tx, value: 0n };
  } else {
    for (const t of await approvalTxs(pr, owner, token, UNIVERSAL_ROUTER, amount)) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
    tx = swapCalldataV3Universal(key, token, quote, amount, minOut, owner, dl);
    to = UNIVERSAL_ROUTER;
  }
  if (dry && steps.length) return { steps, minOut, expected, note: "approvals missing, so the sale itself was not simulated" };
  const before = await balanceOf(pr, quote, owner);
  const r = await sendTx(signer, { ...tx, to, label: "sell token" }, { dryRun: dry, from: owner });
  steps.push(r);
  const got = dry ? null : (await balanceOf(pr, quote, owner)) - before + (quote === ZERO ? (r.fee || 0n) : 0n);
  return { steps, hash: r.hash, minOut, expected, received: got, quote };
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const a = args();
  try {
    const dry = !!a["dry-run"];
    const signer = dry ? null : loadSigner(tradeProvider());
    if (!dry && !signer) throw new Error("no ELYSIUM_PRIVATE_KEY in .env");
    const owner = dry ? a.owner : signer.address;
    const r = await sellToken({ signer, owner, pool: String(a.pool), amount: a.amount || "all", maxLossPct: Number(a.maxloss || 5), dry });
    done({ ok: true, dryRun: dry, ...r });
  } catch (e) { fail(e); }
}
