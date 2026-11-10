// Encoding + ladder math. No RPC, nothing is sent.
import { planLadder, openCalldataV3, closeCalldataV3, swapCalldataV3Router, openCalldataV4, alignRange, mintAmounts,
  tightMintCalldataV4, P, ZERO, WHYPE, USDC, deadline } from "../lib.mjs";

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
check("tight band matches python (-51, 50)", band.ok && band.tickLower === -51 && band.tickUpper === 50, JSON.stringify(band));
check("tight band contains the tick", band.tickLower <= 0 && 0 < band.tickUpper);
const coarse = alignRange(100, 200, 0.2);
check("coarse spacing is flagged", coarse.ok && coarse.spacingCoarse && coarse.tickLower % 200 === 0);
const both = mintAmounts(0, -50, 50, 1_000_000n, 1_000_000n * 10n ** 12n);
check("two-sided mint uses both tokens", both.amount0 > 0n && both.amount1 > 0n && both.liquidity > 0n);
const tm = tightMintCalldataV4(key, band.tickLower, band.tickUpper, both.liquidity, both.amount0, both.amount1, "0x" + "ab".repeat(20), deadline());
check("tight v4 mint is modifyLiquidities", tm.data.startsWith("0xdd46508f"));

console.log(fails ? `\n${fails} FAILED` : "\nOFFLINE ENCODING OK");
process.exit(fails ? 1 : 0);
