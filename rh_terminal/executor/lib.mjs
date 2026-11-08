// RH Terminal executor: Uniswap V4 on Robinhood Chain (4663), signed with the key in .env.
//
// The key (RH_PRIVATE_KEY, 64 hex) is read here and nowhere else: Python and the browser only
// ever get the address back. Every transaction is simulated with eth_call first and abandoned
// if it reverts, so a failing action costs nothing.
//
// Encodings follow RH Pool / RH Portfolio (verified against mined transactions there), built
// with ethers' ABI coder instead of hand-written hex:
//   open     PositionManager.modifyLiquidities: MINT_POSITION x n + SETTLE_PAIR (+ SWEEP for ETH)
//   close    BURN_POSITION x n + TAKE_PAIR per pool
//   claim    DECREASE_LIQUIDITY(0) x n + TAKE_PAIR per pool
//   swap     UniversalRouter.execute(V4_SWAP): SWAP_EXACT_IN_SINGLE + SETTLE_ALL + TAKE_ALL on an
//            exact PoolKey (decoded from mined router swaps on this chain)
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { ethers } = require("ethers");

export const CHAIN_ID = 4663n;
export const POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951";
export const POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7";
export const STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b";
export const UNIVERSAL_ROUTER = "0x8876789976dEcBfCbBbe364623C63652db8C0904";
export const PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
export const ZERO = ethers.ZeroAddress;
export const USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168";
export const WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73";
const QUOTE_RANK = { [USDG]: 2, [ZERO]: 1, [WETH]: 1 };
// The chain's own node and PublicNode first; Daybreak and dRPC for networks that block those two
// (seen behind Indonesian ISPs: connection refused / HTTP 403).
export const PUBLIC_RPCS = ["https://rpc.mainnet.chain.robinhood.com", "https://robinhood-rpc.publicnode.com",
  "https://robinhood.daybreak-rpc.network", "https://robinhood.drpc.org"];

export const ACT = { DECREASE: 0x01, MINT: 0x02, BURN: 0x03, SWAP_EXACT_IN_SINGLE: 0x06, SETTLE_ALL: 0x0c,
  SETTLE_PAIR: 0x0d, TAKE_ALL: 0x0f, TAKE_PAIR: 0x11, CLOSE_CURRENCY: 0x12, SWEEP: 0x14 };
const UR_V4_SWAP = 0x10;
const MAX_U160 = (1n << 160n) - 1n;
const MAX_U48 = (1n << 48n) - 1n;

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

// ------------------------------------------------------------------ io ----
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

// ------------------------------------------------------------------ rpc ----
function counted(url) {
  const host = url.includes("alchemy") ? "alchemy" : new URL(url).hostname;
  const req = new ethers.FetchRequest(url);
  req.preflightFunc = async r => { stats.calls++; stats.byHost[host] = (stats.byHost[host] || 0) + 1; return r; };
  return new ethers.JsonRpcProvider(req, Number(CHAIN_ID), { staticNetwork: true, batchMaxCount: 1 });
}
export function keyedUrl() {
  const custom = (process.env.RH_RPC_URL || "").trim();
  if (custom) return custom;
  const k = (process.env.ALCHEMY_API_KEY || "").trim();
  if (/^[A-Za-z0-9_-]+$/.test(k)) return `https://robinhood-mainnet.g.alchemy.com/v2/${k}`;
  return null;
}
export const tradeUrl = () => keyedUrl() || PUBLIC_RPCS[0];
// Reads: the public nodes in turn, the keyed one last. Sends: the keyed one (Alchemy estimates gas
// reliably); without a key, the first public node that answers.
export const readProvider = () => counted(PUBLIC_RPCS[0]);
let _tradeUrl = null;
export function tradeProvider() { return counted(_tradeUrl || tradeUrl()); }
export async function pickTradeProvider() {
  if (keyedUrl()) return tradeProvider();
  for (const u of PUBLIC_RPCS) {
    try { await counted(u).getBlockNumber(); _tradeUrl = u; return counted(u); } catch (e) {}
  }
  return tradeProvider();
}
export async function withRead(fn) {
  let last;
  for (const u of [...PUBLIC_RPCS, keyedUrl()].filter(Boolean)) {
    try { return await fn(counted(u)); } catch (e) { last = e; if (e && /revert|CALL_EXCEPTION/i.test(String(e.code || e.message))) throw e; }
  }
  throw last;
}

// ------------------------------------------------------------------ key ----
export function loadSigner(provider) {
  const raw = String(process.env.RH_PRIVATE_KEY || process.env.PRIVATE_KEY || "").trim().replace(/^0x/i, "");
  if (!raw) return null;
  if (!/^[0-9a-fA-F]{64}$/.test(raw)) throw new Error(`RH_PRIVATE_KEY must be 64 hex characters (got ${raw.length})`);
  return new ethers.Wallet("0x" + raw, provider);
}

// ------------------------------------------------------------------ reads ----
export const quoteSide = k => {
  const r0 = QUOTE_RANK[k.currency0.toLowerCase()] || 0, r1 = QUOTE_RANK[k.currency1.toLowerCase()] || 0;
  if (!r0 && !r1) return null;
  return r0 >= r1 ? 0 : 1;
};
export const poolIdOf = k => ethers.keccak256(ab.encode([KEY], [[k.currency0, k.currency1, k.fee, k.tickSpacing, k.hooks]]));

export async function poolKey(provider, poolId) {
  const r = await provider.call({ to: POSITION_MANAGER, data: pmIface.encodeFunctionData("poolKeys", [poolId.slice(0, 52)]) });
  const [c0, c1, fee, ts, hooks] = pmIface.decodeFunctionResult("poolKeys", r);
  const k = { currency0: c0.toLowerCase(), currency1: c1.toLowerCase(), fee: Number(fee), tickSpacing: Number(ts), hooks: hooks.toLowerCase() };
  if (k.currency0 === ZERO && k.currency1 === ZERO) throw new Error("pool not known to the PositionManager");
  if (poolIdOf(k).toLowerCase() !== poolId.toLowerCase()) throw new Error("pool key does not hash to this pool id");
  return k;
}
export async function slot0(provider, poolId) {
  const r = await provider.call({ to: STATE_VIEW, data: svIface.encodeFunctionData("getSlot0", [poolId]) });
  const [sqrtPriceX96, tick, protocolFee, lpFee] = svIface.decodeFunctionResult("getSlot0", r);
  return { sqrtPriceX96, tick: Number(tick), protocolFee: Number(protocolFee), lpFee: Number(lpFee) };
}
export async function decimalsOf(provider, token) {
  if (token.toLowerCase() === ZERO) return 18;
  return Number(erc20.decodeFunctionResult("decimals", await provider.call({ to: token, data: erc20.encodeFunctionData("decimals", []) }))[0]);
}
export async function balanceOf(provider, token, owner) {
  if (token.toLowerCase() === ZERO) return await provider.getBalance(owner);
  return erc20.decodeFunctionResult("balanceOf", await provider.call({ to: token, data: erc20.encodeFunctionData("balanceOf", [owner]) }))[0];
}
export async function positionInfo(provider, tokenId) {
  const [k, info] = pmIface.decodeFunctionResult("getPoolAndPositionInfo",
    await provider.call({ to: POSITION_MANAGER, data: pmIface.encodeFunctionData("getPoolAndPositionInfo", [tokenId]) }));
  const liq = pmIface.decodeFunctionResult("getPositionLiquidity",
    await provider.call({ to: POSITION_MANAGER, data: pmIface.encodeFunctionData("getPositionLiquidity", [tokenId]) }))[0];
  const owner = pmIface.decodeFunctionResult("ownerOf",
    await provider.call({ to: POSITION_MANAGER, data: pmIface.encodeFunctionData("ownerOf", [tokenId]) }))[0];
  const key = { currency0: k[0].toLowerCase(), currency1: k[1].toLowerCase(), fee: Number(k[2]), tickSpacing: Number(k[3]), hooks: k[4].toLowerCase() };
  const s24 = x => (x & 0x800000n ? x - 0x1000000n : x);
  return { tokenId: BigInt(tokenId), key, poolId: poolIdOf(key), owner: owner.toLowerCase(), liquidity: liq,
    tickLower: Number(s24((info >> 8n) & 0xffffffn)), tickUpper: Number(s24((info >> 32n) & 0xffffffn)) };
}

// ------------------------------------------------------------------ ladder ----
const sqrtAt = t => Math.pow(1.0001, t / 2);
export const ticksBelow = pct => { const f = Math.min(99, Math.max(0, pct)) / 100; return f <= 0 ? 0 : Math.log(1 / (1 - f)) / Math.log(1.0001); };

// RH Pool's planZap, unchanged: equal-width slices on the tick grid from `top`% to `bottom`% below
// the price, weights 1:2:...:n (deepest slice biggest), single-sided in the deposit currency.
export function planLadder(key, currentTick, depositRaw, depositIsC0, { slices, top = 0, bottom }) {
  const sp = key.tickSpacing;
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
    // %-below-price of each edge, in the token's price (what the page shows)
    const pct = t => (1 - Math.pow(1.0001, depositIsC0 ? -(t - currentTick) : (t - currentTick))) * 100;
    out.push({ tickLower: lo, tickUpper: hi, amount, liquidity: BigInt(Math.floor(L * (1 - 1e-9))),
      nearPct: pct(depositIsC0 ? lo : hi), farPct: pct(depositIsC0 ? hi : lo) });
  }
  if (!out.length) throw new Error("deposit too small for any slice");
  return { slices: out, step, span, topTicks, realEnd: out[out.length - 1].farPct };
}

// ------------------------------------------------------------------ encoders ----
const keyTuple = k => [k.currency0, k.currency1, k.fee, k.tickSpacing, k.hooks];
const enc = (types, vals) => ab.encode(types, vals);
export const P = {
  mint: (k, lo, hi, L, max0, max1, owner) => enc([KEY, "int24", "int24", "uint256", "uint128", "uint128", "address", "bytes"],
    [keyTuple(k), lo, hi, L, max0, max1, owner, "0x"]),
  burn: (id, min0 = 0n, min1 = 0n) => enc(["uint256", "uint128", "uint128", "bytes"], [id, min0, min1, "0x"]),
  decrease: (id, L, min0 = 0n, min1 = 0n) => enc(["uint256", "uint256", "uint128", "uint128", "bytes"], [id, L, min0, min1, "0x"]),
  settlePair: (c0, c1) => enc(["address", "address"], [c0, c1]),
  takePair: (c0, c1, to) => enc(["address", "address", "address"], [c0, c1, to]),
  sweep: (c, to) => enc(["address", "address"], [c, to]),
  closeCurrency: c => enc(["address"], [c]),
};
export function modifyLiquidities(actions, params, deadline) {
  const unlock = enc(["bytes", "bytes[]"], [ethers.hexlify(Uint8Array.from(actions)), params]);
  return pmIface.encodeFunctionData("modifyLiquidities", [unlock, deadline]);
}
export const deadline = (min = 20) => BigInt(Math.floor(Date.now() / 1000) + min * 60);

export function openCalldata(key, plan, depositIsC0, owner, dl) {
  const acts = [], params = [];
  for (const s of plan.slices) {
    acts.push(ACT.MINT);
    params.push(P.mint(key, s.tickLower, s.tickUpper, s.liquidity, depositIsC0 ? s.amount : 0n, depositIsC0 ? 0n : s.amount, owner));
  }
  acts.push(ACT.SETTLE_PAIR);
  params.push(P.settlePair(key.currency0, key.currency1));
  const native = key.currency0 === ZERO && depositIsC0;
  if (native) { acts.push(ACT.SWEEP); params.push(P.sweep(ZERO, owner)); }   // refund what the mint did not use
  const value = native ? plan.slices.reduce((a, s) => a + s.amount, 0n) : 0n;
  return { data: modifyLiquidities(acts, params, dl), value };
}
// positions: [{tokenId, key}] -> one modifyLiquidities that burns (or claims) all, TAKE_PAIR per pool
export function closeCalldata(positions, recipient, dl, claimOnly = false) {
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
export function swapCalldata(key, zeroForOne, amountIn, minOut, dl) {
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

// Price of currency0 in currency1, raw units: (sqrtP / 2^96)^2
export const rawPrice01 = sqrtX96 => Math.pow(Number(sqrtX96) / 2 ** 96, 2);

// ------------------------------------------------------------------ approvals ----
// ERC-20 -> Permit2 (max, once), Permit2 -> spender (max, no expiry). Native ETH needs neither.
export async function approvalTxs(provider, owner, token, spender, amount) {
  if (token.toLowerCase() === ZERO) return [];
  const out = [];
  const a = erc20.decodeFunctionResult("allowance", await provider.call({ to: token, data: erc20.encodeFunctionData("allowance", [owner, PERMIT2]) }))[0];
  if (a < amount) out.push({ label: "approve token → Permit2", to: token, data: erc20.encodeFunctionData("approve", [PERMIT2, ethers.MaxUint256]) });
  const [amt, exp] = p2Iface.decodeFunctionResult("allowance", await provider.call({ to: PERMIT2, data: p2Iface.encodeFunctionData("allowance", [owner, token, spender]) }));
  const now = BigInt(Math.floor(Date.now() / 1000));
  if (amt < amount || (exp !== 0n && exp < now)) {
    out.push({ label: "Permit2 → " + (spender.toLowerCase() === UNIVERSAL_ROUTER.toLowerCase() ? "router" : "PositionManager"),
      to: PERMIT2, data: p2Iface.encodeFunctionData("approve", [token, spender, MAX_U160, MAX_U48]) });
  }
  return out;
}

// ------------------------------------------------------------------ send ----
// simulate (eth_call) -> estimate x1.3 -> sign + send -> wait. Throws before signing if it would revert.
export async function sendTx(signer, { to, data, value = 0n, label = "tx" }, { dryRun = false, from } = {}) {
  const provider = signer ? signer.provider : await pickTradeProvider();
  const sender = from || (signer && signer.address);
  try {
    await provider.call({ from: sender, to, data, value });
  } catch (e) {
    throw new Error(`${label}: would revert, not sent (${String(e.shortMessage || e.reason || e.message).slice(0, 160)})`);
  }
  if (dryRun) return { label, simulated: true };
  const gas = (await provider.estimateGas({ from: sender, to, data, value })) * 13n / 10n;
  const tx = await signer.sendTransaction({ to, data, value, gasLimit: gas, chainId: CHAIN_ID });
  const rc = await tx.wait(1, 180_000);
  if (!rc || rc.status !== 1) throw new Error(`${label}: reverted on chain ${tx.hash}`);
  return { label, hash: tx.hash, gasUsed: rc.gasUsed, fee: rc.gasUsed * (rc.gasPrice || 0n), logs: rc.logs };
}

// Position NFT ids minted to `owner` in a receipt (Transfer from 0x0).
export function mintedIds(logs, owner) {
  const T = ethers.id("Transfer(address,address,uint256)");
  return (logs || []).filter(l => l.address.toLowerCase() === POSITION_MANAGER && l.topics[0] === T
    && BigInt(l.topics[1]) === 0n && ("0x" + l.topics[2].slice(26)).toLowerCase() === owner.toLowerCase()).map(l => BigInt(l.topics[3]).toString());
}

export { ethers };
