"""Publish a read-only snapshot of the boards for the web showcase
(rh_wallet_finder/web, hosted on Vercel).

This computer stays the engine: after each ranking the worker uploads one
gzipped JSON file to the Vercel project's Blob store, and the site shows it.
Only public chain data goes out: wallet addresses and the figures worked out
from their LP positions. Labels, notes, the watchlist, LP Agent results, RH
Smart LP Finder rankings and every key stay here.

Upload, as @vercel/blob's put() does it (its source, api version 13):
  PUT https://vercel.com/api/blob/?pathname=rhwf/snapshot.json.gz
  authorization: Bearer <BLOB_READ_WRITE_TOKEN>, x-vercel-blob-access: public, ...
"""
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config
import queries as Q
from db import get_meta, set_meta

BLOB_API = "https://vercel.com/api/blob/"
PATHNAME = "rhwf/snapshot.json.gz"
VERSION = 1
MAX_ROWS = 2000  # per view
VIEWS = ("raw", "real", "d1raw", "d1real")

# what the page shows and filters on; nothing personal
FIELDS = ("addr", "kind", "kind_fit", "score", "verdict", "worth", "net", "roi_win", "net_7d", "momentum", "net_1d",
          "float", "total", "roi", "win", "pf", "daily", "fees", "dd", "recover", "recover_open", "usdg", "size",
          "duration", "trades", "closed_trades", "closed", "flat", "style", "style_conf", "sim_pct", "range", "mcap",
          "open", "concur", "copy", "copiers", "exposure", "open_now", "first_seen", "last_seen", "span_days")


class PublishError(Exception):
    pass


def _num(v):
    """Floats to 4 significant digits: the file stays small, the page shows less anyway."""
    if isinstance(v, float):
        return None if v != v or v in (float("inf"), float("-inf")) else float(f"{v:.4g}")
    return v


def _row(r):
    out = {k: _num(r.get(k)) for k in FIELDS if r.get(k) is not None}
    if r.get("ref"):
        out["ref"] = 1  # a reference wallet of its class (the label itself stays private)
    if r.get("sim_ref") and r.get("sim_pct") is not None:
        out["sim_ref"] = 1
    return out


def snapshot(con):
    """Every view of the boards plus the status strip, as one dict."""
    stt = Q.status(con)
    keep = ("discovered", "scored", "rated", "queue", "positions", "pools", "kinds", "synced_at", "scored_at",
            "data_from", "metric_days", "active_days", "multiday_hours", "backfill_days", "eth_usd", "sell_fee",
            "slippage", "rank_version")
    status = {k: _num(stt.get(k)) for k in keep}
    status["min_closed"] = int(stt["settings"]["MIN_CLOSED"])
    views = {v: [_row(r) for r in Q.wallet_rows(con, v)[0][:MAX_ROWS]] for v in VIEWS}
    return {"v": VERSION, "generated": int(time.time()), "status": status, "views": views}


def upload(data, token, pathname=PATHNAME, timeout=60):
    """PUT bytes to the Blob store behind token -> the blob's public URL."""
    req = urllib.request.Request(
        BLOB_API + "?" + urllib.parse.urlencode({"pathname": pathname}), data=data, method="PUT", headers={
            "authorization": f"Bearer {token}", "x-api-version": "13", "x-vercel-blob-access": "public",
            "x-content-type": "application/gzip", "x-add-random-suffix": "0", "x-allow-overwrite": "1",
            "x-cache-control-max-age": "60", "content-type": "application/octet-stream",
            "user-agent": "rh-wallet-finder/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode("utf-8", "replace")
        try:
            msg = json.loads(body)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = body or e.reason
        hint = " Check the Vercel Blob token in Settings." if e.code in (401, 403) else ""
        raise PublishError(f"Vercel Blob said HTTP {e.code}: {msg}.{hint}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PublishError(f"Can't reach Vercel Blob: {e}") from e
    if not isinstance(out, dict) or not out.get("url"):
        raise PublishError(f"Unexpected answer from Vercel Blob: {str(out)[:200]}")
    return out["url"]


def publish(con, token=None, log=print):
    """Build the snapshot and upload it; remembers when and where in meta."""
    token = token or config.load_env()["VERCEL_BLOB_TOKEN"]
    if not token:
        raise PublishError("No Vercel Blob token: paste it in Settings (Web showcase).")
    snap = snapshot(con)
    data = gzip.compress(json.dumps(snap, separators=(",", ":")).encode(), 9)
    try:
        url = upload(data, token)
    except PublishError as e:
        set_meta(con, "publish_error", str(e))
        con.commit()
        raise
    set_meta(con, "published", {"at": snap["generated"], "url": url, "bytes": len(data),
                                "wallets": len(snap["views"]["raw"])})
    set_meta(con, "publish_error", None)
    con.commit()
    log(f"Published {len(snap['views']['raw']):,} wallets to the web showcase ({len(data) / 1024:.0f} KB)")
    return url


def last(con):
    """{'at', 'url', 'bytes', 'wallets'} of the last upload, plus 'error' if the latest one failed."""
    out = dict(get_meta(con, "published") or {})
    err = get_meta(con, "publish_error")
    if err:
        out["error"] = err
    return out
