"""Find wallets that open the same DLMM positions as the tracked vault.

For every position the vault opened, read the pool's transactions in a time window around
that open, collect positions other wallets created there, and score each wallet by how often
it shows up with a similar bin range. A normal wallet that keeps matching the vault can be
followed with any wallet copy-trade tool:

- it opens BEFORE the vault  -> possibly the signal source (or the manager's own wallet)
- it opens AFTER the vault   -> most likely someone already copying this vault
"""

import statistics

from . import dlmm

SLOT_SECONDS = 0.4


def _overlap(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    inter = max(0, hi - lo + 1)
    union = max(a[1], b[1]) - min(a[0], b[0]) + 1
    return inter / union if union > 0 else 0.0


def _cursor_after(rpc, slot, tip):
    """A signature from the first produced block at/after `slot` (used as a `before` cursor)."""
    if slot >= tip:
        return None
    for k in range(30):
        try:
            b = rpc.call("getBlock", [slot + k, {"transactionDetails": "signatures", "rewards": False,
                                                  "maxSupportedTransactionVersion": 1}], retries=2)
        except Exception:
            continue  # skipped slot
        if b and b.get("signatures"):
            return b["signatures"][0]
    return None


def _sigs(rpc, address, before=None, until=None, min_time=None, cap=400):
    out = []
    while len(out) < cap:
        page = rpc.get_signatures(address, limit=min(1000, cap - len(out)), before=before, until=until)
        if not page:
            break
        for s in page:
            if min_time and (s.get("blockTime") or 0) < min_time:
                return out
            if not s.get("err"):
                out.append(s)
        before = page[-1]["signature"]
        if len(page) < min(1000, cap - len(out)):
            break
    return out


def _vault_opens(tracker, max_events):
    opens = [e for e in tracker.state["events"] if e.get("type") == "OPEN" and e.get("sig") and e.get("lb_pair")]
    return opens[-max_events:]


def find_mirrors(tracker, window=600, max_events=15, max_txs=400, on_progress=None):
    """Returns (rows, info). rows: one dict per wallet, best match first."""
    rpc = tracker.rpc
    own = set(tracker.state["holders"])
    signers = tracker.state.get("signers", {})
    opens = _vault_opens(tracker, max_events)
    tip = rpc.call("getSlot")
    hits = {}      # owner -> {event_index: (sim, delay, pool, position, range)}
    scanned = 0
    for i, ev in enumerate(opens):
        if on_progress:
            on_progress(i + 1, len(opens), ev)
        vtx = rpc.get_transaction(ev["sig"])
        if not vtx:
            continue
        vrange = tuple(ev["range"]) if ev.get("range") else dlmm.tx_position_inits(vtx).get(ev["position"])
        if not vrange:
            continue
        slot, ts = vtx["slot"], vtx.get("blockTime") or ev["ts"]
        pool = ev["lb_pair"]
        cursor = _cursor_after(rpc, slot + int(window / SLOT_SECONDS), tip)
        after = _sigs(rpc, pool, before=cursor, until=ev["sig"], cap=max_txs)
        after = [s for s in after if (s.get("blockTime") or 0) <= ts + window]
        before = _sigs(rpc, pool, before=ev["sig"], min_time=ts - window, cap=max_txs)
        txs = rpc.get_transactions([s["signature"] for s in after + before])
        scanned += len(txs)
        for sig, tx in txs.items():
            if not tx or (tx.get("meta") or {}).get("err"):
                continue
            inits = None
            for e in dlmm.tx_dlmm_events(tx):
                if e["event"] != "PositionCreate" or e["lb_pair"] != pool or e["owner"] in own:
                    continue
                inits = inits if inits is not None else dlmm.tx_position_inits(tx)
                rng = inits.get(e["position"])
                if not rng:
                    continue
                sim = _overlap(vrange, rng)
                if sim <= 0:
                    continue
                delay = (tx.get("blockTime") or ts) - ts
                prev = hits.setdefault(e["owner"], {}).get(i)
                if prev is None or (sim, -abs(delay)) > (prev[0], -abs(prev[1])):
                    hits[e["owner"]][i] = (sim, delay, pool, e["position"], rng)
    rows = score(hits, len(opens), signers)
    info = {"vault_opens": len(opens), "txs_scanned": scanned, "window_s": window,
            "position_owners": sorted(own)}
    return rows, info


def score(hits, n_events, signers=()):
    """hits: {owner: {event_index: (overlap, delay_s, pool, position, range)}} -> ranked rows."""
    rows = []
    for owner, per_event in hits.items():
        sims = [h[0] for h in per_event.values()]
        delays = [h[1] for h in per_event.values()]
        med = statistics.median(delays)
        avg = sum(sims) / len(sims)
        # strong = keeps showing up with a similar range, not a one-off in a busy pool
        strong = len(per_event) >= max(2, round(0.5 * n_events)) and avg >= 0.6
        if owner in signers:
            label = "manager (juga menandatangani tx vault)"
        elif not strong:
            label = "lemah / kebetulan"
        elif med < 0:
            label = "buka SEBELUM vault: sumber sinyal?"
        else:
            label = "buka SETELAH vault: copytrader?"
        rows.append({
            "wallet": owner, "matches": len(per_event), "events": n_events,
            "exact": sum(1 for s in sims if s >= 0.95), "avg_overlap": round(sum(sims) / len(sims), 3),
            "median_delay_s": round(med), "pools": sorted({h[2] for h in per_event.values()}),
            "label": label, "strong": strong or owner in signers,
        })
    rows.sort(key=lambda r: (not r["strong"], -r["matches"], -r["exact"], -r["avg_overlap"], abs(r["median_delay_s"])))
    return rows
