// The latest snapshot of the boards, as the Wallet Finder on the Mac uploaded it
// to this project's Vercel Blob store (rh_wallet_finder/publish.py).
// Vercel adds BLOB_READ_WRITE_TOKEN once a Blob store is connected to the project;
// the store's public host comes from that token, as @vercel/blob works it out.
const zlib = require("zlib");

const PATHNAME = "rhwf/snapshot.json.gz";

module.exports = async (req, res) => {
  const token = process.env.BLOB_READ_WRITE_TOKEN || "";
  const storeId = token.split("_")[3];
  res.setHeader("content-type", "application/json; charset=utf-8");
  if (!storeId) {
    res.statusCode = 503;
    res.end(JSON.stringify({ error: "No Blob store is connected to this Vercel project yet." }));
    return;
  }
  let r;
  try {
    r = await fetch(`https://${storeId}.public.blob.vercel-storage.com/${PATHNAME}?t=${Date.now()}`,
                    { cache: "no-store" });
  } catch (e) {
    res.statusCode = 502;
    res.end(JSON.stringify({ error: `Can't reach the Blob store: ${e.message}` }));
    return;
  }
  if (r.status === 404) {
    res.statusCode = 404;
    res.end(JSON.stringify({ error: "Nothing published yet. Paste the Blob token in the Wallet Finder's Settings." }));
    return;
  }
  if (!r.ok) {
    res.statusCode = 502;
    res.end(JSON.stringify({ error: `The Blob store answered HTTP ${r.status}.` }));
    return;
  }
  let body = Buffer.from(await r.arrayBuffer());
  if (body[0] === 0x1f && body[1] === 0x8b) body = zlib.gunzipSync(body); // (unless fetch already unpacked it)
  res.setHeader("cache-control", "public, max-age=0, s-maxage=60, stale-while-revalidate=300");
  res.statusCode = 200;
  res.end(body);
};
