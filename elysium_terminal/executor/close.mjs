// Close (decrease + collect + burn) or claim fees. One transaction.
//   node close.mjs --pool 0x.. --ids 1,2 [--claim] [--dry-run --owner 0x..]
// --pool picks the manager: 32-byte id = v4 PositionManager, 20-byte = that v3 pool's NPM.
import { pathToFileURL } from "node:url";
import { args, done, fail, tradeProvider, loadSigner, describePool, positionInfo, closeCalldataV4, closeCalldataV3,
  sendTx, deadline, balanceOf, ZERO } from "./lib.mjs";

export async function closePositions({ pool, ids, claim = false, dry = false, owner }) {
  const signer = dry ? null : loadSigner(tradeProvider());
  if (!dry && !signer) throw new Error("no ELYSIUM_PRIVATE_KEY in .env");
  const who = (dry ? owner : signer.address).toLowerCase();
  const pr = signer ? signer.provider : tradeProvider();
  const desc = await describePool(pr, pool);
  const ps = [];
  for (const id of ids) {
    const p = await positionInfo(pr, id, desc.dex, desc.manager);
    if (p.owner !== who) throw new Error(`position ${id} belongs to ${p.owner}, not ${who}`);
    p.key.tickSpacing = p.key.tickSpacing || desc.key.tickSpacing;
    p.key.hooks = p.key.hooks || desc.key.hooks;
    ps.push(p);
  }
  const cur = [...new Set(ps.flatMap(p => [p.key.currency0, p.key.currency1]))];
  const before = Object.fromEntries(await Promise.all(cur.map(async c => [c, await balanceOf(pr, c, who)])));
  const data = desc.dex === "v4" ? closeCalldataV4(ps, who, deadline(), claim) : closeCalldataV3(ps, who, deadline(), claim);
  const r = await sendTx(signer, { to: desc.manager, data, label: `${claim ? "claim" : "close"} ${ps.length}` }, { dryRun: dry, from: who });
  const received = {};
  if (!dry) for (const c of cur) received[c] = (await balanceOf(pr, c, who)) - before[c] + (c === ZERO ? (r.fee || 0n) : 0n);
  return { tx: r, positions: ps, received, owner: who, desc };
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const a = args();
  try {
    const ids = String(a.ids || "").split(",").filter(Boolean).map(x => BigInt(x));
    if (!ids.length) throw new Error("--ids required");
    if (!a.pool) throw new Error("--pool required");
    const r = await closePositions({ pool: String(a.pool), ids, claim: !!a.claim, dry: !!a["dry-run"], owner: a.owner });
    done({ ok: true, dryRun: !!a["dry-run"], hash: r.tx.hash, ids: ids.map(String), received: r.received, dex: r.desc.dex });
  } catch (e) { fail(e); }
}
