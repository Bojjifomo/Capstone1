// A tight range, start to finish, in one run.
//
//   open       node tight.mjs --mode open --pool 0x.. --usd 100 --quote-usd 1 --width 2
//   rebalance  node tight.mjs --mode rebalance --pool 0x.. --ids 12 --width 2
//   common     [--slippage-bps 100] [--dry-run --owner 0x..]
//
// open:      take `usd` of the pool's quote, swap part of it to the token, mint a range of
//            `width` percent around the current price.
// rebalance: close the position (fees included), swap to the mix the new range needs, mint.
//
// The mint only uses what this run produced: the quote you asked for, or what the close
// returned. It never sweeps the rest of the wallet into the position.
//
// These are separate transactions (close, swap, mint). Each one is simulated first. If one
// fails, the earlier ones stay: the output says which `stage` failed and `sent: true` when
// something already went out, so the caller knows not to simply retry.
//
// Dry run: open simulates the swap; rebalance simulates the close. The later steps need
// tokens that do not exist until the earlier ones are real, so they are planned, not simulated.
import { args, done, fail, tradeProvider, loadSigner, describePool, slot0, quoteSide, quoteSymbol, decimalsOf,
  balanceOf, alignRange, mintAmounts, balancePlan, perLiquidity, sqrtOf, approvalTxs, erc20Approval, sendTx, deadline,
  tightMintCalldataV4, tightMintCalldataV3, mintedIds, wrapTx, v3LpFee, POSITION_MANAGER, ZERO, WHYPE, GAS_RESERVE,
  ethers } from "./lib.mjs";
import { closePositions } from "./close.mjs";
import { swapInPool } from "./swap.mjs";

const DYNAMIC = 0x800000;
const MINT_BUFFER = 0.005;       // v4 mints a fixed liquidity; leave 0.5% for the price to move
const a = args();
const out = { ok: false, steps: [], stage: "start" };
const sent = () => out.steps.some(s => s.hash);
const push = (stage, r) => { for (const s of (r.steps || [r])) out.steps.push({ step: stage, label: s.label, hash: s.hash, simulated: s.simulated, needed: s.needed }); };
const num = x => Number(x);

try {
  const dry = !!a["dry-run"];
  const mode = String(a.mode || "open");
  if (mode !== "open" && mode !== "rebalance") throw new Error("--mode open or rebalance");
  const signer = dry ? null : loadSigner(tradeProvider());
  if (!dry && !signer) throw new Error("no ELYSIUM_PRIVATE_KEY in .env");
  const owner = (dry ? String(a.owner || "") : signer.address).toLowerCase();
  if (!ethers.isAddress(owner)) throw new Error("--owner needed for --dry-run");
  const pr = signer ? signer.provider : tradeProvider();
  const widthPct = Number(a.width || 2);
  const slipPct = Math.min(10, Math.max(0.01, Number(a["slippage-bps"] || 100) / 100));

  const desc = await describePool(pr, String(a.pool || ""));
  const key = desc.key;
  const stop = error => { done({ ok: false, unsupported: true, error, steps: [] }); process.exit(0); };
  if ((key.hooks || ZERO).toLowerCase() !== ZERO) stop("pool ber-hook: tight range tidak didukung, tidak ada yang dikirim");
  if ((Number(key.fee) & DYNAMIC) !== 0) stop("fee dinamis: tight range tidak didukung, tidak ada yang dikirim");
  const q = quoteSide(key);
  if (q === null) throw new Error("bukan pool USDC / HYPE");
  const c0 = key.currency0.toLowerCase(), c1 = key.currency1.toLowerCase();
  const quote = q === 0 ? c0 : c1;
  const [dec0, dec1] = await Promise.all([decimalsOf(pr, c0), decimalsOf(pr, c1)]);
  const decQ = q === 0 ? dec0 : dec1;
  const poolAddr = desc.dex === "v4" ? desc.pool : key.pool;
  const read = () => slot0(pr, poolAddr, desc.dex);
  const feeOf = (s0, zeroForOne) => desc.dex === "v4"
    ? ((s0.lpFee || key.fee || 0) + (zeroForOne ? s0.protocolFee & 0xfff : (s0.protocolFee >> 12) & 0xfff)) / 1e6
    : v3LpFee(key.fee, s0.protocolFee, zeroForOne) / 1e6;
  // value of (raw0, raw1) in whole quote units at this price
  const inQuote = (s0, r0, r1) => {
    const p = sqrtOf(s0.sqrtPriceX96) ** 2;
    return (q === 0 ? num(r0) + num(r1) / p : num(r1) + num(r0) * p) / 10 ** decQ;
  };
  const band0 = s0 => {
    const b = alignRange(s0.tick, key.tickSpacing, widthPct);
    if (!b.ok) throw new Error(b.error || "range gagal");
    return b;
  };

  // ---- 1. where the money comes from -------------------------------------------
  let bud0 = 0n, bud1 = 0n, wrapPlanned = false;
  if (mode === "open") {
    out.stage = "budget";
    const usd = Number(a.usd), qUsd = Number(a["quote-usd"]);
    if (!(usd > 0) || !(qUsd > 0)) throw new Error("--usd and --quote-usd required");
    const need = BigInt(Math.floor(usd / qUsd * 10 ** decQ));
    let have = await balanceOf(pr, quote, owner);
    if (quote === ZERO) have = have > GAS_RESERVE ? have - GAS_RESERVE : 0n;
    if (have < need && quote === WHYPE) {
      const native = await balanceOf(pr, ZERO, owner);
      const short = need - have;
      if (native >= short + GAS_RESERVE) {
        out.stage = "wrap";
        if (dry) { out.steps.push({ step: "wrap", label: "wrap HYPE → WHYPE (direncanakan)", planned: true }); wrapPlanned = true; }
        else push("wrap", await sendTx(signer, wrapTx(short)));
        have = need;
      }
    }
    if (have < need) throw new Error(`wallet cuma punya ${(num(have) / 10 ** decQ).toPrecision(6)} ${quoteSymbol(quote)}, butuh ${(num(need) / 10 ** decQ).toPrecision(6)}`);
    if (q === 0) bud0 = need; else bud1 = need;
  } else {
    out.stage = "close";
    const ids = String(a.ids || "").split(",").filter(Boolean).map(x => BigInt(x));
    if (!ids.length) throw new Error("--ids required");
    const c = await closePositions({ pool: String(a.pool), ids, dry, owner });
    push("close", c.tx);
    out.closed = ids.map(String);
    if (dry) {
      // what the close would return, from the position itself (fees not counted)
      const s0 = await read();
      for (const p of c.positions) {
        const per = perLiquidity(sqrtOf(s0.sqrtPriceX96), p.tickLower, p.tickUpper);
        bud0 += BigInt(Math.floor(per.per0 * num(p.liquidity)));
        bud1 += BigInt(Math.floor(per.per1 * num(p.liquidity)));
      }
    } else {
      bud0 = c.received[c0] || 0n;
      bud1 = c.received[c1] || 0n;
      if (c0 === ZERO) {
        const bal = await balanceOf(pr, ZERO, owner);
        const cap = bal > GAS_RESERVE ? bal - GAS_RESERVE : 0n;
        if (bud0 > cap) bud0 = cap;
      }
    }
    if (bud0 <= 0n && bud1 <= 0n) throw new Error("close tidak mengembalikan apa-apa buat dibuka lagi");
  }

  // ---- 2. swap to the mix the new range needs ----------------------------------
  out.stage = "swap";
  let s0 = await read();
  let band = band0(s0);
  const startQuote = inQuote(s0, bud0, bud1);
  const plan = balancePlan(sqrtOf(s0.sqrtPriceX96), band.tickLower, band.tickUpper, bud0, bud1, feeOf(s0, true));
  const plan2 = plan.zeroForOne ? plan
    : balancePlan(sqrtOf(s0.sqrtPriceX96), band.tickLower, band.tickUpper, bud0, bud1, feeOf(s0, false));
  out.plan = { tickLower: band.tickLower, tickUpper: band.tickUpper, actualWidthPct: band.actualWidthPct,
    spacingCoarse: band.spacingCoarse, tick: s0.tick, swapFrac: plan2.frac,
    swapFrom: (plan2.zeroForOne ? c0 : c1) === quote ? quoteSymbol(quote) : "token",
    budgetQuote: startQuote, quoteSymbol: quoteSymbol(quote) };
  if (plan2.frac >= 0.005 && plan2.amountIn > 0n) {
    if (dry && (mode === "rebalance" || wrapPlanned)) {      // the tokens to swap do not exist yet
      out.steps.push({ step: "swap", label: `swap ${(plan2.frac * 100).toFixed(1)}% (direncanakan)`, planned: true });
    } else {
      const sw = await swapInPool({ signer, owner, desc, zeroForOne: plan2.zeroForOne, amount: plan2.amountIn, maxLossPct: slipPct, dry, label: "tight swap" });
      push("swap", sw);
      if (!dry) {
        const got = sw.received || 0n;
        if (plan2.zeroForOne) { bud0 -= plan2.amountIn; bud1 += got; } else { bud1 -= plan2.amountIn; bud0 += got; }
      }
    }
  }
  if (dry) {
    out.steps.push({ step: "mint", label: "mint range (direncanakan)", planned: true });
    done({ ok: true, dryRun: true, mode, steps: out.steps, plan: out.plan, closed: out.closed,
      note: mode === "open" ? "practice: swap disimulasikan, mint direncanakan" : "practice: close disimulasikan, swap + mint direncanakan" });
    process.exit(0);
  }

  // ---- 3. mint ------------------------------------------------------------------
  out.stage = "mint";
  s0 = await read();
  if (!(band.tickLower <= s0.tick && s0.tick < band.tickUpper)) band = band0(s0);   // the swap (or the market) moved the price out
  const sp = sqrtOf(s0.sqrtPriceX96);
  const dl = deadline();
  let r, manager, before;
  const snap = () => Promise.all([balanceOf(pr, c0, owner), balanceOf(pr, c1, owner)]);
  if (desc.dex === "v4") {
    manager = POSITION_MANAGER;
    const m = mintAmounts(sp, band.tickLower, band.tickUpper, bud0, bud1, MINT_BUFFER);
    for (const [tok, amt] of [[c0, bud0], [c1, bud1]]) {
      if (amt <= 0n || tok === ZERO) continue;
      for (const t of await approvalTxs(pr, owner, tok, POSITION_MANAGER, amt)) push("approve", await sendTx(signer, t));
    }
    const tx = tightMintCalldataV4(key, band.tickLower, band.tickUpper, m.liquidity, bud0, bud1, owner, dl);
    before = await snap();
    r = await sendTx(signer, { ...tx, to: manager, label: "tight mint" });
  } else {
    manager = desc.manager;
    for (const [tok, amt] of [[c0, bud0], [c1, bud1]]) {
      if (amt <= 0n) continue;
      for (const t of await erc20Approval(pr, owner, tok, manager, amt, "approve → NPM")) push("approve", await sendTx(signer, t));
    }
    const tx = tightMintCalldataV3(key, band.tickLower, band.tickUpper, bud0, bud1, owner, dl);
    before = await snap();
    r = await sendTx(signer, { ...tx, to: manager, label: "tight mint" });
  }
  push("mint", r);
  const after = await snap();
  const used0 = before[0] - after[0] - (c0 === ZERO ? (r.fee || 0n) : 0n);
  const used1 = before[1] - after[1];
  const depositQuote = inQuote(s0, used0 > 0n ? used0 : 0n, used1 > 0n ? used1 : 0n);
  done({ ok: true, dryRun: false, mode, hash: r.hash, steps: out.steps, plan: out.plan, closed: out.closed,
    band: { tickLower: band.tickLower, tickUpper: band.tickUpper, actualWidthPct: band.actualWidthPct },
    positions: mintedIds(r.logs, owner, manager), manager: manager.toLowerCase(), venue: desc.venue,
    depositQuote, leftoverQuote: Math.max(0, startQuote - depositQuote), quoteSymbol: quoteSymbol(quote) });
} catch (e) { fail(e, { stage: out.stage, steps: out.steps, sent: sent(), closed: out.closed }); }
