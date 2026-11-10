// Open a quote-only ladder below the price. v4 pool id (32 bytes) or v3 pool address (20 bytes).
//   node open_ladder.mjs --pool 0x.. --usd 400 [--hypeusd 75] --slices 5 --top 0 --bottom 60 [--plan | --dry-run --owner 0x..]
import { args, done, fail, withRead, tradeProvider, loadSigner, describePool, slot0, quoteSide, quoteSymbol, decimalsOf,
  balanceOf, planLadder, openCalldataV4, openCalldataV3, approvalTxs, erc20Approval, sendTx, deadline, mintedIds,
  POSITION_MANAGER, ZERO, USDC, WHYPE, GAS_RESERVE, ethers } from "./lib.mjs";

const a = args();
try {
  const poolArg = String(a.pool || "");
  const { desc, s0, dec } = await withRead(async pr => {
    const desc = await describePool(pr, poolArg);
    const s0 = await slot0(pr, desc.dex === "v4" ? desc.pool : desc.key.pool, desc.dex);
    const q = quoteSide(desc.key);
    const quote = q === 0 ? desc.key.currency0 : desc.key.currency1;
    return { desc, s0, dec: await decimalsOf(pr, quote), q, quote };
  });
  const q = quoteSide(desc.key);
  if (q === null) throw new Error("not a USDC / HYPE / WHYPE pool");
  const quote = (q === 0 ? desc.key.currency0 : desc.key.currency1).toLowerCase();
  let units = a.amount != null ? Number(a.amount) : null;
  if (units == null) {
    const usd = Number(a.usd);
    if (!(usd > 0)) throw new Error("--usd or --amount required");
    const px = quote === USDC ? 1 : Number(a.hypeusd || a.ethusd);
    if (!(px > 0)) throw new Error("--hypeusd needed to size a HYPE ladder in dollars");
    units = usd / px;
  }
  const depositRaw = BigInt(Math.floor(units * 10 ** dec));
  const plan = planLadder(desc.key, s0.tick, depositRaw, q === 0, { slices: Number(a.slices || 5), top: Number(a.top || 0), bottom: Math.abs(Number(a.bottom || 60)) });
  const total = plan.slices.reduce((s, x) => s + x.amount, 0n);
  const view = {
    pool: desc.pool || poolArg.toLowerCase(), dex: desc.dex, venue: desc.venue, key: desc.key, quote, quoteSymbol: quoteSymbol(quote),
    quoteDecimals: dec, tick: s0.tick, lpFee: s0.lpFee ?? desc.key.fee, total: Number(total) / 10 ** dec, realEnd: plan.realEnd, step: plan.step,
    slices: plan.slices.map(s => ({ tickLower: s.tickLower, tickUpper: s.tickUpper, amount: Number(s.amount) / 10 ** dec, nearPct: s.nearPct, farPct: s.farPct })),
  };
  if (a.plan) { done({ ok: true, plan: view }); process.exit(0); }

  const dry = !!a["dry-run"];
  const signer = dry ? null : loadSigner(tradeProvider());
  if (!dry && !signer) throw new Error("no ELYSIUM_PRIVATE_KEY in .env");
  const owner = dry ? String(a.owner || "") : signer.address;
  if (!ethers.isAddress(owner)) throw new Error("--owner needed for --dry-run");
  const pr = signer ? signer.provider : tradeProvider();
  let native = false;
  if (desc.dex === "v4" && quote === ZERO) native = true;
  if (desc.dex === "v3" && quote === WHYPE) {
    const whypeBal = await balanceOf(pr, WHYPE, owner);
    const hypeBal = await balanceOf(pr, ZERO, owner);
    if (whypeBal < total && hypeBal >= total + GAS_RESERVE) native = true;
  }
  const bal = native ? await balanceOf(pr, ZERO, owner) : await balanceOf(pr, quote, owner);
  const reserve = native ? GAS_RESERVE : 0n;
  if (bal < total + reserve) {
    throw new Error(`wallet holds ${Number(bal) / 10 ** dec} ${native ? "HYPE" : quoteSymbol(quote)}, ladder needs ${Number(total + reserve) / 10 ** dec}`);
  }
  const steps = [];
  const spender = desc.manager;
  const appr = native ? [] : (desc.dex === "v4"
    ? await approvalTxs(pr, owner, quote, spender, total)
    : await erc20Approval(pr, owner, quote, spender, total, "approve quote → NPM"));
  for (const t of appr) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
  const tx = desc.dex === "v4"
    ? openCalldataV4(desc.key, plan, q === 0, owner, deadline())
    : openCalldataV3(desc.key, plan, q === 0, owner, deadline(), native);
  if (dry && appr.length) {
    done({ ok: true, dryRun: true, plan: view, steps, note: "approvals missing, so the mint itself was not simulated" });
    process.exit(0);
  }
  const r = await sendTx(signer, { ...tx, to: desc.manager, label: `mint ${plan.slices.length} slices` }, { dryRun: dry, from: owner });
  steps.push(r);
  done({ ok: true, dryRun: dry, plan: view, steps, hash: r.hash, positions: r.logs ? mintedIds(r.logs, owner, desc.manager) : [],
    manager: desc.manager.toLowerCase(), venue: desc.venue });
} catch (e) { fail(e); }
