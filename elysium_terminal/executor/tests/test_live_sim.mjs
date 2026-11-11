// Live read-only simulation on Elysium. Sends nothing.
//
// eth_call (+ state overrides for a made-up wallet) runs the same calldata the executor would
// send, against the contracts at the current block. A provider.send of eth_sendRawTransaction
// or eth_sendTransaction throws.
//
//   node tests/test_live_sim.mjs
import { ethers, readProvider, describePool, slot0, quoteSide, planLadder, openCalldataV4, openCalldataV3,
  swapCalldataV3Router, deadline, WHYPE, USDC, ZERO, V3, POOL_MANAGER } from "../lib.mjs";

const pr = readProvider();
const orig = pr.send.bind(pr);
pr.send = async (method, params) => {
  if (method === "eth_sendRawTransaction" || method === "eth_sendTransaction") {
    throw new Error("refusing to send: " + method);
  }
  return orig(method, params);
};

const FAKE = "0x" + "7e".repeat(20);
let fails = 0, skipped = 0;
const check = (name, ok, detail = "") => { console.log(`${ok ? "ok" : "FAIL"} ${name}${detail ? "  " + detail : ""}`); if (!ok) fails++; };
const note = (name, detail) => { skipped++; console.log(`skip ${name}  ${detail}`); };

const tryCall = async (tx, ov) => {
  try {
    const r = await pr.send("eth_call", [{
      from: tx.from, to: tx.to, data: tx.data,
      value: tx.value ? ethers.toQuantity(tx.value) : undefined,
    }, "latest", ov || {}]);
    return { ok: true, ret: r };
  } catch (e) {
    const data = e.data || (e.error && e.error.data) || "";
    return { ok: false, err: String(e.shortMessage || e.message).slice(0, 180) + (data ? " data=" + String(data).slice(0, 80) : "") };
  }
};

async function main() {
  const net = await pr.getNetwork();
  check("chain id 99901", net.chainId === 99901n, String(net.chainId));
  const head = await pr.getBlockNumber();
  console.log("head", head);

  // --- v4: find a native-HYPE quote pool, prefer no hooks ---
  const swapTopic = ethers.id("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)");
  let v4 = null;
  try {
    const logs = await pr.getLogs({ address: POOL_MANAGER, topics: [swapTopic], fromBlock: head - 300, toBlock: head });
    const ids = [];
    for (const lg of logs) if (!ids.includes(lg.topics[1])) ids.push(lg.topics[1]);
    for (const id of ids.slice(0, 25)) {
      try {
        const desc = await describePool(pr, id);
        const q = quoteSide(desc.key);
        if (q !== 0 || desc.key.currency0 !== ZERO) continue;
        const hooked = desc.key.hooks !== ZERO;
        if (!v4 || (v4.hooked && !hooked)) v4 = { desc, hooked };
        if (!hooked) break;
      } catch { /* next */ }
    }
  } catch (e) { note("v4 discovery", String(e.message || e).slice(0, 160)); }

  if (!v4) note("v4 open", "no native-HYPE quote pool in the last 300 blocks");
  else {
    const { desc } = v4;
    const s0 = await slot0(pr, desc.pool, "v4");
    const dec = 18;
    const plan = planLadder(desc.key, s0.tick, BigInt(Math.floor(0.05 * 10 ** dec)), true, { slices: 3, top: 0, bottom: 40 });
    const tx = openCalldataV4(desc.key, plan, true, FAKE, deadline());
    const ov = { [FAKE]: { balance: ethers.toQuantity(10n ** 18n) } };
    const r = await tryCall({ from: FAKE, to: desc.manager, ...tx }, ov);
    check(`v4 open 3 slices on ${desc.pool.slice(0, 10)}…${v4.hooked ? " (hooked)" : ""}`, r.ok, r.err || `${plan.slices.length} slices`);
    const poor = await tryCall({ from: FAKE, to: desc.manager, ...tx }, { [FAKE]: { balance: "0x0" } });
    check("v4 open refuses an empty wallet", !poor.ok);
  }

  // --- v3 ElysiumSwap: WHYPE-quoted pool, pay with native HYPE (NPM wraps) ---
  const esNpm = V3.elysiumswap.npm;
  let v3pool = null;
  try {
    const created = ethers.id("PoolCreated(address,address,uint24,int24,address)");
    const logs = await pr.getLogs({ address: V3.elysiumswap.factory, topics: [created], fromBlock: 1, toBlock: head });
    for (const lg of logs) {
      const t1 = ("0x" + lg.topics[2].slice(26)).toLowerCase();
      const t0 = ("0x" + lg.topics[1].slice(26)).toLowerCase();
      const pool = ("0x" + lg.data.slice(-40)).toLowerCase();
      if (t1 === WHYPE || t0 === WHYPE) {
        if ((t0 === USDC && t1 === WHYPE) || (t1 === USDC && t0 === WHYPE)) continue; // USDC is the quote there
        v3pool = pool;
        break;
      }
    }
  } catch (e) { note("v3 discovery", String(e.message || e).slice(0, 160)); }

  if (!v3pool) note("v3 open", "no WHYPE-quoted ElysiumSwap pool");
  else {
    const desc = await describePool(pr, v3pool);
    const q = quoteSide(desc.key);
    const s0 = await slot0(pr, v3pool, "v3");
    const depositIsC0 = q === 0;
    const plan = planLadder(desc.key, s0.tick, 10n ** 16n, depositIsC0, { slices: 3, top: 0, bottom: 40 });
    const tx = openCalldataV3(desc.key, plan, depositIsC0, FAKE, deadline(), true);
    const r = await tryCall({ from: FAKE, to: esNpm, ...tx }, { [FAKE]: { balance: ethers.toQuantity(10n ** 18n) } });
    check(`v3 ElysiumSwap open on ${v3pool.slice(0, 10)}…`, r.ok, r.err || "");
    const poor = await tryCall({ from: FAKE, to: esNpm, ...tx }, { [FAKE]: { balance: "0x0" } });
    check("v3 open refuses an empty wallet", !poor.ok);

    // Buy the token with native HYPE through the ElysiumSwap SwapRouter. Huge minOut must revert.
    const token = depositIsC0 ? desc.key.currency1 : desc.key.currency0;
    const amount = 10n ** 15n;
    const okSwap = swapCalldataV3Router(desc.key, WHYPE, token, amount, 1n, FAKE, deadline());
    const badSwap = swapCalldataV3Router(desc.key, WHYPE, token, amount, 10n ** 30n, FAKE, deadline());
    const s1 = await tryCall({ from: FAKE, to: V3.elysiumswap.router, data: okSwap.data, value: amount }, { [FAKE]: { balance: ethers.toQuantity(10n ** 18n) } });
    check("v3 SwapRouter exactInputSingle (WHYPE in, minOut 1) simulates", s1.ok, s1.err || "");
    const s2 = await tryCall({ from: FAKE, to: V3.elysiumswap.router, data: badSwap.data, value: amount }, { [FAKE]: { balance: ethers.toQuantity(10n ** 18n) } });
    check("v3 SwapRouter refuses a minOut the pool cannot pay", !s2.ok, s2.err || "");
  }

  console.log(fails ? `\n${fails} FAILED` : `\nLIVE SIM PASSED${skipped ? ` (${skipped} skipped)` : ""}`);
  process.exit(fails ? 1 : 0);
}

main().catch(e => {
  console.error("RPC unreachable or sim crashed:", e.shortMessage || e.message || e);
  process.exit(2);
});
