// The latest snapshot of the boards, as the Wallet Finder on the Mac published it
// (rh_wallet_finder/publish.py, through api/publish or straight to Blob). A
// private store is read with the credential; a public one answers either way.
const zlib = require("zlib");
const { PATHNAME, NO_STORE, credentials, send } = require("./_blob");

module.exports = async (req, res) => {
  res.setHeader("content-type", "application/json; charset=utf-8");
  const creds = credentials(req, process.env);
  if (!creds.length) return send(res, 503, { error: NO_STORE });
  let r = null;
  const tried = [];
  try {
    search: for (const c of creds) {
      for (const access of ["private", "public"]) {
        r = await fetch(`https://${c.store}.${access}.blob.vercel-storage.com/${PATHNAME}?t=${Date.now()}`,
                        { cache: "no-store", headers: { authorization: `Bearer ${c.token}` } });
        if (r.ok) break search;
        tried.push(`${c.kind} ${access} ${r.status}`);
      }
    }
  } catch (e) {
    return send(res, 502, { error: `Can't reach the Blob store: ${e.message}` });
  }
  if (!r.ok) {
    const missing = tried.every(t => / (403|404)$/.test(t));
    return send(res, missing ? 404 : 502, {
      error: missing ? "Nothing published yet. In the Wallet Finder: Settings → Web showcase → paste the publish " +
        "link → Save → Publish now." : `The Blob store answered HTTP ${r.status}.`, tried });
  }
  let body = Buffer.from(await r.arrayBuffer());
  if (body[0] === 0x1f && body[1] === 0x8b) body = zlib.gunzipSync(body); // (unless fetch already unpacked it)
  res.setHeader("cache-control", "public, max-age=0, s-maxage=60, stale-while-revalidate=300");
  send(res, 200, body);
};
