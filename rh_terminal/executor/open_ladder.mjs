// Open an RH ladder in a USDG / ETH / WETH pool, quote-only, below the price.
//   node open_ladder.mjs --pool 0x<poolId> --usd 400 [--ethusd 2230] --slices 5 --top 0 --bottom 60 [--plan | --dry-run --owner 0x..]
//   --amount <quote units> instead of --usd. --plan: work out the ladder, read-only.
import { args, done, fail, withRead, pickTradeProvider, loadSigner, poolKey, slot0, quoteSide, decimalsOf, balanceOf,
  planLadder, openCalldata, approvalTxs, sendTx, deadline, mintedIds, POSITION_MANAGER, ZERO, USDG, ethers } from "./lib.mjs";

const a = args();
try {
  const pool = String(a.pool || "").toLowerCase();
  if (!/^0x[0-9a-f]{64}$/.test(pool)) throw new Error("--pool must be a 32-byte pool id");
  const { key, s0, dec } = await withRead(async pr => {
    const key = await poolKey(pr, pool);
    return { key, s0: await slot0(pr, pool) };
  }).then(async x => ({ ...x, dec: await withRead(pr => decimalsOf(pr, quoteSide(x.key) === 0 ? x.key.currency0 : x.key.currency1)) }));
  const q = quoteSide(key);
  if (q === null) throw new Error("not a USDG / ETH pool");
  const quote = q === 0 ? key.currency0 : key.currency1;
  let units = a.amount != null ? Number(a.amount) : null;
  if (units == null) {
    const usd = Number(a.usd);
    if (!(usd > 0)) throw new Error("--usd or --amount required");
    const px = quote === USDG ? 1 : Number(a.ethusd);
    if (!(px > 0)) throw new Error("--ethusd needed to size an ETH ladder in dollars");
    units = usd / px;
  }
  const depositRaw = BigInt(Math.floor(units * 10 ** dec));
  const plan = planLadder(key, s0.tick, depositRaw, q === 0, { slices: Number(a.slices || 5), top: Number(a.top || 0), bottom: Math.abs(Number(a.bottom || 60)) });
  const total = plan.slices.reduce((s, x) => s + x.amount, 0n);
  const view = {
    pool, key, quote, quoteDecimals: dec, tick: s0.tick, lpFee: s0.lpFee, total: Number(total) / 10 ** dec,
    realEnd: plan.realEnd, step: plan.step,
    slices: plan.slices.map(s => ({ tickLower: s.tickLower, tickUpper: s.tickUpper, amount: Number(s.amount) / 10 ** dec,
      nearPct: s.nearPct, farPct: s.farPct })),
  };
  if (a.plan) { done({ ok: true, plan: view }); process.exit(0); }

  const dry = !!a["dry-run"];
  const signer = dry ? null : loadSigner((await pickTradeProvider()));
  if (!dry && !signer) throw new Error("no RH_PRIVATE_KEY in .env");
  const owner = dry ? String(a.owner || "") : signer.address;
  if (!ethers.isAddress(owner)) throw new Error("--owner needed for --dry-run");
  const pr = signer ? signer.provider : (await pickTradeProvider());
  const bal = await balanceOf(pr, quote, owner);
  const reserve = quote === ZERO ? 50_000_000_000_000n : 0n;      // keep ~0.00005 ETH for gas
  if (bal < total + reserve) throw new Error(`wallet holds ${Number(bal) / 10 ** dec} ${quote === USDG ? "USDG" : "ETH"}, ladder needs ${Number(total + reserve) / 10 ** dec}`);
  const steps = [];
  const appr = await approvalTxs(pr, owner, quote, POSITION_MANAGER, total);
  for (const t of appr) steps.push(dry ? { label: t.label, needed: true } : await sendTx(signer, t));
  const tx = openCalldata(key, plan, q === 0, owner, deadline());
  if (dry && appr.length) {
    done({ ok: true, dryRun: true, plan: view, steps, note: "approvals missing, so the mint itself was not simulated" });
    process.exit(0);
  }
  const r = await sendTx(signer, { ...tx, to: POSITION_MANAGER, label: `mint ${plan.slices.length} slices` }, { dryRun: dry, from: owner });
  steps.push(r);
  done({ ok: true, dryRun: dry, plan: view, steps, hash: r.hash, positions: r.logs ? mintedIds(r.logs, owner) : [] });
} catch (e) { fail(e); }
