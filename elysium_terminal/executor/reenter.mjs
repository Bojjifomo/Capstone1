// Close + swap, and optionally re-open, for one ladder.
//   node reenter.mjs --pool 0x.. --ids 1,2 [--maxloss 5] [--reopen --slices 5 --top 0 --bottom 60] [--dry-run --owner 0x..]
// Dry run stops after the close simulation: the sale depends on what the close returns.
import { args, done, fail, tradeProvider, loadSigner, quoteSide, slot0, planLadder, openCalldataV4, openCalldataV3,
  approvalTxs, erc20Approval, sendTx, deadline, mintedIds, decimalsOf, ZERO, WHYPE, GAS_RESERVE, balanceOf } from "./lib.mjs";
import { closePositions } from "./close.mjs";
import { sellToken } from "./swap.mjs";

const a = args();
const out = { ok: false, steps: [] };
try {
  const dry = !!a["dry-run"];
  const signer = dry ? null : loadSigner(tradeProvider());
  if (!dry && !signer) throw new Error("no ELYSIUM_PRIVATE_KEY in .env");
  const owner = (dry ? String(a.owner || "") : signer.address).toLowerCase();
  const ids = String(a.ids || "").split(",").filter(Boolean).map(x => BigInt(x));
  if (!ids.length || !a.pool) throw new Error("--pool and --ids required");
  out.step = "close";
  const c = await closePositions({ pool: String(a.pool), ids, dry, owner });
  out.steps.push({ step: "close", hash: c.tx.hash, simulated: c.tx.simulated });
  const desc = c.desc;
  const key = desc.key;
  const q = quoteSide(key);
  if (q === null) throw new Error("pool has no USDC / HYPE side");
  const quote = (q === 0 ? key.currency0 : key.currency1).toLowerCase();
  const token = (q === 0 ? key.currency1 : key.currency0).toLowerCase();
  if (dry) {
    done({ ok: true, dryRun: true, steps: out.steps, dex: desc.dex, note: "dry run stops after the close simulation" });
    process.exit(0);
  }
  out.step = "swap";
  const tokOut = c.received[token] || 0n;
  let quoteBack = c.received[quote] || 0n;
  if (tokOut > 0n) {
    const s = await sellToken({ signer, owner, pool: String(a.pool), amount: tokOut, maxLossPct: Number(a.maxloss || 5) });
    out.steps.push({ step: "swap", hash: s.hash, received: s.received });
    quoteBack += s.received || 0n;
  }
  out.quoteBack = quoteBack;
  if (a.reopen) {
    out.step = "reopen";
    const pr = signer.provider;
    const poolAddr = desc.dex === "v4" ? desc.pool : key.pool;
    const s0 = await slot0(pr, poolAddr, desc.dex);
    const native = (desc.dex === "v4" && quote === ZERO) || (desc.dex === "v3" && quote === WHYPE && (await balanceOf(pr, WHYPE, owner)) < quoteBack);
    const reserve = native ? GAS_RESERVE : 0n;
    const dep = (native ? await balanceOf(pr, ZERO, owner) : quoteBack) - reserve;
    const use = native ? (quoteBack < dep ? quoteBack : dep) : quoteBack;
    if (use <= 0n) throw new Error("nothing came back to re-open with");
    const plan = planLadder(key, s0.tick, use, q === 0, { slices: Number(a.slices || ids.length), top: Number(a.top || 0), bottom: Math.abs(Number(a.bottom || 60)) });
    const total = plan.slices.reduce((s, x) => s + x.amount, 0n);
    const appr = native ? [] : (desc.dex === "v4"
      ? await approvalTxs(pr, owner, quote, desc.manager, total)
      : await erc20Approval(pr, owner, quote, desc.manager, total, "approve quote → NPM"));
    for (const t of appr) out.steps.push({ step: "approve", ...(await sendTx(signer, t)) });
    const tx = desc.dex === "v4" ? openCalldataV4(key, plan, q === 0, owner, deadline()) : openCalldataV3(key, plan, q === 0, owner, deadline(), native && quote === WHYPE);
    const r = await sendTx(signer, { ...tx, to: desc.manager, label: `re-open ${plan.slices.length} slices` });
    out.positions = mintedIds(r.logs, owner, desc.manager);
    out.steps.push({ step: "reopen", hash: r.hash, positions: out.positions });
    out.reopened = total;
    out.reopenedQuote = Number(total) / 10 ** (await decimalsOf(pr, quote));
    out.manager = desc.manager.toLowerCase();
    out.venue = desc.venue;
  }
  out.ok = true;
  delete out.step;
  done(out);
} catch (e) { fail(e, { step: out.step, steps: out.steps }); }
