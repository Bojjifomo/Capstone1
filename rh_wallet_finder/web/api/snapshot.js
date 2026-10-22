// The latest snapshot of the boards, as the Wallet Finder on the Mac uploaded it
// to this project's Vercel Blob store (rh_wallet_finder/publish.py).
// Vercel adds BLOB_READ_WRITE_TOKEN once a Blob store is connected to the project
// (<PREFIX>_READ_WRITE_TOKEN when it was connected with another prefix), and only
// to deployments made after that. The store's host comes from the token, as
// @vercel/blob works it out. A private store is read with the token; a public
// one answers either way.
const zlib = require("zlib");

const PATHNAME = "rhwf/snapshot.json.gz";

const NO_TOKEN = "This deployment has no Blob token. In Vercel: Storage → your Blob store → Connect Project → " +
  "this project (Production), then Deployments → the latest one → ⋯ → Redeploy.";

function blobToken(env) {
  if ((env.BLOB_READ_WRITE_TOKEN || "").startsWith("vercel_blob_rw_")) return env.BLOB_READ_WRITE_TOKEN;
  const k = Object.keys(env).sort().find(n => n.endsWith("READ_WRITE_TOKEN") && String(env[n]).startsWith("vercel_blob_rw_"));
  return k ? env[k] : "";
}

function send(res, status, body) {
  res.statusCode = status;
  res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
}

module.exports = async (req, res) => {
  const token = blobToken(process.env);
  const storeId = token.split("_")[3];
  res.setHeader("content-type", "application/json; charset=utf-8");
  if (!storeId) return send(res, 503, { error: NO_TOKEN });
  let r = null;
  try {
    for (const access of ["private", "public"]) {
      r = await fetch(`https://${storeId}.${access}.blob.vercel-storage.com/${PATHNAME}?t=${Date.now()}`,
                      { cache: "no-store", headers: { authorization: `Bearer ${token}` } });
      if (r.ok) break;
    }
  } catch (e) {
    return send(res, 502, { error: `Can't reach the Blob store: ${e.message}` });
  }
  if (r.status === 404 || r.status === 403) {
    return send(res, 404, { error: "Nothing published yet. Paste the Blob token in the Wallet Finder's Settings and click Publish now." });
  }
  if (!r.ok) return send(res, 502, { error: `The Blob store answered HTTP ${r.status}.` });
  let body = Buffer.from(await r.arrayBuffer());
  if (body[0] === 0x1f && body[1] === 0x8b) body = zlib.gunzipSync(body); // (unless fetch already unpacked it)
  res.setHeader("cache-control", "public, max-age=0, s-maxage=60, stale-while-revalidate=300");
  send(res, 200, body);
};
