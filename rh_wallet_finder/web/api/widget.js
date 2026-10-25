// A few KB for the phone widget (ios/RH Pools Widget.js): best fee pools of the last
// 7 days and the top wallets of each class, from the same snapshot as the page.
//   /api/widget?pools=5&wallets=3
const { readSnapshot, send } = require("./_blob");

const PICK = ["addr", "kind", "score", "verdict", "net", "net_7d", "win", "dd", "trades"];

function slim(data, nPools, nWallets) {
  const rows = (data.views && (data.views.real || data.views.raw)) || [];
  const top = kind => rows.filter(r => r.kind === kind).slice(0, nWallets)
    .map(r => Object.fromEntries(PICK.filter(k => r[k] !== undefined).map(k => [k, r[k]])));
  const pools = (data.pools || []).slice(0, nPools).map(p => ({
    pair: p.pair, pool: p.pool, fee_tier: p.fee_tier, fee_apr: p.fee_apr, net_apr: p.net_apr,
    fee_per_1k: p.fee_per_1k, wallets: p.wallets, positions: p.positions, win: p.win, top_share: p.top_share,
  }));
  const st = data.status || {};
  return { generated: data.generated, age_min: Math.round((Date.now() / 1000 - (data.generated || 0)) / 60),
           wallets: rows.length, eth_usd: st.eth_usd, pools, multiday: top("multiday"), degen: top("degen") };
}

module.exports = async (req, res) => {
  res.setHeader("content-type", "application/json; charset=utf-8");
  const q = new URL(req.url, "http://x").searchParams;
  const clamp = (v, d) => Math.max(1, Math.min(20, parseInt(v, 10) || d));
  const snap = await readSnapshot(req, process.env);
  if (snap.status !== 200) return send(res, snap.status, { error: snap.error });
  let data;
  try {
    data = JSON.parse(snap.body.toString("utf8"));
  } catch (e) {
    return send(res, 502, { error: "The published snapshot isn't valid JSON." });
  }
  res.setHeader("cache-control", "public, max-age=0, s-maxage=60, stale-while-revalidate=300");
  send(res, 200, slim(data, clamp(q.get("pools"), 5), clamp(q.get("wallets"), 3)));
};

module.exports.slim = slim;
