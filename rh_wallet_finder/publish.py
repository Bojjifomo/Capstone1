"""Publish a read-only snapshot of the boards for the web showcase
(rh_wallet_finder/web, hosted on Vercel).

This computer stays the engine: after each ranking the worker uploads one
gzipped JSON file to the Vercel project's Blob store, and the site shows it.
Only public chain data goes out: wallet addresses and the figures worked out
from their LP positions. Labels, notes, the watchlist, LP Agent results, RH
Smart LP Finder rankings and every key stay here.

Two ways up:
  - through the site (VERCEL_PUBLISH_URL, https://<site>/api/publish?key=…): PUT
    the gzipped file with the key; the site's own function writes it to the
    project's Blob store with Vercel's OIDC credential. Nothing to renew.
  - straight to Blob (VERCEL_BLOB_TOKEN), as @vercel/blob's put() does it (api
    version 13): PUT https://vercel.com/api/blob/?pathname=rhwf/snapshot.json.gz
    with authorization: Bearer <BLOB_READ_WRITE_TOKEN>, x-vercel-blob-access, ...
    The tokens the Vercel dashboard shows for a store now expire, so this one
    stops working after a while.
A Blob store is created either private or public and only takes uploads of its
own kind: the first upload finds out which, and later ones remember it.
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
MAX_ROWS = 6000  # per view (the site sends the snapshot gzipped: ~1 MB per 3,000 wallets)
VIEWS = ("raw", "real", "d1raw", "d1real")

# what the page shows and filters on; nothing personal
FIELDS = ("addr", "kind", "kind_fit", "score", "verdict", "worth", "net", "roi_nw", "roi_win", "net_7d", "momentum",
          "net_1d", "avg_month", "pnl_3m", "pnl_all", "roi_all", "hist_from", "float", "total", "roi", "win", "win_pos",
          "pf", "daily", "fees", "dd", "recover", "recover_open", "usdg", "size", "duration", "trades", "closed_trades",
          "closed", "flat", "style", "style_conf", "sim_pct", "range", "mcap", "open", "concur", "copy", "copiers",
          "exposure", "open_now", "first_seen", "last_seen", "span_days")


class PublishError(Exception):
    pass


class WrongAccess(PublishError):
    """The store only takes the other kind of upload (private / public)."""

    def __init__(self, msg, access):
        super().__init__(msg)
        self.access = access


def _num(v):
    """Floats to 6 significant digits: exact to the dollar up to $999,999 for the raw
    data export, and the file stays small."""
    if isinstance(v, float):
        return None if v != v or v in (float("inf"), float("-inf")) else float(f"{v:.6g}")
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


EXPIRED = ("Blob tokens copied from the Vercel dashboard expire now. Publish through your site instead: paste its "
           "publish link (https://<your site>/api/publish?key=…) in Settings → Web showcase.")
BAD_TOKEN = ("That isn't a Vercel Blob read-write token. It looks like vercel_blob_rw_<store id>_<secret>: in "
             "Vercel open Storage → your Blob store → .env.local and copy the value of BLOB_READ_WRITE_TOKEN "
             "(only what's between the quotes) into Settings.")


def upload(data, token, pathname=PATHNAME, access="private", timeout=60):
    """PUT bytes to the Blob store behind token -> the blob's URL."""
    token = config.blob_token(token)
    store = config.blob_store_id(token)
    if not store:
        raise PublishError(BAD_TOKEN)
    req = urllib.request.Request(
        BLOB_API + "?" + urllib.parse.urlencode({"pathname": pathname}), data=data, method="PUT", headers={
            "authorization": f"Bearer {token}", "x-vercel-blob-store-id": store,
            "x-api-version": "13", "x-vercel-blob-access": access,
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
        for kind in ("private", "public"):  # "Cannot use public access on a private store."
            if f"on a {kind} store" in msg.lower():
                raise WrongAccess(msg, kind) from e
        hint = f" {BAD_TOKEN}" if "store id" in msg.lower() else f" {EXPIRED}" if e.code in (401, 403) else ""
        raise PublishError(f"Vercel Blob said HTTP {e.code}: {msg}.{hint}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PublishError(f"Can't reach Vercel Blob: {e}") from e
    if not isinstance(out, dict) or not out.get("url"):
        raise PublishError(f"Unexpected answer from Vercel Blob: {str(out)[:200]}")
    return out["url"]


def upload_site(data, link, timeout=60):
    """PUT the gzipped snapshot to the site's /api/publish -> the blob's URL."""
    endpoint, key = config.split_publish_link(link)
    if not key:
        raise PublishError("The publish link has no key: copy the whole link, …/api/publish?key=…")
    req = urllib.request.Request(endpoint, data=data, method="PUT", headers={
        "authorization": f"Bearer {key}", "content-type": "application/octet-stream",
        "user-agent": "rh-wallet-finder/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode("utf-8", "replace")
        try:
            msg = json.loads(body)["error"]
        except (ValueError, KeyError, TypeError):
            msg = f"HTTP {e.code}" + (": the site has no /api/publish yet (redeploy it)" if e.code == 404 else "")
        raise PublishError(f"The web showcase said: {msg}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PublishError(f"Can't reach the web showcase: {e}") from e
    if not isinstance(out, dict) or "url" not in out:
        raise PublishError(f"Unexpected answer from the web showcase: {str(out)[:200]}")
    return out["url"]


def publish(con, cfg=None, log=print):
    """Build the snapshot and upload it (through the site's publish link if set,
    else straight to Blob); remembers when and where in meta."""
    cfg = cfg or config.load_env()
    link, token = cfg.get("VERCEL_PUBLISH_URL"), cfg.get("VERCEL_BLOB_TOKEN")
    if not (link or token):
        raise PublishError("Nowhere to publish yet: paste the site's publish link in Settings (Web showcase).")
    snap = snapshot(con)
    data = gzip.compress(json.dumps(snap, separators=(",", ":")).encode(), 9)
    try:
        if link:
            url = upload_site(data, link)
        else:
            access = get_meta(con, "blob_access") or "private"
            try:
                url = upload(data, token, access=access)
            except WrongAccess as e:  # the store is the other kind: say so once, then remember it
                access = e.access
                url = upload(data, token, access=access)
            set_meta(con, "blob_access", access)
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
