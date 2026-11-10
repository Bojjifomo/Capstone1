// One tight-range step. Each invocation is a single transaction (or a simulation of one).
//   node tight.mjs --pool 0x.. --step swap|mint --usd 100 --quote-usd 1 --swap-frac 0.5
//                  --slippage-bps 100 --lower -50 --upper 50 [--dry-run --owner 0x..]
// A hooked pool or a dynamic-fee pool returns unsupported and sends nothing.
// Close stays in close.mjs. This file never chains swap and mint.
import { args, done, fail, tradeProvider, loadSigner, describePool, slot0, quoteSide, quoteSymbol, decimalsOf,
  balanceOf, alignRange, mintAmounts, approvalTxs, erc20Approval, sendTx, deadline, minOutFor, v3LpFee,
  swapCalldataV4, swapCalldataV3Router, swapCalldataV3Universal, tightMintCalldataV4, tightMintCalldataV3,
  mintedIds, UNIVERSAL_ROUTER, POSITION_MANAGER, ZERO, ethers } from "./lib.mjs";

const DYNAMIC = 0x800000;
const a = args();

function unsupported(error) {
  done({ ok: false, unsupported: true, error });
  process.exit(0);
}

try {
  const dry = !!a["dry-run"];
  const signer = dry ? null : loadSigner(tradeProvider());
  if (!dry && !signer) throw new Error("no ELYSIUM_PRIVATE_KEY in .env");
  const owner = (dry ? String(a.owner || "") : signer.address).toLowerCase();
  if (!ethers.isAddress(owner)) throw new Error("--owner needed for --dry-run");
  const step = String(a.step || "");
  if (step !== "swap" && step !== "mint") throw new Error("--step swap or mint");
  const pr = signer ? signer.provider : tradeProvider();
  const desc = await describePool(pr, String(a.pool || ""));
  const key = desc.key;
  if ((key.hooks || ZERO).toLowerCase() !== ZERO) unsupported("pool ber-hook. Tight swap/mint tidak didukung, tidak dikirim.");
  if ((Number(key.fee) & DYNAMIC) !== 0) unsupported("fee dinamis. Tight swap/mint tidak didukung, tidak dikirim.");
  const q = quoteSide(key);
  if (q === null) throw new Error("bukan pool USDC / HYPE");
  const quote = (q === 0 ? key.currency0 : key.currency1).toLowerCase();
  const token = (q === 0 ? key.currency1 : key.currency0).toLowerCase();
  const poolAddr = desc.dex === "v4" ? desc.pool : key.pool;
  const s0 = await slot0(pr, poolAddr, desc.dex);
  const lower = a.lower != null ? Number(a.lower) : null;
  const upper = a.upper != null ? Number(a.upper) : null;
  if (lower != null && upper != null && !(lower <= s0.tick && s0.tick < upper)) {
    throw new Error("harga sudah geser, range yang di-review tidak lagi nge-cover harga. Review ulang. Tidak dikirim.");
  }
  const slipPct = Math.max(0.01, Number(a["slippage-bps"] || 100) / 100);

  if (step === "swap") {
    const frac = Number(a["swap-frac"] || 0);
    const usd = Number(a.usd);
    const qUsd = Number(a["quote-usd"]);
    if (!(frac > 0) || !(usd > 0) || !(qUsd > 0)) {
      done({ ok: true, dryRun: true, simulated: true, skipped: true, note: "tidak ada swap yang perlu" });
      process.exit(0);
    }
    const dec = await decimalsOf(pr, quote);
    const amountIn = BigInt(Math.floor(usd * frac / qUsd * 10 ** dec));
    if (amountIn <= 0n) {
      done({ ok: true, dryRun: true, simulated: true, skipped: true, note: "swap lebih kecil dari 1 unit" });
      process.exit(0);
    }
    const zeroForOne = quote === key.currency0.toLowerCase();
    let feeFrac;
    if (desc.dex === "v4") {
      const pf = zeroForOne ? s0.protocolFee & 0xfff : (s0.protocolFee >> 12) & 0xfff;
      feeFrac = ((s0.lpFee || key.fee || 0) + pf) / 1e6;
    } else {
      feeFrac = v3LpFee(key.fee, s0.protocolFee, zeroForOne) / 1e6;
    }
    const { minOut } = minOutFor(amountIn, zeroForOne, s0.sqrtPriceX96, feeFrac, slipPct);
    if (minOut <= 0n) throw new Error("swap terlalu kecil buat dihitung minOut");
    const steps = [];
    const dl = deadline();
    let tx, to;
    if (desc.dex === "v4") {
      for (const t of await approvalTxs(pr, owner, quote, UNIVERSAL_ROUTER, amountIn)) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
      tx = swapCalldataV4(key, zeroForOne, amountIn, minOut, dl);
      to = UNIVERSAL_ROUTER;
    } else if (desc.swap === "router") {
      for (const t of await erc20Approval(pr, owner, quote, desc.router, amountIn, "approve quote → SwapRouter")) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
      tx = swapCalldataV3Router(key, quote, token, amountIn, minOut, owner, dl);
      to = desc.router;
    } else {
      for (const t of await approvalTxs(pr, owner, quote, UNIVERSAL_ROUTER, amountIn)) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
      tx = swapCalldataV3Universal(key, quote, token, amountIn, minOut, owner, dl);
      to = UNIVERSAL_ROUTER;
    }
    if (dry && steps.some(s => s.needed)) {
      done({ ok: true, dryRun: true, steps, note: "approval belum ada, swap-nya sendiri belum disimulasikan" });
      process.exit(0);
    }
    const r = await sendTx(signer, { ...tx, to, label: "tight swap" }, { dryRun: dry, from: owner });
    steps.push(r);
    done({ ok: true, dryRun: dry, simulated: !!r.simulated, hash: r.hash, steps, minOut: minOut.toString(), quote: quoteSymbol(quote) });
    process.exit(0);
  }

  const band = (lower != null && upper != null)
    ? { ok: true, tickLower: lower, tickUpper: upper }
    : alignRange(s0.tick, key.tickSpacing, Number(a.width || 1));
  if (!band.ok) throw new Error(band.error || "range gagal");
  const [b0, b1] = await Promise.all([balanceOf(pr, key.currency0, owner), balanceOf(pr, key.currency1, owner)]);
  const amounts = mintAmounts(s0.tick, band.tickLower, band.tickUpper, b0, b1);
  const steps = [];
  const dl = deadline();
  if (desc.dex === "v4") {
    for (const [tok, amt] of [[key.currency0, amounts.amount0], [key.currency1, amounts.amount1]]) {
      if (amt <= 0n || tok.toLowerCase() === ZERO) continue;
      for (const t of await approvalTxs(pr, owner, tok, POSITION_MANAGER, amt)) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
    }
    const tx = tightMintCalldataV4(key, band.tickLower, band.tickUpper, amounts.liquidity, amounts.amount0, amounts.amount1, owner, dl);
    if (dry && steps.some(s => s.needed)) {
      done({ ok: true, dryRun: true, steps, band, note: "approval belum ada, mint-nya sendiri belum disimulasikan" });
      process.exit(0);
    }
    const r = await sendTx(signer, { ...tx, to: POSITION_MANAGER, label: "tight mint" }, { dryRun: dry, from: owner });
    steps.push(r);
    done({ ok: true, dryRun: dry, simulated: !!r.simulated, hash: r.hash, steps, band,
      positions: r.logs ? mintedIds(r.logs, owner, POSITION_MANAGER) : [] });
    process.exit(0);
  }
  const npm = desc.manager;
  for (const [tok, amt] of [[key.currency0, amounts.amount0], [key.currency1, amounts.amount1]]) {
    if (amt <= 0n || tok.toLowerCase() === ZERO) continue;
    for (const t of await erc20Approval(pr, owner, tok, npm, amt, "approve → NPM")) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
  }
  const tx = tightMintCalldataV3(key, band.tickLower, band.tickUpper, amounts.amount0, amounts.amount1, owner, dl);
  if (dry && steps.some(s => s.needed)) {
    done({ ok: true, dryRun: true, steps, band, note: "approval belum ada, mint-nya sendiri belum disimulasikan" });
    process.exit(0);
  }
  const r = await sendTx(signer, { ...tx, to: npm, label: "tight mint" }, { dryRun: dry, from: owner });
  steps.push(r);
  done({ ok: true, dryRun: dry, simulated: !!r.simulated, hash: r.hash, steps, band,
    positions: r.logs ? mintedIds(r.logs, owner, npm) : [] });
} catch (e) { fail(e); process.exit(1); }
