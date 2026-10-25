// The latest snapshot of the boards, as the Wallet Finder on the Mac published it
// (rh_wallet_finder/publish.py, through api/publish or straight to Blob).
const { readSnapshot, send, sendBig } = require("./_blob");

module.exports = async (req, res) => {
  res.setHeader("content-type", "application/json; charset=utf-8");
  const got = await readSnapshot(req, process.env);
  if (got.status !== 200) return send(res, got.status, { error: got.error, ...(got.tried ? { tried: got.tried } : {}) });
  res.setHeader("cache-control", "public, max-age=0, s-maxage=60, stale-while-revalidate=300");
  sendBig(req, res, 200, got.body, got.gz);
};
