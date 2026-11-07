// Close + swap, and optionally re-open (re-entry), for the slices of one ladder.
//   node reenter.mjs --ids 1,2,3 [--maxloss 5] [--reopen --slices 5 --top 0 --bottom 60] [--dry-run --owner 0x..]
// 1) close every slice (withdraw 100% + fees) in one transaction
// 2) sell the token that came out (the balance change only: tokens already in the wallet stay)
//    to the pool's quote, refused if it would land > maxloss % under the pool price
// 3) --reopen: a fresh ladder at today's price with all the quote that came back (ETH keeps gas)
// If the sale fails or is refused, nothing is re-opened and the token stays in the wallet.
import { args, done, fail, pickTradeProvider, loadSigner, quoteSide, slot0, planLadder, openCalldata, approvalTxs, sendTx,
  deadline, mintedIds, POSITION_MANAGER, ZERO } from "./lib.mjs";
import { closePositions } from "./close.mjs";
import { sellToken } from "./swap.mjs";

const a = args();
const out = { ok: false, steps: [] };
try {
  const dry = !!a["dry-run"];
  const signer = dry ? null : loadSigner((await pickTradeProvider()));
  if (!dry && !signer) throw new Error("no RH_PRIVATE_KEY in .env");
  const owner = (dry ? String(a.owner || "") : signer.address).toLowerCase();
  const ids = String(a.ids || "").split(",").filter(Boolean).map(x => BigInt(x));
  if (!ids.length) throw new Error("--ids required");
  out.step = "close";
  const c = await closePositions({ ids, dry, owner });
  out.steps.push({ step: "close", hash: c.tx.hash, simulated: c.tx.simulated });
  const keys = [...new Map(c.positions.map(p => [p.poolId, p.key])).entries()];
  if (keys.length !== 1) throw new Error("re-entry works on one ladder (one pool) at a time");
  const [poolId, key] = keys[0];
  const q = quoteSide(key);
  if (q === null) throw new Error("pool has no USDG / ETH side");
  const quote = q === 0 ? key.currency0 : key.currency1, token = q === 0 ? key.currency1 : key.currency0;
  if (dry) {
    done({ ok: true, dryRun: true, steps: out.steps, note: "dry run stops after the close simulation: the sale and re-open depend on what it returns" });
    process.exit(0);
  }
  out.step = "swap";
  const tokOut = c.received[token] || 0n;
  let quoteBack = c.received[quote] || 0n;
  if (tokOut > 0n) {
    const s = await sellToken({ signer, owner, pool: poolId, key, amount: tokOut, maxLossPct: Number(a.maxloss || 5) });
    out.steps.push({ step: "swap", hash: s.hash, received: s.received });
    quoteBack += s.received || 0n;
  }
  out.quoteBack = quoteBack;
  if (a.reopen) {
    out.step = "reopen";
    const pr = signer.provider;
    const s0 = await slot0(pr, poolId);
    const reserve = quote === ZERO ? 100_000_000_000_000n : 0n;   // ~0.0001 ETH stays for gas
    const dep = quoteBack - reserve;
    if (dep <= 0n) throw new Error("nothing came back to re-open with");
    const plan = planLadder(key, s0.tick, dep, q === 0, { slices: Number(a.slices || ids.length), top: Number(a.top || 0), bottom: Math.abs(Number(a.bottom || 60)) });
    const total = plan.slices.reduce((s, x) => s + x.amount, 0n);
    for (const t of await approvalTxs(pr, owner, quote, POSITION_MANAGER, total)) out.steps.push({ step: "approve", ...(await sendTx(signer, t)) });
    const tx = openCalldata(key, plan, q === 0, owner, deadline());
    const r = await sendTx(signer, { ...tx, to: POSITION_MANAGER, label: `re-open ${plan.slices.length} slices` });
    out.steps.push({ step: "reopen", hash: r.hash, positions: mintedIds(r.logs, owner) });
    out.reopened = total;
  }
  out.ok = true;
  delete out.step;
  done(out);
} catch (e) { fail(e, { step: out.step, steps: out.steps }); }
