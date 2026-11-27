// Live simulation of every executor action on Robinhood Chain, sending nothing.
//
// eth_call with state overrides lets a made-up wallet hold USDG / ETH / tokens and the Permit2
// approvals, so the exact calldata the executor would send is run by the real contracts at the
// current block. Close and claim run as the real owner of real positions (no key needed: it is
// a call, not a transaction).
//
//   node tests/test_live_sim.mjs --usdg-pool 0x.. --eth-pool 0x.. --owner 0x.. --ids 1,2
import { ethers, pickTradeProvider, poolKey, poolIdOf, slot0, quoteSide, planLadder, openCalldata, closeCalldata, swapCalldata,
  positionInfo, deadline, rawPrice01, POSITION_MANAGER, UNIVERSAL_ROUTER, PERMIT2, USDG, ZERO, args, P } from "../lib.mjs";

const a = args();
const pr = await pickTradeProvider();   // first RPC that answers (the official one is blocked on some networks)
const FAKE = "0x" + "7e".repeat(20);
const ab = ethers.AbiCoder.defaultAbiCoder();
const big = 10n ** 18n;   // fits uint64: USDG keeps balances in 64 bits (a larger value reads back truncated)
let fails = 0;
const check = (name, ok, detail = "") => { console.log(`${ok ? "✓" : "✗"} ${name}${detail ? "  " + detail : ""}`); if (!ok) fails++; };

const callOv = async (tx, ov) => pr.send("eth_call", [{ from: tx.from, to: tx.to, data: tx.data, value: tx.value ? ethers.toQuantity(tx.value) : undefined }, "latest", ov]);
const tryCall = async (tx, ov) => {
  try { return { ok: true, ret: await callOv(tx, ov) }; }
  catch (e) {
    const data = e.data || (e.error && e.error.data) || (e.info && e.info.error && e.info.error.data) || "";
    return { ok: false, err: String(e.shortMessage || e.message).slice(0, 120) + (data ? ` data=${String(data).slice(0, 138)}` : "") };
  }
};
const slotKey = (k, slot) => ethers.keccak256(ab.encode(["address", "uint256"], [k, slot]));
const slotKey2 = (k1, k2, slot) => ethers.keccak256(ab.encode(["address", "bytes32"], [k2, slotKey(k1, slot)]));
const word = v => ethers.toBeHex(v, 32);
const erc = new ethers.Interface(["function balanceOf(address) view returns (uint256)", "function allowance(address,address) view returns (uint256)"]);
const p2 = new ethers.Interface(["function allowance(address,address,address) view returns (uint160,uint48,uint48)"]);

// Find the storage slot of balanceOf / allowance for an ERC-20 by trying the usual layouts.
async function erc20Slots(token, owner, amt = big) {
  let bal = null, alw = null;
  for (let s = 0; s < 60 && (bal === null || alw === null); s++) {
    if (bal === null) {
      const r = await callOv({ to: token, data: erc.encodeFunctionData("balanceOf", [owner]) }, { [token]: { stateDiff: { [slotKey(owner, s)]: word(amt) } } });
      if (BigInt(r) === amt) bal = s;
    }
    if (alw === null) {
      const r = await callOv({ to: token, data: erc.encodeFunctionData("allowance", [owner, PERMIT2]) }, { [token]: { stateDiff: { [slotKey2(owner, PERMIT2, s)]: word(big) } } });
      if (BigInt(r) === big) alw = s;
    }
  }
  if (bal === null || alw === null) throw new Error(`storage layout of ${token} not found (balance ${bal}, allowance ${alw})`);
  return { bal, alw };
}
// Permit2's allowance(owner, token, spender) mapping: find its slot the same way.
async function permit2Slot(owner, token, spender) {
  const packed = (BigInt(Math.floor(Date.now() / 1000) + 86400) << 160n) | ((1n << 160n) - 1n);
  for (let s = 0; s < 8; s++) {
    const k = ethers.keccak256(ab.encode(["address", "bytes32"], [spender, ethers.keccak256(ab.encode(["address", "bytes32"], [token, slotKey(owner, s)]))]));
    const r = await callOv({ to: PERMIT2, data: p2.encodeFunctionData("allowance", [owner, token, spender]) }, { [PERMIT2]: { stateDiff: { [k]: word(packed) } } });
    if (p2.decodeFunctionResult("allowance", r)[0] === (1n << 160n) - 1n) return { slot: s, key: k, value: word(packed) };
  }
  throw new Error("Permit2 allowance slot not found");
}
async function fundedOverrides(token, owner, spender, amt = big) {
  const sl = await erc20Slots(token, owner, amt);
  const pm = await permit2Slot(owner, token, spender);
  return { [token]: { stateDiff: { [slotKey(owner, sl.bal)]: word(amt), [slotKey2(owner, PERMIT2, sl.alw)]: word(amt) } },
           [PERMIT2]: { stateDiff: { [pm.key]: pm.value } }, [owner]: { balance: ethers.toQuantity(10n ** 18n) } };
}

// ---- 1. encoders match RH Pool / RH Portfolio's hand-written hex
{
  const _u = v => BigInt(v).toString(16).padStart(64, "0");
  const _a = x => String(x).replace(/^0x/, "").toLowerCase().padStart(64, "0");
  const _i = v => { const n = BigInt(v); return (n < 0n ? n + (1n << 256n) : n).toString(16).padStart(64, "0"); };
  const k = { currency0: ZERO, currency1: "0x978e82db664577e78e3a8dae8812a876b52daf5a", fee: 10000, tickSpacing: 200, hooks: ZERO };
  const rhMint = _a(k.currency0) + _a(k.currency1) + _u(k.fee) + _i(k.tickSpacing) + _a(k.hooks) + _i(-1200) + _i(800) + _u(12345) + _u(7) + _u(0) + _a(FAKE) + _u(384) + _u(0);
  check("MINT_POSITION params == RH Pool mintParam", P.mint(k, -1200, 800, 12345n, 7n, 0n, FAKE).slice(2) === rhMint);
  check("BURN_POSITION params == RH Portfolio burnParam", P.burn(99n).slice(2) === _u(99) + _u(0) + _u(0) + _u(128) + _u(0));
  check("DECREASE(0) params == RH Portfolio decreaseParam", P.decrease(99n, 0n).slice(2) === _u(99) + _u(0) + _u(0) + _u(0) + _u(160) + _u(0));
  check("TAKE_PAIR params == RH Portfolio takePairParam", P.takePair(ZERO, k.currency1, FAKE).slice(2) === _a(ZERO) + _a(k.currency1) + _a(FAKE));
}

// ---- 2. ladder plan: shape and side
async function planFor(pool, usdUnits) {
  const key = await poolKey(pr, pool), s0 = await slot0(pr, pool);
  const q = quoteSide(key), dec = q === 0 && key.currency0 === ZERO ? 18 : 6;
  const plan = planLadder(key, s0.tick, BigInt(Math.floor(usdUnits * 10 ** dec)), q === 0, { slices: 5, top: 0, bottom: 60 });
  return { key, s0, q, plan };
}
for (const [label, pool, units] of [["USDG", a["usdg-pool"], 400], ["ETH", a["eth-pool"], 0.05]]) {
  const { key, s0, q, plan } = await planFor(pool, units);
  const sl = plan.slices;
  const amounts = sl.map(s => Number(s.amount));
  check(`${label} plan: 5 slices weighted 1:2:3:4:5`, sl.length === 5 && amounts.every((x, i) => Math.abs(x / amounts[0] - (i + 1)) < 0.01), amounts.join(" "));
  check(`${label} plan: every edge on the tick grid`, sl.every(s => s.tickLower % key.tickSpacing === 0 && s.tickUpper % key.tickSpacing === 0));
  const oneSide = q === 0 ? sl.every(s => s.tickLower > s0.tick) : sl.every(s => s.tickUpper <= s0.tick);
  check(`${label} plan: whole ladder on the ${q === 0 ? "currency0 side (above tick)" : "currency1 side (at/below tick)"}`, oneSide);
  check(`${label} plan: reaches ~-60%`, plan.realEnd > 55 && plan.realEnd < 65, plan.realEnd.toFixed(1) + "%");

  // ---- 3. the mint itself, from a funded made-up wallet, at the current block
  const tx = openCalldata(key, plan, q === 0, FAKE, deadline());
  const quote = q === 0 ? key.currency0 : key.currency1;
  const ov = quote === ZERO ? { [FAKE]: { balance: ethers.toQuantity(10n ** 18n) } } : await fundedOverrides(quote, FAKE, POSITION_MANAGER);
  const r = await tryCall({ from: FAKE, to: POSITION_MANAGER, ...tx }, ov);
  check(`${label} open ladder simulates on chain`, r.ok, r.err || "");
  // and without funds it must refuse
  const r2 = await tryCall({ from: FAKE, to: POSITION_MANAGER, ...tx }, quote === ZERO ? { [FAKE]: { balance: "0x0" } } : {});
  check(`${label} open ladder refuses an unfunded wallet`, !r2.ok);

}

// ---- 4. selling a token through the router. A made-up wallet cannot hold most launch tokens by
// state override (their transfer path keeps balances elsewhere), so this re-runs real recent sales
// as their real sellers, with the calldata the executor builds and its own minOut (pool price - fee
// - 5%). First the bytes: rebuilt from a mined router swap they must be identical.
{
  const ur = new ethers.Interface(["function execute(bytes commands, bytes[] inputs, uint256 deadline)"]);
  const SP = "tuple(tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks) poolKey,bool zeroForOne,uint128 amountIn,uint128 amountOutMinimum,uint256 minHopPriceX36,bytes hookData)";
  const head = await pr.getBlockNumber();
  let same = 0, ok = 0, guard = 0, seen = 0;
  for (let b = head; b > head - 3000 && (seen < 8 || same < 2); b--) {
    const blk = await pr.getBlock(b, true);
    for (const tx of blk.prefetchedTransactions) {
      if ((tx.to || "").toLowerCase() !== UNIVERSAL_ROUTER.toLowerCase() || !tx.data.startsWith("0x3593564c")) continue;
      let d, acts, params, p;
      try {                                              // other router payload shapes are skipped, not decoded
        d = ur.parseTransaction({ data: tx.data });
        if (d.args[0] !== "0x10") continue;
        [acts, params] = ab.decode(["bytes", "bytes[]"], d.args[1][0]);
        if (acts !== "0x060c0f") continue;               // single-pool exact-in only: what the executor builds
        [p] = ab.decode([SP], params[0]);
        if (p[5] !== "0x" || p[4] !== 0n || p[0].length !== 5) continue;   // ethers decodes lazily: touch the fields here
      } catch (e) { continue; }
      const key = { currency0: p[0][0].toLowerCase(), currency1: p[0][1].toLowerCase(), fee: Number(p[0][2]), tickSpacing: Number(p[0][3]), hooks: p[0][4].toLowerCase() };
      if (swapCalldata(key, p[1], p[2], p[3], d.args[2]).data.toLowerCase() === tx.data.toLowerCase()) same++;
      const tokIn = p[1] ? key.currency0 : key.currency1;
      if (tokIn === ZERO || tokIn === USDG) continue;
      seen++;
      const s0 = await slot0(pr, poolIdOf(key));
      const p01 = rawPrice01(s0.sqrtPriceX96);
      const expected = p[1] ? Number(p[2]) * p01 : Number(p[2]) / p01;
      const pf = p[1] ? s0.protocolFee & 0xfff : (s0.protocolFee >> 12) & 0xfff;
      const minOut = BigInt(Math.floor(expected * (1 - (s0.lpFee + pf) / 1e6) * 0.95));
      const r = await tryCall({ from: tx.from, to: UNIVERSAL_ROUTER, ...swapCalldata(key, p[1], p[2], minOut, deadline()) });
      if (r.ok) {
        ok++;
        const tooHigh = BigInt(Math.floor(expected * 1.05));
        const r2 = await tryCall({ from: tx.from, to: UNIVERSAL_ROUTER, ...swapCalldata(key, p[1], p[2], tooHigh, deadline()) });
        if (!r2.ok && /8b063d73/.test(r2.err)) guard++;
      }
    }
  }
  check("router swap calldata is byte-identical to mined swaps", same > 0, `${same} rebuilt`);
  check("real recent token sales go through with the executor's calldata and minOut", ok > 0, `${ok}/${seen} (the rest: seller no longer holds the token, or price moved > 5%)`);
  check("a minOut above the pool price is refused (V4TooLittleReceived)", guard > 0 && guard === ok, `${guard}/${ok}`);
}

// ---- 5. close and claim real positions, as their real owner
if (a.ids && a.owner) {
  const ids = String(a.ids).split(",").map(x => BigInt(x));
  const ps = [];
  for (const id of ids) ps.push(await positionInfo(pr, id));
  check("positions belong to the owner", ps.every(p => p.owner === a.owner.toLowerCase()));
  for (const claim of [false, true]) {
    const data = closeCalldata(ps, a.owner, deadline(), claim);
    const r = await tryCall({ from: a.owner, to: POSITION_MANAGER, data });
    check(`${claim ? "claim" : "close"} ${ps.length} real positions simulates`, r.ok, r.err || "");
  }
  const r = await tryCall({ from: FAKE, to: POSITION_MANAGER, data: closeCalldata(ps, FAKE, deadline()) });
  check("close refuses a wallet that does not own them", !r.ok);
}

console.log(fails ? `\n${fails} FAILED` : "\nALL SIMULATIONS PASSED");
process.exit(fails ? 1 : 0);
