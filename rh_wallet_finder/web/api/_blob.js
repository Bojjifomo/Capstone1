// Shared by the site's functions (a leading underscore keeps this file from
// being a function itself). How to reach this project's Blob store:
//   - a store connected the current way: BLOB_STORE_ID plus the OIDC token Vercel
//     hands every function call (x-vercel-oidc-token header), as @vercel/blob does;
//   - or a read-write token in BLOB_READ_WRITE_TOKEN (<PREFIX>_READ_WRITE_TOKEN
//     with another prefix), which also names the store.
// Env vars only reach deployments made after they were added.
const PATHNAME = "rhwf/snapshot.json.gz";
const BLOB_API = "https://vercel.com/api/blob/";

const NO_STORE = "No Blob store reaches this deployment. In Vercel: Storage → your Blob store → Connect Project → " +
  "this project (Production), then Deployments → the latest one → ⋯ → Redeploy.";

function blobToken(env) {
  if ((env.BLOB_READ_WRITE_TOKEN || "").startsWith("vercel_blob_rw_")) return env.BLOB_READ_WRITE_TOKEN;
  const k = Object.keys(env).sort().find(n => n.endsWith("READ_WRITE_TOKEN") && String(env[n]).startsWith("vercel_blob_rw_"));
  return k ? env[k] : "";
}

function storeId(env) {
  const k = env.BLOB_STORE_ID ? "BLOB_STORE_ID"
    : Object.keys(env).sort().find(n => n.endsWith("_STORE_ID") && String(env[n]).startsWith("store_"));
  return k ? String(env[k]).replace(/^store_/, "") : "";
}

// [{ store, token, kind }] to try, in order
function credentials(req, env) {
  const out = [];
  const oidc = (req && req.headers && req.headers["x-vercel-oidc-token"]) || env.VERCEL_OIDC_TOKEN || "";
  const store = storeId(env);
  if (oidc && store) out.push({ store, token: oidc, kind: "oidc" });
  const rw = blobToken(env);
  if (rw) out.push({ store: rw.split("_")[3], token: rw, kind: "token" });
  return out;
}

// The latest snapshot -> { status: 200, body: Buffer of JSON, gz: the stored gzip or null }
// or { status, error, tried }
async function readSnapshot(req, env) {
  const zlib = require("zlib");
  const creds = credentials(req, env);
  if (!creds.length) return { status: 503, error: NO_STORE };
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
    return { status: 502, error: `Can't reach the Blob store: ${e.message}` };
  }
  if (!r.ok) {
    const missing = tried.every(t => / (403|404)$/.test(t));
    return { status: missing ? 404 : 502, tried, error: missing
      ? "Nothing published yet. In the Wallet Finder: Settings → Web showcase → paste the publish link → Save → " +
        "Publish now." : `The Blob store answered HTTP ${r.status}.` };
  }
  const got = Buffer.from(await r.arrayBuffer());
  const gz = got[0] === 0x1f && got[1] === 0x8b ? got : null; // (unless fetch already unpacked it)
  return { status: 200, body: gz ? zlib.gunzipSync(gz) : got, gz };
}

// Big bodies go out gzipped when the client takes that (it always does: browsers,
// curl --compressed, spreadsheets): a function's answer must stay under 4.5 MB.
function sendBig(req, res, status, body, gz) {
  const zlib = require("zlib");
  res.setHeader("vary", "accept-encoding");
  if (/\bgzip\b/.test(String((req.headers || {})["accept-encoding"] || ""))) {
    res.setHeader("content-encoding", "gzip");
    return send(res, status, gz || zlib.gzipSync(Buffer.isBuffer(body) ? body : Buffer.from(body), { level: 6 }));
  }
  send(res, status, body);
}

function send(res, status, body) {
  res.statusCode = status;
  res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
}

module.exports = { PATHNAME, BLOB_API, NO_STORE, credentials, readSnapshot, send, sendBig };
