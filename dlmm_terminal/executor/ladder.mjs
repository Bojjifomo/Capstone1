// RH ladder plan, the same as MET.planLadder in "MET Pool". Pure: no RPC.
const BINS_PER_POSITION = 70;
// ---- plan: identical to MET.planLadder in "MET Pool" (RH ladder, weights 1..n) ----
const binsForDrop = (pct, step) => { const f = Math.min(99.9, Math.max(0, pct)) / 100; return f <= 0 ? 0 : Math.log(1 / (1 - f)) / Math.log(1 + step / 10000); };
const dropForBins = (bins, step) => (1 - Math.pow(1 + step / 10000, -bins)) * 100;
export function planLadder({ activeId, binStep, solIsX, lamports, slices, top, range }) {
  const topBins = Math.round(binsForDrop(top || 0, binStep));
  let endBins = Math.max(topBins, Math.round(binsForDrop(range, binStep)));
  const wanted = Math.max(1, Math.floor(slices || 1));
  const fit = wanted * BINS_PER_POSITION;
  if (endBins - topBins + 1 > fit && endBins - topBins + 1 - fit <= Math.max(2, Math.ceil(fit * 0.03))) endBins = topBins + fit - 1;
  const total = endBins - topBins + 1;
  let n = Math.min(Math.max(wanted, Math.ceil(total / BINS_PER_POSITION)), total);
  const weights = Array.from({ length: n }, (_, i) => i + 1);
  const wsum = weights.reduce((a, b) => a + b, 0);
  const base = Math.floor(total / n), extra = total % n;
  const res = [];
  let offset = topBins, spent = 0n;
  for (let i = 0; i < n; i++) {
    const size = base + (i < extra ? 1 : 0);
    const nearD = offset, farD = offset + size - 1;
    offset += size;
    const lo = solIsX ? activeId + nearD : activeId - farD;
    const hi = solIsX ? activeId + farD : activeId - nearD;
    let amount = i === n - 1 ? lamports - spent : (lamports * BigInt(weights[i] * 1e6)) / BigInt(wsum * 1e6);
    if (amount < 0n) amount = 0n;
    spent += amount;
    res.push({ lo, hi, width: hi - lo + 1, lamports: amount, nearPct: dropForBins(nearD, binStep), farPct: dropForBins(farD, binStep), weight: weights[i] / wsum });
  }
  return { slices: res.filter(s => s.lamports > 0n), raised: n > wanted ? n : 0, bins: total, realTop: dropForBins(topBins, binStep), realEnd: dropForBins(endBins, binStep) };
}

