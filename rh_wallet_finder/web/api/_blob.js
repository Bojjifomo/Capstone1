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

function send(res, status, body) {
  res.statusCode = status;
  res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
}

module.exports = { PATHNAME, BLOB_API, NO_STORE, credentials, send };
