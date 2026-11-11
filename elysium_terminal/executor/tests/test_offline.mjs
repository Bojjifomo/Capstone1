// Encoding + ladder math. No RPC, nothing is sent.
import { planLadder, planSame, openCalldataV3, closeCalldataV3, swapCalldataV3Router, openCalldataV4, alignRange, mintAmounts, balancePlan,
  perLiquidity, tightMintCalldataV4, P, ZERO, WHYPE, USDC, deadline } from "../lib.mjs";

let fails = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "ok" : "FAIL"} ${name}${detail ? "  " + detail : ""}`);
  if (!ok) fails++;
};

const key = { currency0: USDC, currency1: WHYPE, fee: 10000, tickSpacing: 200, hooks: ZERO };
const plan = planLadder(key, 230000, 400n * 10n ** 6n, true, { slices: 5, top: 0, bottom: 60 });
check("5 slices", plan.slices.length === 5, String(plan.slices.length));
const amounts = plan.slices.map(s => Number(s.amount));
check("weights 1:2:3:4:5", amounts.every((x, i) => Math.abs(x / amounts[0] - (i + 1)) < 0.02), amounts.map(x => x.toFixed(0)).join(","));
check("on the tick grid", plan.slices.every(s => s.tickLower % 200 === 0 && s.tickUpper % 200 === 0));
check("quote is currency0, so the ladder sits above the tick", plan.slices.every(s => s.tickLower > 230000));
check("reaches about -60%", plan.realEnd > 55 && plan.realEnd < 70, plan.realEnd.toFixed(1));

const open = openCalldataV3(key, plan, true, "0x" + "ab".repeat(20), deadline(), false);
check("v3 open is a multicall", open.data.startsWith("0xac9650d8"));
check("v3 open contains mint selector", open.data.includes("88316456"));
check("no native value when paying USDC", open.value === 0n);

const native = openCalldataV3({ ...key, currency0: WHYPE, currency1: "0x" + "cd".repeat(20) }, plan, true, "0x" + "ab".repeat(20), deadline(), true);
check("native WHYPE mint sets value and refundETH", native.value > 0n && native.data.includes("12210e8a"));

const close = closeCalldataV3([{ tokenId: 7n, liquidity: 100n }], "0x" + "ab".repeat(20), deadline(), false);
check("close has decrease, collect, burn", ["0c49ccbe", "fc6f7865", "42966c68"].every(s => close.includes(s)));
const claim = closeCalldataV3([{ tokenId: 7n, liquidity: 100n }], "0x" + "ab".repeat(20), deadline(), true);
check("claim is collect only", claim.includes("fc6f7865") && !claim.includes("42966c68") && !claim.includes("0c49ccbe"));

const swap = swapCalldataV3Router(key, WHYPE, USDC, 10n ** 15n, 1n, "0x" + "ab".repeat(20), deadline());
check("SwapRouter exactInputSingle selector", swap.data.startsWith("0x414bf389"));

const v4 = openCalldataV4({ currency0: ZERO, currency1: USDC, fee: 3000, tickSpacing: 60, hooks: ZERO }, plan, true, "0x" + "ab".repeat(20), deadline());
check("v4 open is modifyLiquidities", v4.data.startsWith("0xdd46508f"));
check("v4 mint param helper is hex", P.mint(key, -200, 200, 1n, 1n, 0n, ZERO).startsWith("0x"));

const band = alignRange(0, 1, 1);
check("tight band matches python (-50, 50)", band.ok && band.tickLower === -50 && band.tickUpper === 50, JSON.stringify(band));
check("tight band contains the tick", band.tickLower <= 0 && 0 < band.tickUpper);
const near = alignRange(53314, 60, 2);
check("coarse grid snaps to the nearest lines, like python", near.tickLower === 53220 && near.tickUpper === 53400 && !near.spacingCoarse, JSON.stringify(near));
const coarse = alignRange(100, 200, 0.2);
check("coarse spacing is flagged", coarse.ok && coarse.spacingCoarse && coarse.tickLower % 200 === 0);
const both = mintAmounts(1, -50, 50, 1_000_000n, 1_000_000n * 10n ** 12n);
check("two-sided mint uses both tokens", both.amount0 > 0n && both.amount1 > 0n && both.liquidity > 0n);
const buffered = mintAmounts(1, -50, 50, 1_000_000n, 1_000_000n, 0.005);
const exact = mintAmounts(1, -50, 50, 1_000_000n, 1_000_000n, 0);
check("buffer leaves room for a price move", buffered.liquidity < exact.liquidity && buffered.amount0 <= 1_000_000n && buffered.amount1 <= 1_000_000n);

// balancePlan: start with one token only, end with the mix the range needs
const sp = 1.0001 ** (10 / 2);                       // price a little above the middle of [-50, 50)
for (const [b0, b1, name] of [[1e9, 0, "only token0"], [0, 1e9, "only token1"], [4e8, 9e8, "both"]]) {
  const bp = balancePlan(sp, -50, 50, BigInt(b0), BigInt(b1), 0.003);
  const x = Number(bp.amountIn), p = sp * sp;
  const a0 = bp.zeroForOne ? b0 - x : b0 + x * 0.997 / p;
  const a1 = bp.zeroForOne ? b1 + x * p * 0.997 : b1 - x;
  const { per0, per1 } = perLiquidity(sp, -50, 50);
  check(`balance plan (${name}) lands on the range's mix`, Math.abs(a1 / a0 - per1 / per0) / (per1 / per0) < 1e-6 && bp.frac > 0 && bp.frac < 1,
    `${bp.zeroForOne ? "0→1" : "1→0"} ${(bp.frac * 100).toFixed(1)}%`);
  const m = mintAmounts(sp, -50, 50, BigInt(Math.floor(a0)), BigInt(Math.floor(a1)));
  check(`  and the mint uses nearly all of it (${name})`, Number(m.amount0) > a0 * 0.999 && Number(m.amount1) > a1 * 0.999);
}
const below = balancePlan(1.0001 ** (-100 / 2), -50, 50, 0n, 10n ** 9n, 0.003);
check("price below the range: everything goes to token0", !below.zeroForOne && below.amountIn === 10n ** 9n);

const tm = tightMintCalldataV4(key, band.tickLower, band.tickUpper, both.liquidity, both.amount0, both.amount1, "0x" + "ab".repeat(20), deadline());
check("tight v4 mint is modifyLiquidities", tm.data.startsWith("0xdd46508f") && tm.value === 0n);
const tmNative = tightMintCalldataV4({ ...key, currency0: ZERO }, band.tickLower, band.tickUpper, both.liquidity, 123n, both.amount1, "0x" + "ab".repeat(20), deadline());
check("native tight mint sends the max as value and adds a third action (sweep)", tmNative.value === 123n && tmNative.data.length > tm.data.length);

// re-entry keeps the old shape: same widths, same split, quote-only, next to the new price
const olds = [{ tickLower: 53400, tickUpper: 53520, liquidity: 3n * 10n ** 20n }, { tickLower: 53520, tickUpper: 53760, liquidity: 10n ** 20n }];
const k60 = { currency0: ZERO, currency1: "0x" + "cd".repeat(20), fee: 3000, tickSpacing: 60, hooks: ZERO };
const same = planSame(k60, 54011, 10n ** 18n, true, olds);
check("re-entry keeps each width", same.slices.map(x => x.tickUpper - x.tickLower).join() === "120,240", JSON.stringify(same.slices.map(x => [x.tickLower, x.tickUpper])));
check("re-entry starts just past the price and stays one-sided", same.slices[0].tickLower === 54060 && same.slices[1].tickLower === same.slices[0].tickUpper);
const w0 = 3 * (1 / 1.0001 ** (53400 / 2) - 1 / 1.0001 ** (53520 / 2)), w1 = 1 / 1.0001 ** (53520 / 2) - 1 / 1.0001 ** (53760 / 2);
check("re-entry keeps the split between ranges", Math.abs(Number(same.slices[0].amount) / 1e18 - w0 / (w0 + w1)) < 1e-6);
check("re-entry spends no more than came back", same.slices.reduce((a, x) => a + x.amount, 0n) <= 10n ** 18n);
const sameQ1 = planSame({ ...k60, currency0: "0x" + "cd".repeat(20), currency1: WHYPE }, 54011, 10n ** 18n, false, [olds[0]]);
check("quote is token1: the range sits below the tick", sameQ1.slices[0].tickUpper === 54000 && sameQ1.slices[0].tickLower === 53880);

console.log(fails ? `\n${fails} FAILED` : "\nOFFLINE ENCODING OK");
process.exit(fails ? 1 : 0);
