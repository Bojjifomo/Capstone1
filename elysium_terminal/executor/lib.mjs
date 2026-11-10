// Elysium Terminal executor. Chain 99901.
//
// ELYSIUM_PRIVATE_KEY is read here and nowhere else. Python strips RH_PRIVATE_KEY before spawn
// so a Robinhood key cannot sign an Elysium transaction. Every tx is eth_call'd first and
// abandoned if it would revert.
//
// v4 (official Uniswap, confirmed on developers.uniswap.org and on-chain):
//   open   PositionManager.modifyLiquidities: MINT_POSITION x n + SETTLE_PAIR (+ SWEEP for native HYPE)
//   close  BURN_POSITION x n + TAKE_PAIR
//   claim  DECREASE_LIQUIDITY(0) x n + TAKE_PAIR
//   swap   Universal Router 2.2 execute(V4_SWAP) on the pool's own PoolKey
// v3 (ElysiumSwap NPM + SwapRouter, and official Uniswap v3 NPM):
//   open   NPM.multicall(mint x n)  — native HYPE is accepted when the quote is WHYPE, then refundETH
//   close  multicall(decreaseLiquidity + collect + burn) per NFT
//   claim  multicall(collect)
//   swap   ElysiumSwap: SwapRouter.exactInputSingle. Official v3: Universal Router V3_SWAP_EXACT_IN
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { ethers } = require("ethers");

export const CHAIN_ID = 99901n;
export const POOL_MANAGER = "0xE2e105d3F7209A9DA11f83cCf3E7398a753823F1";
export const POSITION_MANAGER = "0x1d1C51b9d07fD27d36412A2b66CC23D19Eb3E518";
export const STATE_VIEW = "0x27eD73aE0652fdCf71d6793412A7525f32bE9dfE";
export const UNIVERSAL_ROUTER = "0x1B341FDD55d56F8E08409A2C25DDFad93Eb03C1a";
export const PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
export const ZERO = ethers.ZeroAddress;
export const USDC = "0xb47c4c8f73f009cc0c0d3f92b388d13e293f0b65";
export const WHYPE = "0xba876237b0f021a6c6d852d6c26e107997cc5e81";
export const V3 = {
  elysiumswap: {
    factory: "0x7266c0EcfF2A687B4Ad0Ead7B24840929540b697",
    npm: "0xC6Dc519451F58dFd27bB2920EeFAe57597bd91f5",
    router: "0x9fae201fa4c0740b1b92DebEcDDfFE74C1Ecd8b3",
    swap: "router",
  },
  "uniswap-v3": {
    factory: "0x1707327f626496a7b5b3872e7e4d2879df7d7a9f",
    npm: "0xf025e0fe9e331a0ef05c2ad3c4e9c64b625cda6f",
    router: null,
    swap: "universal",
  },
};
const QUOTE_RANK = { [USDC]: 2, [ZERO]: 1, [WHYPE]: 1 };
const QUOTE_SYMBOL = { [USDC]: "USDC", [ZERO]: "HYPE", [WHYPE]: "WHYPE" };
export const PUBLIC_RPC = "https://rpc.elysium.kinetiq.xyz";
export const GAS_RESERVE = 10n ** 16n;          // 0.01 HYPE left behind for gas

export const ACT = { DECREASE: 0x01, MINT: 0x02, BURN: 0x03, SWAP_EXACT_IN_SINGLE: 0x06, SETTLE_ALL: 0x0c,
  SETTLE_PAIR: 0x0d, TAKE_ALL: 0x0f, TAKE_PAIR: 0x11, SWEEP: 0x14 };
const UR_V4_SWAP = 0x10;
const UR_V3_SWAP_EXACT_IN = 0x00;
const MAX_U160 = (1n << 160n) - 1n;
const MAX_U48 = (1n << 48n) - 1n;
const MAX_U128 = (1n << 128n) - 1n;
const MIN_SQRT = 4295128739n + 1n;
const MAX_SQRT = 1461446703485210103287273052203988822378723970342n - 1n;

const ab = ethers.AbiCoder.defaultAbiCoder();
const KEY = "tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)";
export const pmIface = new ethers.Interface([
  "function modifyLiquidities(bytes unlockData, uint256 deadline) payable",
  "function getPoolAndPositionInfo(uint256 tokenId) view returns (" + KEY + " poolKey, uint256 info)",
  "function getPositionLiquidity(uint256 tokenId) view returns (uint128)",
  "function poolKeys(bytes25 poolId) view returns (address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)",
  "function ownerOf(uint256 tokenId) view returns (address)",
]);
const svIface = new ethers.Interface([
  "function getSlot0(bytes32 poolId) view returns (uint160 sqrtPriceX96, int24 tick, uint24 protocolFee, uint24 lpFee)",
]);
const urIface = new ethers.Interface(["function execute(bytes commands, bytes[] inputs, uint256 deadline) payable"]);
const erc20 = new ethers.Interface([
  "function balanceOf(address) view returns (uint256)", "function allowance(address,address) view returns (uint256)",
  "function approve(address,uint256) returns (bool)", "function decimals() view returns (uint8)", "function symbol() view returns (string)",
]);
const p2Iface = new ethers.Interface([
  "function allowance(address owner, address token, address spender) view returns (uint160 amount, uint48 expiration, uint48 nonce)",
  "function approve(address token, address spender, uint160 amount, uint48 expiration)",
]);
const v3Pool = new ethers.Interface([
  "function slot0() view returns (uint160 sqrtPriceX96,int24 tick,uint16,uint16,uint16,uint8 feeProtocol,bool)",
  "function token0() view returns (address)", "function token1() view returns (address)",
  "function fee() view returns (uint24)", "function tickSpacing() view returns (int24)", "function factory() view returns (address)",
]);
export const v3Npm = new ethers.Interface([
  "function mint((address token0,address token1,uint24 fee,int24 tickLower,int24 tickUpper,uint256 amount0Desired,uint256 amount1Desired,uint256 amount0Min,uint256 amount1Min,address recipient,uint256 deadline)) payable returns (uint256 tokenId, uint128 liquidity, uint256 amount0, uint256 amount1)",
  "function decreaseLiquidity((uint256 tokenId,uint128 liquidity,uint256 amount0Min,uint256 amount1Min,uint256 deadline)) payable returns (uint256 amount0, uint256 amount1)",
  "function collect((uint256 tokenId,address recipient,uint128 amount0Max,uint128 amount1Max)) payable returns (uint256 amount0, uint256 amount1)",
  "function burn(uint256 tokenId) payable",
  "function multicall(bytes[] data) payable returns (bytes[] results)",
  "function refundETH() payable",
  "function positions(uint256 tokenId) view returns (uint96 nonce,address operator,address token0,address token1,uint24 fee,int24 tickLower,int24 tickUpper,uint128 liquidity,uint256 feeGrowthInside0LastX128,uint256 feeGrowthInside1LastX128,uint128 tokensOwed0,uint128 tokensOwed1)",
  "function ownerOf(uint256 tokenId) view returns (address)",
]);
const v3Router = new ethers.Interface([
  "function exactInputSingle((address tokenIn,address tokenOut,uint24 fee,address recipient,uint256 deadline,uint256 amountIn,uint256 amountOutMinimum,uint160 sqrtPriceLimitX96)) payable returns (uint256 amountOut)",
]);

export function args() {
  const a = {};
  const v = process.argv.slice(2);
  for (let i = 0; i < v.length; i++) {
    if (!v[i].startsWith("--")) continue;
    const k = v[i].slice(2);
    a[k] = v[i + 1] && !v[i + 1].startsWith("--") ? v[++i] : true;
  }
  return a;
}
const stats = { calls: 0, byHost: {} };
export const done = obj => { process.stdout.write(JSON.stringify({ ...obj, rpc: stats }, (k, v) => typeof v === "bigint" ? v.toString() : v) + "\n"); };
export const fail = (e, extra) => done({ ok: false, error: String((e && (e.shortMessage || e.reason || e.message)) || e).slice(0, 400), ...extra });

function counted(url) {
  const host = url.includes("alchemy") ? "alchemy" : new URL(url).hostname;
  const req = new ethers.FetchRequest(url);
  req.setHeader("User-Agent", "elysium-terminal/1");
  req.preflightFunc = async r => { stats.calls++; stats.byHost[host] = (stats.byHost[host] || 0) + 1; return r; };
  return new ethers.JsonRpcProvider(req, Number(CHAIN_ID), { staticNetwork: true, batchMaxCount: 1 });
}
export function tradeUrl() {
  const custom = (process.env.ELYSIUM_RPC_URL || "").trim();
  if (custom) return custom;
  const k = (process.env.ALCHEMY_API_KEY || "").trim();
  if (/^[A-Za-z0-9_-]+$/.test(k)) return `https://elysium-mainnet.g.alchemy.com/v2/${k}`;
  return PUBLIC_RPC;
}
export const readProvider = () => counted(PUBLIC_RPC);
export const tradeProvider = () => counted(tradeUrl());
export async function withRead(fn) {
  try { return await fn(readProvider()); }
  catch (e) {
    if (tradeUrl() === PUBLIC_RPC) throw e;
    return await fn(tradeProvider());
  }
}

export function loadSigner(provider) {
  const raw = String(process.env.ELYSIUM_PRIVATE_KEY || "").trim().replace(/^0x/i, "");
  if (!raw) return null;
  if (!/^[0-9a-fA-F]{64}$/.test(raw)) throw new Error(`ELYSIUM_PRIVATE_KEY must be 64 hex characters (got ${raw.length})`);
  return new ethers.Wallet("0x" + raw, provider);
}

export const isV4 = pool => /^0x[0-9a-fA-F]{64}$/.test(pool || "");
export const quoteSide = k => {
  const r0 = QUOTE_RANK[(k.currency0 || "").toLowerCase()] || 0, r1 = QUOTE_RANK[(k.currency1 || "").toLowerCase()] || 0;
  if (!r0 && !r1) return null;
  return r0 >= r1 ? 0 : 1;
};
export const quoteSymbol = a => QUOTE_SYMBOL[(a || "").toLowerCase()] || "?";
export const poolIdOf = k => ethers.keccak256(ab.encode([KEY], [[k.currency0, k.currency1, k.fee, k.tickSpacing, k.hooks]]));

const INIT_TOPIC = ethers.id("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)");
export async function poolKey(provider, poolId) {
  try {
    const r = await provider.call({ to: POSITION_MANAGER, data: pmIface.encodeFunctionData("poolKeys", [poolId.slice(0, 52)]) });
    const [c0, c1, fee, ts, hooks] = pmIface.decodeFunctionResult("poolKeys", r);
    const k = { currency0: c0.toLowerCase(), currency1: c1.toLowerCase(), fee: Number(fee), tickSpacing: Number(ts), hooks: hooks.toLowerCase() };
    if (!(k.currency0 === ZERO && k.currency1 === ZERO) && poolIdOf(k).toLowerCase() === poolId.toLowerCase()) return k;
  } catch { /* poolKeys only knows pools someone minted through the PositionManager */ }
  const logs = await provider.getLogs({ address: POOL_MANAGER, topics: [INIT_TOPIC, poolId], fromBlock: 1, toBlock: "latest" });
  if (!logs.length) throw new Error("pool key not found (no poolKeys, no Initialize)");
  const lg = logs[logs.length - 1];
  const [fee, ts, hooks] = ab.decode(["uint24", "int24", "address", "uint160", "int24"], lg.data);
  const k = { currency0: ("0x" + lg.topics[2].slice(26)).toLowerCase(), currency1: ("0x" + lg.topics[3].slice(26)).toLowerCase(),
    fee: Number(fee), tickSpacing: Number(ts), hooks: String(hooks).toLowerCase() };
  if (poolIdOf(k).toLowerCase() !== poolId.toLowerCase()) throw new Error("Initialize log does not hash to this pool id");
  return k;
}

async function v3Describe(provider, pool) {
  const [t0, t1, fee, ts, factory] = await Promise.all(["token0", "token1", "fee", "tickSpacing", "factory"].map(async fn => {
    const r = await provider.call({ to: pool, data: v3Pool.encodeFunctionData(fn, []) });
    return v3Pool.decodeFunctionResult(fn, r)[0];
  }));
  const fac = String(factory).toLowerCase();
  const venue = Object.entries(V3).find(([, v]) => v.factory.toLowerCase() === fac);
  if (!venue) throw new Error("pool factory is not ElysiumSwap or official Uniswap v3");
  const [name, spec] = venue;
  const key = { currency0: String(t0).toLowerCase(), currency1: String(t1).toLowerCase(), fee: Number(fee),
    tickSpacing: Number(ts), hooks: ZERO, pool: pool.toLowerCase() };
  return { dex: "v3", venue: name, key, manager: spec.npm, router: spec.router, swap: spec.swap };
}

export async function describePool(provider, pool) {
  const id = String(pool || "");
  if (isV4(id)) {
    const key = await poolKey(provider, id);
    return { dex: "v4", venue: "uniswap-v4", key, manager: POSITION_MANAGER, router: UNIVERSAL_ROUTER, swap: "v4", pool: id.toLowerCase() };
  }
  if (!/^0x[0-9a-fA-F]{40}$/.test(id)) throw new Error("--pool must be a 32-byte v4 id or a 20-byte v3 pool");
  return v3Describe(provider, id);
}

export async function slot0(provider, pool, dex) {
  if (dex === "v4") {
    const r = await provider.call({ to: STATE_VIEW, data: svIface.encodeFunctionData("getSlot0", [pool]) });
    const [sqrtPriceX96, tick, protocolFee, lpFee] = svIface.decodeFunctionResult("getSlot0", r);
    return { sqrtPriceX96, tick: Number(tick), protocolFee: Number(protocolFee), lpFee: Number(lpFee) };
  }
  const r = await provider.call({ to: pool, data: v3Pool.encodeFunctionData("slot0", []) });
  const [sqrtPriceX96, tick, , , , feeProtocol] = v3Pool.decodeFunctionResult("slot0", r);
  return { sqrtPriceX96, tick: Number(tick), protocolFee: Number(feeProtocol), lpFee: null };
}
export async function decimalsOf(provider, token) {
  if (token.toLowerCase() === ZERO) return 18;
  if (token.toLowerCase() === USDC) return 6;
  if (token.toLowerCase() === WHYPE) return 18;
  return Number(erc20.decodeFunctionResult("decimals", await provider.call({ to: token, data: erc20.encodeFunctionData("decimals", []) }))[0]);
}
export async function balanceOf(provider, token, owner) {
  if (token.toLowerCase() === ZERO) return await provider.getBalance(owner);
  return erc20.decodeFunctionResult("balanceOf", await provider.call({ to: token, data: erc20.encodeFunctionData("balanceOf", [owner]) }))[0];
}

const sqrtAt = t => Math.pow(1.0001, t / 2);

// Full width in percent of price, centered, snapped outward so the current tick stays inside.
// Same rounding as elysium_terminal/tight.py align_range.
export function alignRange(currentTick, spacing, widthPct) {
  const sp = Math.abs(Number(spacing)) || 0;
  const width = Number(widthPct);
  if (!sp) return { ok: false, error: "tick spacing tidak ada" };
  if (!(width > 0) || width >= 180) return { ok: false, error: "lebar range harus lebih dari 0 dan di bawah 180%" };
  const half = width / 2 / 100;
  if (half >= 0.9) return { ok: false, error: "lebar range terlalu besar" };
  const ln = Math.log(1.0001);
  const down = Math.log(1 / (1 - half)) / ln;
  const up = Math.log(1 + half) / ln;
  let lo = Math.floor((currentTick - down) / sp) * sp;
  let hi = Math.ceil((currentTick + up) / sp) * sp;
  if (!(lo <= currentTick && currentTick < hi)) {
    if (currentTick < lo) lo = Math.floor(currentTick / sp) * sp;
    if (currentTick >= hi) hi = Math.floor(currentTick / sp) * sp + sp;
  }
  if (hi <= lo) hi = lo + sp;
  const MIN = -887272, MAX = 887272;
  lo = Math.max(lo, Math.ceil(MIN / sp) * sp);
  hi = Math.min(hi, Math.floor(MAX / sp) * sp);
  if (hi <= lo || !(lo <= currentTick && currentTick < hi)) return { ok: false, error: "range tidak muat di grid tick" };
  const actual = (Math.pow(1.0001, hi - lo) - 1) * 100;
  return { ok: true, tickLower: lo, tickUpper: hi, requestedWidthPct: width, actualWidthPct: actual, spacingCoarse: actual > width * 1.5 };
}

// Biggest liquidity that fits both balances. Amounts use the same sqrt(1.0001^tick) unit as planLadder.
export function mintAmounts(tick, lo, hi, bal0, bal1) {
  const sp = sqrtAt(tick), sa = sqrtAt(lo), sb = sqrtAt(hi);
  let per0, per1;
  if (tick < lo) { per0 = (sb - sa) / (sa * sb); per1 = 0; }
  else if (tick >= hi) { per0 = 0; per1 = sb - sa; }
  else { per0 = (sb - sp) / (sp * sb); per1 = sp - sa; }
  const L0 = per0 > 0 ? Number(bal0) / per0 : Infinity;
  const L1 = per1 > 0 ? Number(bal1) / per1 : Infinity;
  const L = Math.min(L0, L1);
  if (!Number.isFinite(L) || L <= 0) throw new Error("saldo tidak cukup buat mint range ini");
  return {
    liquidity: BigInt(Math.max(1, Math.floor(L * (1 - 1e-9)))),
    amount0: BigInt(Math.max(0, Math.floor(per0 * L))),
    amount1: BigInt(Math.max(0, Math.floor(per1 * L))),
  };
}
export const ticksBelow = pct => { const f = Math.min(99, Math.max(0, pct)) / 100; return f <= 0 ? 0 : Math.log(1 / (1 - f)) / Math.log(1.0001); };

// Same ladder as RH Pool: equal-width slices from `top`% to `bottom`% below the price,
// weights 1:2:...:n (deepest slice biggest), single-sided in the deposit currency.
export function planLadder(key, currentTick, depositRaw, depositIsC0, { slices, top = 0, bottom }) {
  const sp = Math.abs(key.tickSpacing) || 1;
  const n = Math.max(1, Math.min(100, Math.floor(slices)));
  const topPct = Math.min(98, Math.max(0, Number(top) || 0));
  const botPct = Math.min(99, Math.max(1, Number(bottom)));
  if (botPct <= topPct) throw new Error("bottom must be further down than top");
  const topTicks = Math.round(ticksBelow(topPct) / sp) * sp;
  const span = Math.max(sp, Math.round((ticksBelow(botPct) - ticksBelow(topPct)) / sp) * sp);
  const start = depositIsC0 ? Math.ceil((currentTick + 1) / sp) * sp + topTicks : Math.floor(currentTick / sp) * sp - topTicks;
  const step = Math.max(sp, Math.round(span / n / sp) * sp);
  const edges = [];
  for (let i = 0; i <= n; i++) edges.push(depositIsC0 ? start + i * step : start - i * step);
  const wTotal = n * (n + 1) / 2;
  const out = [];
  for (let i = 0; i < n; i++) {
    const lo = depositIsC0 ? edges[i] : edges[i + 1];
    const hi = depositIsC0 ? edges[i + 1] : edges[i];
    const amount = BigInt(Math.floor(Number(depositRaw) * (i + 1) / wTotal));
    if (amount <= 0n) continue;
    const sa = sqrtAt(lo), sb = sqrtAt(hi);
    const L = depositIsC0 ? Number(amount) / (1 / sa - 1 / sb) : Number(amount) / (sb - sa);
    if (!Number.isFinite(L) || L <= 0) continue;
    const pct = t => (1 - Math.pow(1.0001, depositIsC0 ? -(t - currentTick) : (t - currentTick))) * 100;
    out.push({ tickLower: lo, tickUpper: hi, amount, liquidity: BigInt(Math.floor(L * (1 - 1e-9))),
      nearPct: pct(depositIsC0 ? lo : hi), farPct: pct(depositIsC0 ? hi : lo) });
  }
  if (!out.length) throw new Error("deposit too small for any slice");
  return { slices: out, step, span, topTicks, realEnd: out[out.length - 1].farPct };
}

const keyTuple = k => [k.currency0, k.currency1, k.fee, k.tickSpacing, k.hooks || ZERO];
const enc = (types, vals) => ab.encode(types, vals);
export const P = {
  mint: (k, lo, hi, L, max0, max1, owner) => enc([KEY, "int24", "int24", "uint256", "uint128", "uint128", "address", "bytes"],
    [keyTuple(k), lo, hi, L, max0, max1, owner, "0x"]),
  burn: (id, min0 = 0n, min1 = 0n) => enc(["uint256", "uint128", "uint128", "bytes"], [id, min0, min1, "0x"]),
  decrease: (id, L, min0 = 0n, min1 = 0n) => enc(["uint256", "uint256", "uint128", "uint128", "bytes"], [id, L, min0, min1, "0x"]),
  settlePair: (c0, c1) => enc(["address", "address"], [c0, c1]),
  takePair: (c0, c1, to) => enc(["address", "address", "address"], [c0, c1, to]),
  sweep: (c, to) => enc(["address", "address"], [c, to]),
};
export function modifyLiquidities(actions, params, deadline) {
  const unlock = enc(["bytes", "bytes[]"], [ethers.hexlify(Uint8Array.from(actions)), params]);
  return pmIface.encodeFunctionData("modifyLiquidities", [unlock, deadline]);
}
export const deadline = (min = 20) => BigInt(Math.floor(Date.now() / 1000) + min * 60);

export function openCalldataV4(key, plan, depositIsC0, owner, dl) {
  const acts = [], params = [];
  for (const s of plan.slices) {
    acts.push(ACT.MINT);
    params.push(P.mint(key, s.tickLower, s.tickUpper, s.liquidity, depositIsC0 ? s.amount : 0n, depositIsC0 ? 0n : s.amount, owner));
  }
  acts.push(ACT.SETTLE_PAIR);
  params.push(P.settlePair(key.currency0, key.currency1));
  const native = key.currency0.toLowerCase() === ZERO && depositIsC0;
  if (native) { acts.push(ACT.SWEEP); params.push(P.sweep(ZERO, owner)); }
  const value = native ? plan.slices.reduce((a, s) => a + s.amount, 0n) : 0n;
  return { data: modifyLiquidities(acts, params, dl), value };
}
export function tightMintCalldataV4(key, lo, hi, liquidity, amount0, amount1, owner, dl) {
  const acts = [ACT.MINT, ACT.SETTLE_PAIR];
  const params = [P.mint(key, lo, hi, liquidity, amount0, amount1, owner), P.settlePair(key.currency0, key.currency1)];
  const value = key.currency0.toLowerCase() === ZERO ? amount0 : 0n;
  return { data: modifyLiquidities(acts, params, dl), value };
}
export function tightMintCalldataV3(key, lo, hi, amount0, amount1, owner, dl) {
  const mint = v3Npm.encodeFunctionData("mint", [[
    key.currency0, key.currency1, key.fee, lo, hi, amount0, amount1, 0, 0, owner, dl,
  ]]);
  return { data: v3Npm.encodeFunctionData("multicall", [[mint]]), value: 0n };
}
export function closeCalldataV4(positions, recipient, dl, claimOnly = false) {
  const byPool = new Map();
  for (const p of positions) {
    const id = poolIdOf(p.key);
    if (!byPool.has(id)) byPool.set(id, { key: p.key, ps: [] });
    byPool.get(id).ps.push(p);
  }
  const acts = [], params = [];
  for (const g of byPool.values()) {
    for (const p of g.ps) {
      acts.push(claimOnly ? ACT.DECREASE : ACT.BURN);
      params.push(claimOnly ? P.decrease(p.tokenId, 0n) : P.burn(p.tokenId));
    }
    acts.push(ACT.TAKE_PAIR);
    params.push(P.takePair(g.key.currency0, g.key.currency1, recipient));
  }
  return modifyLiquidities(acts, params, dl);
}
export function swapCalldataV4(key, zeroForOne, amountIn, minOut, dl) {
  const cin = zeroForOne ? key.currency0 : key.currency1, cout = zeroForOne ? key.currency1 : key.currency0;
  const params = [
    enc(["tuple(" + KEY + " poolKey,bool zeroForOne,uint128 amountIn,uint128 amountOutMinimum,bytes hookData)"],
      [[keyTuple(key), zeroForOne, amountIn, minOut, "0x"]]),
    enc(["address", "uint256"], [cin, amountIn]),
    enc(["address", "uint256"], [cout, minOut]),
  ];
  const input = enc(["bytes", "bytes[]"], [ethers.hexlify(Uint8Array.from([ACT.SWAP_EXACT_IN_SINGLE, ACT.SETTLE_ALL, ACT.TAKE_ALL])), params]);
  return { data: urIface.encodeFunctionData("execute", [ethers.hexlify(Uint8Array.from([UR_V4_SWAP])), [input], dl]),
    value: cin.toLowerCase() === ZERO ? amountIn : 0n };
}

export function openCalldataV3(key, plan, depositIsC0, owner, dl, native) {
  const calls = plan.slices.map(s => v3Npm.encodeFunctionData("mint", [[
    key.currency0, key.currency1, key.fee, s.tickLower, s.tickUpper,
    depositIsC0 ? s.amount : 0n, depositIsC0 ? 0n : s.amount, 0, 0, owner, dl,
  ]]));
  const value = native ? plan.slices.reduce((a, s) => a + s.amount, 0n) : 0n;
  if (value > 0n) calls.push(v3Npm.encodeFunctionData("refundETH", []));
  return { data: v3Npm.encodeFunctionData("multicall", [calls]), value };
}
export function closeCalldataV3(positions, recipient, dl, claimOnly = false) {
  const calls = [];
  for (const p of positions) {
    if (!claimOnly && p.liquidity > 0n) {
      calls.push(v3Npm.encodeFunctionData("decreaseLiquidity", [[p.tokenId, p.liquidity, 0, 0, dl]]));
    }
    calls.push(v3Npm.encodeFunctionData("collect", [[p.tokenId, recipient, MAX_U128, MAX_U128]]));
    if (!claimOnly) calls.push(v3Npm.encodeFunctionData("burn", [p.tokenId]));
  }
  return v3Npm.encodeFunctionData("multicall", [calls]);
}
export function swapCalldataV3Router(key, tokenIn, tokenOut, amountIn, minOut, recipient, dl) {
  const zeroForOne = tokenIn.toLowerCase() === key.currency0.toLowerCase();
  const limit = zeroForOne ? MIN_SQRT : MAX_SQRT;
  const data = v3Router.encodeFunctionData("exactInputSingle", [[tokenIn, tokenOut, key.fee, recipient, dl, amountIn, minOut, limit]]);
  const value = tokenIn.toLowerCase() === WHYPE ? 0n : 0n;
  return { data, value, to: null };
}
export function swapCalldataV3Universal(key, tokenIn, tokenOut, amountIn, minOut, recipient, dl) {
  const path = ethers.solidityPacked(["address", "uint24", "address"], [tokenIn, key.fee, tokenOut]);
  const input = ab.encode(["address", "uint256", "uint256", "bytes", "bool"], [recipient, amountIn, minOut, path, true]);
  return { data: urIface.encodeFunctionData("execute", [ethers.hexlify(Uint8Array.from([UR_V3_SWAP_EXACT_IN])), [input], dl]), value: 0n };
}

export const rawPrice01 = sqrtX96 => Math.pow(Number(sqrtX96) / 2 ** 96, 2);
export function v3LpFee(fee, feeProtocol, zeroForOne) {
  const pf = zeroForOne ? Number(feeProtocol) & 0xf : (Number(feeProtocol) >> 4) & 0xf;
  if (!pf) return Number(fee);
  return Number(fee) - Math.floor(Number(fee) / pf);
}

export async function approvalTxs(provider, owner, token, spender, amount) {
  if (!token || token.toLowerCase() === ZERO) return [];
  const out = [];
  const a = erc20.decodeFunctionResult("allowance", await provider.call({ to: token, data: erc20.encodeFunctionData("allowance", [owner, PERMIT2]) }))[0];
  if (a < amount) out.push({ label: "approve token → Permit2", to: token, data: erc20.encodeFunctionData("approve", [PERMIT2, ethers.MaxUint256]) });
  const [amt, exp] = p2Iface.decodeFunctionResult("allowance", await provider.call({ to: PERMIT2, data: p2Iface.encodeFunctionData("allowance", [owner, token, spender]) }));
  const now = BigInt(Math.floor(Date.now() / 1000));
  if (amt < amount || (exp !== 0n && exp < now)) {
    out.push({ label: "Permit2 → spender", to: PERMIT2, data: p2Iface.encodeFunctionData("approve", [token, spender, MAX_U160, MAX_U48]) });
  }
  return out;
}
export async function erc20Approval(provider, owner, token, spender, amount, label) {
  if (!token || token.toLowerCase() === ZERO) return [];
  const a = erc20.decodeFunctionResult("allowance", await provider.call({ to: token, data: erc20.encodeFunctionData("allowance", [owner, spender]) }))[0];
  if (a >= amount) return [];
  return [{ label: label || "approve token", to: token, data: erc20.encodeFunctionData("approve", [spender, ethers.MaxUint256]) }];
}

export async function sendTx(signer, { to, data, value = 0n, label = "tx" }, { dryRun = false, from } = {}) {
  const provider = signer ? signer.provider : tradeProvider();
  const sender = from || (signer && signer.address);
  try {
    await provider.call({ from: sender, to, data, value });
  } catch (e) {
    throw new Error(`${label}: would revert, not sent (${String(e.shortMessage || e.reason || e.message).slice(0, 180)})`);
  }
  if (dryRun) return { label, simulated: true };
  const gas = (await provider.estimateGas({ from: sender, to, data, value })) * 13n / 10n;
  const tx = await signer.sendTransaction({ to, data, value, gasLimit: gas, chainId: CHAIN_ID });
  const rc = await tx.wait(1, 180_000);
  if (!rc || rc.status !== 1) throw new Error(`${label}: reverted on chain ${tx.hash}`);
  return { label, hash: tx.hash, gasUsed: rc.gasUsed, fee: rc.gasUsed * (rc.gasPrice || 0n), logs: rc.logs };
}

export function mintedIds(logs, owner, manager) {
  const T = ethers.id("Transfer(address,address,uint256)");
  const m = (manager || POSITION_MANAGER).toLowerCase();
  return (logs || []).filter(l => (l.address || "").toLowerCase() === m && l.topics[0] === T
    && BigInt(l.topics[1]) === 0n && ("0x" + l.topics[2].slice(26)).toLowerCase() === owner.toLowerCase()).map(l => BigInt(l.topics[3]).toString());
}

export async function positionInfo(provider, tokenId, dex, manager) {
  if (dex === "v3") {
    const r = await provider.call({ to: manager, data: v3Npm.encodeFunctionData("positions", [tokenId]) });
    const d = v3Npm.decodeFunctionResult("positions", r);
    const owner = v3Npm.decodeFunctionResult("ownerOf", await provider.call({ to: manager, data: v3Npm.encodeFunctionData("ownerOf", [tokenId]) }))[0];
    const key = { currency0: String(d[2]).toLowerCase(), currency1: String(d[3]).toLowerCase(), fee: Number(d[4]), tickSpacing: 0, hooks: ZERO };
    return { tokenId: BigInt(tokenId), key, owner: String(owner).toLowerCase(), liquidity: d[7],
      tickLower: Number(d[5]), tickUpper: Number(d[6]), dex: "v3", manager };
  }
  const [k, info] = pmIface.decodeFunctionResult("getPoolAndPositionInfo",
    await provider.call({ to: POSITION_MANAGER, data: pmIface.encodeFunctionData("getPoolAndPositionInfo", [tokenId]) }));
  const liq = pmIface.decodeFunctionResult("getPositionLiquidity",
    await provider.call({ to: POSITION_MANAGER, data: pmIface.encodeFunctionData("getPositionLiquidity", [tokenId]) }))[0];
  const owner = pmIface.decodeFunctionResult("ownerOf",
    await provider.call({ to: POSITION_MANAGER, data: pmIface.encodeFunctionData("ownerOf", [tokenId]) }))[0];
  const key = { currency0: k[0].toLowerCase(), currency1: k[1].toLowerCase(), fee: Number(k[2]), tickSpacing: Number(k[3]), hooks: k[4].toLowerCase() };
  const s24 = x => (x & 0x800000n ? x - 0x1000000n : x);
  return { tokenId: BigInt(tokenId), key, poolId: poolIdOf(key), owner: owner.toLowerCase(), liquidity: liq,
    tickLower: Number(s24((info >> 8n) & 0xffffffn)), tickUpper: Number(s24((info >> 32n) & 0xffffffn)), dex: "v4", manager: POSITION_MANAGER };
}

export function minOutFor(amount, zeroForOne, sqrtPriceX96, feeFrac, maxLossPct) {
  const p01 = rawPrice01(sqrtPriceX96);
  const expected = zeroForOne ? Number(amount) * p01 : Number(amount) / p01;
  const minOut = BigInt(Math.max(0, Math.floor(expected * (1 - feeFrac) * (1 - maxLossPct / 100))));
  return { expected, minOut };
}

export { ethers };
