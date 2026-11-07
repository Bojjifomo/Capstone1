// Close (burn: withdraw 100% + collect fees) or claim fees from positions, one transaction.
//   node close.mjs --ids 123,124 [--claim] [--dry-run --owner 0x..]
import { pathToFileURL } from "node:url";
import { args, done, fail, pickTradeProvider, loadSigner, positionInfo, closeCalldata, sendTx, deadline, balanceOf, POSITION_MANAGER, ZERO } from "./lib.mjs";

export async function closePositions({ ids, claim = false, dry = false, owner }) {
  const signer = dry ? null : loadSigner((await pickTradeProvider()));
  if (!dry && !signer) throw new Error("no RH_PRIVATE_KEY in .env");
  const who = (dry ? owner : signer.address).toLowerCase();
  const pr = signer ? signer.provider : (await pickTradeProvider());
  const ps = [];
  for (const id of ids) {
    const p = await positionInfo(pr, id);
    if (p.owner !== who) throw new Error(`position ${id} belongs to ${p.owner}, not ${who}`);
    ps.push(p);
  }
  const cur = [...new Set(ps.flatMap(p => [p.key.currency0, p.key.currency1]))];
  const before = Object.fromEntries(await Promise.all(cur.map(async c => [c, await balanceOf(pr, c, who)])));
  const data = closeCalldata(ps, who, deadline(), claim);
  const r = await sendTx(signer, { to: POSITION_MANAGER, data, label: `${claim ? "claim" : "close"} ${ps.length}` }, { dryRun: dry, from: who });
  const received = {};
  // native ETH: add the gas back, so "received" is what the positions paid out
  if (!dry) for (const c of cur) received[c] = (await balanceOf(pr, c, who)) - before[c] + (c === ZERO ? (r.fee || 0n) : 0n);
  return { tx: r, positions: ps, received, owner: who };
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const a = args();
  try {
    const ids = String(a.ids || "").split(",").filter(Boolean).map(x => BigInt(x));
    if (!ids.length) throw new Error("--ids required");
    const r = await closePositions({ ids, claim: !!a.claim, dry: !!a["dry-run"], owner: a.owner });
    done({ ok: true, dryRun: !!a["dry-run"], hash: r.tx.hash, ids: ids.map(String), received: r.received });
  } catch (e) { fail(e); }
}
