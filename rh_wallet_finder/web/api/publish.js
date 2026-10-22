// Where the Wallet Finder on the Mac sends the boards: PUT the gzipped snapshot
// with `authorization: Bearer <PUBLISH_KEY>` and this function stores it in the
// project's Blob store with the project's own credential (see _blob.js), so the
// Mac never needs a Blob token (the ones the dashboard shows now expire).
// PUBLISH_KEY is an env var of this project; the Mac has the same key in its
// publish link (…/api/publish?key=…).
const crypto = require("crypto");
const zlib = require("zlib");
const { PATHNAME, BLOB_API, NO_STORE, credentials, send } = require("./_blob");

const MAX_BYTES = 4 * 1024 * 1024; // Vercel takes request bodies up to 4.5 MB

function sameKey(a, b) {
  const h = s => crypto.createHash("sha256").update(String(s)).digest();
  return crypto.timingSafeEqual(h(a), h(b));
}

async function readBody(req) {
  if (Buffer.isBuffer(req.body)) return req.body;
  const parts = [];
  let n = 0;
  for await (const chunk of req) {
    n += chunk.length;
    if (n > MAX_BYTES) return null;
    parts.push(chunk);
  }
  return Buffer.concat(parts);
}

module.exports = async (req, res) => {
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  if (req.method !== "PUT" && req.method !== "POST") {
    return send(res, 405, { error: "The Wallet Finder PUTs its snapshot here; there is nothing to see." });
  }
  const key = process.env.PUBLISH_KEY || "";
  if (key.length < 16) return send(res, 503, { error: "This Vercel project has no PUBLISH_KEY yet." });
  const given = String(req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  if (!given || !sameKey(given, key)) return send(res, 401, { error: "Wrong publish key: copy the publish link again." });

  const body = await readBody(req);
  if (!body) return send(res, 413, { error: `The snapshot is over ${MAX_BYTES / 1048576} MB.` });
  let snap = null;
  try {
    snap = JSON.parse(zlib.gunzipSync(body));
  } catch (e) {
    snap = null;
  }
  if (!snap || typeof snap.views !== "object") return send(res, 400, { error: "That isn't a gzipped board snapshot." });

  const creds = credentials(req, process.env);
  if (!creds.length) return send(res, 503, { error: NO_STORE });
  let last = "";
  for (const c of creds) {
    for (const access of ["private", "public"]) { // a store takes only uploads of its own kind
      const r = await fetch(`${BLOB_API}?pathname=${encodeURIComponent(PATHNAME)}`, {
        method: "PUT", body,
        headers: { authorization: `Bearer ${c.token}`, "x-vercel-blob-store-id": c.store, "x-api-version": "13",
                   "x-vercel-blob-access": access, "x-content-type": "application/gzip", "x-add-random-suffix": "0",
                   "x-allow-overwrite": "1", "x-cache-control-max-age": "60" } });
      const text = await r.text();
      if (r.ok) {
        let url = "";
        try { url = JSON.parse(text).url || ""; } catch (e) { url = ""; }
        return send(res, 200, { url, bytes: body.length, wallets: (snap.views.raw || []).length, access, via: c.kind });
      }
      let msg = text.slice(0, 300);
      try { msg = JSON.parse(text).error.message || msg; } catch (e) { /* not JSON */ }
      last = `${c.kind} ${access}: HTTP ${r.status} ${msg}`;
      if (!/access on a (private|public) store/i.test(msg)) break; // only a wrong store kind is worth the other try
    }
  }
  return send(res, 502, { error: `The Blob store refused the upload (${last}).` });
};
