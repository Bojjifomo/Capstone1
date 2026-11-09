// Close + swap, and optionally re-open (re-entry), for the slices of one ladder.
//   node reenter.mjs --ids 1,2,3 [--maxloss 5] [--reopen --slices 5 --top 0 --bottom 60] [--dry-run --owner 0x..]
// 1) close every slice (withdraw 100% + fees) in one transaction
// 2) sell the token that came out (the balance change only: tokens already in the wallet stay)
//    to the pool's quote, refused if it would land > maxloss % under the pool price.
//    A ladder that was never in range pays out only the quote: nothing to sell, straight to 3.
// 3) --reopen: a fresh ladder at today's price with all the quote that came back (ETH keeps gas)
// If the sale fails or is refused, nothing is re-opened and the token stays in the wallet.
// What came back is read from the receipts, never from a balance a lagging node might report.
import { args, done, fail, pickTradeProvider, loadSigner, quoteSide, slot0, planLadder, openCalldata, approvalTxs, sendTx,
  deadline, mintedIds, positionInfo, positionAmounts, waitForBalance, POSITION_MANAGER, ZERO } from "./lib.mjs";
import { closePositions } from "./close.mjs";
import { sellToken } from "./swap.mjs";

const a = args();
const out = { ok: false, steps: [] };
const shape = n => ({ slices: Number(a.slices || n), top: Number(a.top || 0), bottom: Math.abs(Number(a.bottom || 60)) });
try {
  const dry = !!a["dry-run"];
  const signer = dry ? null : loadSigner((await pickTradeProvider()));
  if (!dry && !signer) throw new Error("no RH_PRIVATE_KEY in .env");
  const owner = (dry ? String(a.owner || "") : signer.address).toLowerCase();
  const ids = String(a.ids || "").split(",").filter(Boolean).map(x => BigInt(x));
  if (!ids.length) throw new Error("--ids required");

  if (dry) {
    // Practice: simulate the close, then say what the sale and the re-open would do at today's price.
    const pr = await pickTradeProvider();
    const ps = await Promise.all(ids.map(id => positionInfo(pr, id)));
    const pools = [...new Set(ps.map(p => p.poolId))];
    if (pools.length !== 1) throw new Error("re-entry works on one ladder (one pool) at a time");
    const key = ps[0].key, q = quoteSide(key);
    if (q === null) throw new Error("pool has no USDG / ETH side");
    out.step = "close";
    const c = await closePositions({ ids, dry: true, owner });
    out.steps.push({ step: "close", simulated: c.tx.simulated });
    const s0 = await slot0(pr, pools[0]);
    let qAmt = 0n, tAmt = 0n;
    for (const p of ps) {
      const am = positionAmounts(p, s0.sqrtPriceX96);
      qAmt += q === 0 ? am.amount0 : am.amount1;
      tAmt += q === 0 ? am.amount1 : am.amount0;
    }
    out.expected = { quote: qAmt, token: tAmt };
    out.steps.push({ step: "swap", skipped: tAmt === 0n ? "nggak ada token (ladder belum pernah kena harga)" : "jual token yang keluar" });
    if (a.reopen) {
      const plan = planLadder(key, s0.tick, qAmt, q === 0, shape(ids.length));
      out.steps.push({ step: "reopen", planned: plan.slices.length, note: `ladder baru ${plan.slices.length} slice, harga → −${plan.realEnd.toFixed(1)}%` });
    }
    delete out.step;
    done({ ...out, ok: true, dryRun: true, note: "simulasi close lolos; jual + buka ulang dihitung dari harga sekarang (fee belum masuk hitungan)" });
    process.exit(0);
  }

  out.step = "close";
  const c = await closePositions({ ids, owner, signer });
  out.steps.push({ step: "close", hash: c.tx.hash });
  const keys = [...new Map(c.positions.map(p => [p.poolId, p.key])).entries()];
  if (keys.length !== 1) throw new Error("re-entry works on one ladder (one pool) at a time");
  const [poolId, key] = keys[0];
  const q = quoteSide(key);
  if (q === null) throw new Error("pool has no USDG / ETH side");
  const quote = q === 0 ? key.currency0 : key.currency1, token = q === 0 ? key.currency1 : key.currency0;

  out.step = "swap";
  const tokOut = c.received[token] || 0n;
  let quoteBack = c.received[quote] || 0n;
  if (tokOut > 0n) {
    const s = await sellToken({ signer, owner, pool: poolId, key, amount: tokOut, maxLossPct: Number(a.maxloss || 5) });
    out.steps.push({ step: "swap", hash: s.hash, received: s.received, sold: s.sold, left: s.left, note: s.note });
    if (s.left > 0n) out.tokenLeft = s.left;
    quoteBack += s.received || 0n;
  } else {
    out.steps.push({ step: "swap", skipped: "nggak ada token keluar (ladder belum pernah kena harga), langsung lanjut" });
  }
  out.quoteBack = quoteBack;

  if (a.reopen) {
    out.step = "reopen";
    const pr = signer.provider;
    const reserve = quote === ZERO ? 100_000_000_000_000n : 0n;   // ~0.0001 ETH stays for gas
    let dep = quoteBack - reserve;
    if (dep <= 0n) throw new Error(`close cuma balikin ${quoteBack} (raw) ${quote === ZERO ? "ETH" : "USDG"}: nggak ada yang bisa dibuka ulang`);
    // the close/sale is mined; make sure the node we simulate on sees the money too
    const bal = await waitForBalance(pr, quote, owner, dep + reserve);
    if (bal - reserve < dep) {
      if (bal - reserve <= 0n) throw new Error("RPC belum lihat dana dari close (node ketinggalan); coba Zap manual sebentar lagi");
      dep = bal - reserve;
    }
    const s0 = await slot0(pr, poolId);
    const plan = planLadder(key, s0.tick, dep, q === 0, shape(ids.length));
    const total = plan.slices.reduce((s, x) => s + x.amount, 0n);
    for (const t of await approvalTxs(pr, owner, quote, POSITION_MANAGER, total)) out.steps.push({ step: "approve", ...(await sendTx(signer, t)) });
    const tx = openCalldata(key, plan, q === 0, owner, deadline());
    const r = await sendTx(signer, { ...tx, to: POSITION_MANAGER, label: `re-open ${plan.slices.length} slices` }, { retries: 3 });
    const minted = mintedIds(r.logs, owner);
    out.steps.push({ step: "reopen", hash: r.hash, positions: minted });
    out.positions = minted;
    out.reopened = total;
  }
  out.ok = true;
  delete out.step;
  done(out);
} catch (e) { fail(e, { step: out.step, steps: out.steps, quoteBack: out.quoteBack }); }
