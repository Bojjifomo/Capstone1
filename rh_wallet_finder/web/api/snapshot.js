// The latest snapshot of the boards, as the Wallet Finder on the Mac published it
// (rh_wallet_finder/publish.py, through api/publish or straight to Blob).
const { readSnapshot, send } = require("./_blob");

module.exports = async (req, res) => {
  res.setHeader("content-type", "application/json; charset=utf-8");
  const snap = await readSnapshot(req, process.env);
  if (snap.status !== 200) return send(res, snap.status, { error: snap.error, tried: snap.tried });
  res.setHeader("cache-control", "public, max-age=0, s-maxage=60, stale-while-revalidate=300");
  send(res, 200, snap.body);
};
