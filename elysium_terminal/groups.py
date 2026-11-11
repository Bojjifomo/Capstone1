"""Which position NFTs belong together, so two opens in the same pool stay two rows.

One "group" is one thing you opened: a ladder (several slices minted in one transaction)
or a single tight range. The chain only knows NFTs, so:

  * what this terminal opens is written to data/groups.json (exact, with the open time,
    the quote that went in, and how many times it was rebalanced);
  * anything else is grouped from chain facts: v4 NFTs minted in the same block, or
    consecutive token ids whose ranges touch (how a ladder multicall mints its slices).

Pure functions plus one small JSON file. No RPC here.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

FILE = "groups.json"
_lock = threading.Lock()


def tag_of(venue: str) -> str:
    return {"uniswap-v4": "v4", "elysiumswap": "es", "uniswap-v3": "u3"}.get(venue, "x")


def _read(data: Path) -> dict:
    try:
        d = json.loads((Path(data) / FILE).read_text())
        return d if isinstance(d, dict) and isinstance(d.get("groups"), dict) else {"groups": {}}
    except (OSError, ValueError):
        return {"groups": {}}


def _write(data: Path, d: dict) -> None:
    Path(data).mkdir(parents=True, exist_ok=True)
    path = Path(data) / FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(path)


def load(data: Path) -> dict:
    """{gid: {"manager", "ids", "kind", "openedAt", ...}}"""
    with _lock:
        return _read(data)["groups"]


def register(data: Path, venue: str, manager: str, ids: list, kind: str, **meta) -> str | None:
    """Remember NFTs that were minted together. Returns the group id."""
    ids = sorted(int(i) for i in ids or [])
    if not ids:
        return None
    gid = f"{tag_of(venue)}-{ids[0]}"
    with _lock:
        d = _read(data)
        entry = {"venue": venue, "manager": manager.lower(), "ids": ids, "kind": kind, "openedAt": time.time()}
        entry.update({k: v for k, v in meta.items() if v is not None})
        d["groups"][gid] = entry
        _write(data, d)
    return gid


def prune(data: Path, held: set[tuple[str, int]]) -> None:
    """Drop groups whose NFTs are all gone. `held` is {(manager, tokenId)} read from chain just now."""
    with _lock:
        d = _read(data)
        keep = {g: e for g, e in d["groups"].items()
                if any((e.get("manager"), int(i)) in held for i in e.get("ids") or [])}
        if len(keep) != len(d["groups"]):
            d["groups"] = keep
            _write(data, d)


def _touching(a: dict, b: dict) -> bool:
    return a["tickUpper"] == b["tickLower"] or b["tickUpper"] == a["tickLower"]


def group_positions(positions: list[dict], registry: dict, mint_blocks: dict | None = None) -> list[dict]:
    """Split positions into groups. Each result: {"id", "positions", "meta"} (meta = registry entry or {}).

    Positions need tokenId, manager, venue, pool, tickLower, tickUpper.
    """
    mint_blocks = mint_blocks or {}
    by_nft = {}
    for gid, e in (registry or {}).items():
        for i in e.get("ids") or []:
            by_nft[(str(e.get("manager") or "").lower(), int(i))] = gid
    out: dict[str, dict] = {}
    loose: dict[tuple, list[dict]] = {}
    for p in positions:
        gid = by_nft.get((p["manager"].lower(), int(p["tokenId"])))
        if gid:
            out.setdefault(gid, {"id": gid, "positions": [], "meta": registry[gid]})["positions"].append(p)
        else:
            loose.setdefault((p["manager"].lower(), p["pool"]), []).append(p)
    for plist in loose.values():
        plist.sort(key=lambda x: int(x["tokenId"]))
        runs: list[list[dict]] = []
        for p in plist:
            prev = runs[-1][-1] if runs else None
            together = False
            if prev is not None:
                b0, b1 = mint_blocks.get(int(prev["tokenId"])), mint_blocks.get(int(p["tokenId"]))
                if p.get("dex") == "v4" and b0 is not None and b1 is not None:
                    together = b0 == b1
                else:
                    together = int(p["tokenId"]) - int(prev["tokenId"]) == 1 and _touching(prev, p)
            if together:
                runs[-1].append(p)
            else:
                runs.append([p])
        for run in runs:
            gid = f"{tag_of(run[0].get('venue', ''))}-{int(run[0]['tokenId'])}"
            out[gid] = {"id": gid, "positions": run, "meta": {}}
    return list(out.values())
