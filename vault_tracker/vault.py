"""Tracks one wallet / vault: DLMM positions, spot balances and activity events.

Position discovery works on any RPC (no getProgramAccounts needed): the wallet's
transaction history is scanned for DLMM PositionCreate / AddLiquidity /
RemoveLiquidity / Rebalancing / PositionClose events. getProgramAccounts is
used additionally when the RPC allows it (--gpa).
"""

import json
import os
import time

import base58

from . import dlmm
from .dlmm import account_bytes
from .tokens import SOL_MINT

MAX_EVENTS_KEPT = 300
POSITION_DISC_B58 = base58.b58encode(dlmm.DISC_POSITION_V2).decode()
SOL_DUST = 0.005  # ignore SOL deltas below this (rent for ATAs, fees)


def _now():
    return int(time.time())


def _merge_same_tx(events):
    """One tx often emits several AddLiquidity/RemoveLiquidity/ClaimFee events for the same
    position (one per bin-array chunk). Collapse them into a single event."""
    merged, index = [], {}
    sums = {"ADD": ("amount_x", "amount_y"), "REMOVE": ("amount_x", "amount_y"), "CLAIM": ("fee_x", "fee_y")}
    for e in events:
        key = (e["type"], e.get("position"))
        if e["type"] in sums and key in index:
            tgt = merged[index[key]]
            for f in sums[e["type"]]:
                tgt[f] += e[f]
            continue
        index[key] = len(merged)
        merged.append(dict(e))
    # Fold the deposit of an OPEN / the withdrawal of a CLOSE in the same tx into that event.
    absorbed = set()
    for e in merged:
        if e["type"] in ("OPEN", "CLOSE"):
            key = ("ADD" if e["type"] == "OPEN" else "REMOVE", e["position"])
            if key in index:
                src = merged[index[key]]
                e["amount_x"], e["amount_y"] = src["amount_x"], src["amount_y"]
                absorbed.add(index[key])
    return [e for i, e in enumerate(merged) if i not in absorbed]


class VaultTracker:
    def __init__(self, rpc, tokens, prices, address, label="leader", state_dir=".vault_tracker",
                 use_gpa=False):
        self.rpc = rpc
        self.tokens = tokens
        self.prices = prices
        self.address = address
        self.label = label
        self.use_gpa = use_gpa
        self.state_path = os.path.join(state_dir, f"{label}-{address}.json")
        self.state = self._load_state()
        self.snapshot = None
        self._rent_cache = {}
        self._pairs = {}

    # ---- persistence ----------------------------------------------------------

    def _load_state(self):
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"address": self.address, "last_signature": None, "holders": [self.address],
                    "positions": {}, "events": []}

    def save(self):
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_path)

    @property
    def bootstrapped(self):
        return self.state.get("last_signature") is not None

    def _remember(self, events):
        self.state["events"] = (self.state["events"] + events)[-MAX_EVENTS_KEPT:]

    # ---- transaction history --------------------------------------------------

    def bootstrap(self, history_limit=300, on_progress=None):
        """First run: replay recent history silently to learn current positions."""
        sigs, before = [], None
        while len(sigs) < history_limit:
            page = self.rpc.get_signatures(self.address, limit=min(1000, history_limit - len(sigs)),
                                           before=before)
            if not page:
                break
            sigs.extend(page)
            before = page[-1]["signature"]
            if len(page) < 1000:
                break
        events = self._process_signatures(sigs, on_progress)
        if sigs:
            self.state["last_signature"] = sigs[0]["signature"]
        else:
            self.state["last_signature"] = ""
        for e in events:
            e["historical"] = True
        self._remember(events)
        return events

    def poll_transactions(self, max_new=500):
        """New events since the last processed signature."""
        until = self.state.get("last_signature") or None
        sigs, before = [], None
        while len(sigs) < max_new:
            page = self.rpc.get_signatures(self.address, limit=min(1000, max_new), before=before,
                                           until=until)
            if not page:
                break
            sigs.extend(page)
            before = page[-1]["signature"]
            if len(page) < min(1000, max_new):
                break
        if not sigs:
            return []
        events = self._process_signatures(sigs)
        self.state["last_signature"] = sigs[0]["signature"]
        self._remember(events)
        return events

    def _process_signatures(self, sigs, on_progress=None):
        events = []
        ordered = [s for s in reversed(sigs) if not s.get("err")]
        for i, s in enumerate(ordered):
            if on_progress:
                on_progress(i + 1, len(ordered))
            try:
                tx = self.rpc.get_transaction(s["signature"])
            except Exception:
                continue
            if tx and not (tx.get("meta") or {}).get("err"):
                events.extend(self._process_tx(s["signature"], tx))
        return events

    def _register_position(self, position, lb_pair=None, owner=None, sig=None, ts=None, opened=False):
        p = self.state["positions"].get(position)
        if p is None:
            p = self.state["positions"][position] = {
                "lb_pair": lb_pair, "owner": owner, "status": "open",
                "opened_sig": sig if opened else None, "opened_at": ts if opened else None,
                "closed_sig": None, "closed_at": None, "in_range": None,
            }
        if lb_pair and not p.get("lb_pair"):
            p["lb_pair"] = lb_pair
        if owner and owner not in self.state["holders"]:
            self.state["holders"].append(owner)
        return p

    def _process_tx(self, sig, tx):
        ts = tx.get("blockTime") or _now()
        out = []
        dlmm_events = dlmm.tx_dlmm_events(tx)
        for e in dlmm_events:
            pos = e["position"]
            base = {"sig": sig, "ts": ts, "position": pos, "wallet": self.label}
            # Only PositionCreate/PositionClose carry the real position owner; the "from" of
            # Add/RemoveLiquidity may be an operator key whose own funds are not the vault's.
            if e["event"] == "PositionCreate":
                prev = self.state["positions"].get(pos)
                known_open = prev is not None and prev["status"] == "open"
                p = self._register_position(pos, e["lb_pair"], e["owner"], sig, ts, opened=True)
                # PDA positions can be re-created at the same address after a close.
                p.update(status="open", opened_sig=sig, opened_at=ts, closed_sig=None, closed_at=None)
                if not known_open:  # else: already alerted when discovered via getProgramAccounts
                    out.append({**base, "type": "OPEN", "lb_pair": e["lb_pair"]})
            elif e["event"] == "PositionClose":
                p = self._register_position(pos, owner=e["owner"])
                already = p["status"] == "closed"  # refresh() saw the account vanish first
                p.update(status="closed", closed_sig=sig, closed_at=ts)
                if not already:
                    out.append({**base, "type": "CLOSE", "lb_pair": p.get("lb_pair")})
            elif e["event"] in ("AddLiquidity", "RemoveLiquidity"):
                self._register_position(pos, e["lb_pair"])
                out.append({**base, "type": "ADD" if e["event"] == "AddLiquidity" else "REMOVE",
                            "lb_pair": e["lb_pair"], "amount_x": e["amount_x"], "amount_y": e["amount_y"],
                            "active_bin_id": e["active_bin_id"]})
            elif e["event"] == "Rebalancing":
                self._register_position(pos, e["lb_pair"])
                out.append({**base, "type": "REBALANCE", "lb_pair": e["lb_pair"],
                            "old_range": [e["old_min_id"], e["old_max_id"]],
                            "new_range": [e["new_min_id"], e["new_max_id"]],
                            "active_bin_id": e["active_bin_id"]})
            elif e["event"] == "ClaimFee":
                self._register_position(pos, e["lb_pair"])
                out.append({**base, "type": "CLAIM", "lb_pair": e["lb_pair"],
                            "fee_x": e["fee_x"], "fee_y": e["fee_y"]})
        if not dlmm_events:
            swap = self._balance_change_event(sig, ts, tx)
            if swap:
                out.append(swap)
        return _merge_same_tx(out)

    def _balance_change_event(self, sig, ts, tx):
        meta = tx.get("meta") or {}
        holders = set(self.state["holders"])
        deltas = {}
        for side, entries in ((-1, meta.get("preTokenBalances") or []), (1, meta.get("postTokenBalances") or [])):
            for b in entries:
                if b.get("owner") not in holders:
                    continue
                amt = int(b["uiTokenAmount"]["amount"]) / 10 ** b["uiTokenAmount"]["decimals"]
                deltas[b["mint"]] = deltas.get(b["mint"], 0.0) + side * amt
        keys = dlmm.tx_account_keys(tx)
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        for i, k in enumerate(keys):
            if k in holders and i < len(pre) and i < len(post):
                d = (post[i] - pre[i] + (meta.get("fee", 0) if i == 0 else 0)) / 1e9
                deltas[SOL_MINT] = deltas.get(SOL_MINT, 0.0) + d
        if abs(deltas.get(SOL_MINT, 0.0)) < SOL_DUST:
            deltas.pop(SOL_MINT, None)
        deltas = {m: d for m, d in deltas.items() if abs(d) > 1e-12}
        if not deltas:
            return None
        sent = {m: -d for m, d in deltas.items() if d < 0}
        recv = {m: d for m, d in deltas.items() if d > 0}
        kind = "SWAP" if sent and recv else ("TRANSFER_IN" if recv else "TRANSFER_OUT")
        return {"sig": sig, "ts": ts, "wallet": self.label, "type": kind, "sent": sent, "received": recv}

    def pair_info(self, lb_pair):
        """Decoded LbPair + token infos for a pool (cached; pools are never closed)."""
        if lb_pair not in self._pairs:
            acc = self.rpc.call("getAccountInfo", [lb_pair, {"encoding": "base64"}])["value"]
            pair = dlmm.decode_lb_pair(lb_pair, account_bytes(acc))
            self.tokens.load([pair.token_x_mint, pair.token_y_mint])
            self._pairs[lb_pair] = pair
        pair = self._pairs[lb_pair]
        return pair, self.tokens.get(pair.token_x_mint), self.tokens.get(pair.token_y_mint)

    # ---- live state -----------------------------------------------------------

    def _discover_gpa(self):
        found = []
        for holder in self.state["holders"]:
            try:
                res = self.rpc.call("getProgramAccounts", [dlmm.DLMM_PROGRAM, {
                    "encoding": "base64", "dataSlice": {"offset": 8, "length": 32},
                    "filters": [{"memcmp": {"offset": 0, "bytes": POSITION_DISC_B58}},
                                {"memcmp": {"offset": 40, "bytes": holder}}]}], retries=2)
            except Exception:
                self.use_gpa = False  # RPC does not allow it; rely on tx history only
                return found
            for a in res:
                if a["pubkey"] not in self.state["positions"]:
                    lb_pair = dlmm._pk(account_bytes(a["account"]))
                    self._register_position(a["pubkey"], lb_pair, holder)
                    found.append(a["pubkey"])
        return found

    def _free_lamports(self, address):
        info = self.rpc.call("getAccountInfo", [address, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])
        acc = info.get("value")
        if not acc:
            return 0
        space = acc.get("space") or 0
        if space == 0:
            return acc["lamports"]
        if space not in self._rent_cache:
            self._rent_cache[space] = self.rpc.call("getMinimumBalanceForRentExemption", [space])
        return max(0, acc["lamports"] - self._rent_cache[space])

    def refresh(self, emit=True):
        """Read live on-chain state. Returns (snapshot, state-derived events)."""
        events = []
        if self.use_gpa:
            for pk in self._discover_gpa():
                if emit and self.bootstrapped:
                    p = self.state["positions"][pk]
                    events.append({"type": "OPEN", "ts": _now(), "sig": None, "position": pk,
                                   "lb_pair": p["lb_pair"], "wallet": self.label, "discovered": True})

        open_keys = [k for k, p in self.state["positions"].items() if p["status"] == "open"]
        accs = self.rpc.get_multiple_accounts(open_keys) if open_keys else []
        positions = []
        for key, acc in zip(open_keys, accs):
            data = account_bytes(acc)
            if data is None or acc.get("owner") != dlmm.DLMM_PROGRAM:
                p = self.state["positions"][key]
                p.update(status="closed", closed_at=p.get("closed_at") or _now())
                if emit:
                    events.append({"type": "CLOSE", "ts": _now(), "sig": None, "position": key,
                                   "lb_pair": p.get("lb_pair"), "wallet": self.label, "detected": True})
                continue
            try:
                pos = dlmm.decode_position(key, data)
            except ValueError:
                continue
            positions.append(pos)
            if pos.owner not in self.state["holders"]:
                self.state["holders"].append(pos.owner)

        pair_keys = sorted({p.lb_pair for p in positions})
        pairs = {}
        for k, acc in zip(pair_keys, self.rpc.get_multiple_accounts(pair_keys) if pair_keys else []):
            if acc:
                pairs[k] = self._pairs[k] = dlmm.decode_lb_pair(k, account_bytes(acc))

        bin_keys = []
        for p in positions:
            bin_keys.extend(dlmm.bin_arrays_for_range(p.lb_pair, p.lower_bin_id, p.upper_bin_id))
        bin_keys = list(dict.fromkeys(bin_keys))
        bins_by_pair = {}
        for acc in (self.rpc.get_multiple_accounts(bin_keys) if bin_keys else []):
            data = account_bytes(acc)
            if data is None:
                continue
            _, bins = dlmm.decode_bin_array(data)
            pair = dlmm._pk(data[24:56])
            bins_by_pair.setdefault(pair, {}).update(bins)

        # Spot balances of every address that owns the vault's assets.
        spot = {}
        for holder in self.state["holders"]:
            lamports = self._free_lamports(holder)
            if lamports:
                spot[SOL_MINT] = spot.get(SOL_MINT, 0) + lamports / 1e9
            for ta in self.rpc.get_token_accounts(holder):
                info = ta["account"]["data"]["parsed"]["info"]
                amt = info["tokenAmount"]
                spot[info["mint"]] = spot.get(info["mint"], 0) + int(amt["amount"]) / 10 ** amt["decimals"]

        mints = set(spot)
        for pair in pairs.values():
            mints |= {pair.token_x_mint, pair.token_y_mint}
        self.tokens.load(mints)
        self.prices.refresh(mints, pairs.values())

        pos_rows = []
        for p in positions:
            pair = pairs.get(p.lb_pair)
            if pair is None:
                continue
            tx, ty = self.tokens.get(pair.token_x_mint), self.tokens.get(pair.token_y_mint)
            am = dlmm.position_amounts(p, bins_by_pair.get(p.lb_pair, {}))
            ax, ay = tx.ui(am.amount_x), ty.ui(am.amount_y)
            fx, fy = tx.ui(am.fee_x), ty.ui(am.fee_y)
            vx, vy = self.prices.usd(tx.mint, ax), self.prices.usd(ty.mint, ay)
            fvx, fvy = self.prices.usd(tx.mint, fx), self.prices.usd(ty.mint, fy)
            in_range = p.lower_bin_id <= pair.active_id <= p.upper_bin_id
            if pair.active_id > p.upper_bin_id:
                side = "below"   # range below price -> holds only Y (bid / buy side)
            elif pair.active_id < p.lower_bin_id:
                side = "above"   # range above price -> holds only X (ask / sell side)
            else:
                side = "around"
            row = {
                "address": p.address, "lb_pair": p.lb_pair, "owner": p.owner,
                "pair": f"{tx.symbol}-{ty.symbol}", "x": tx.mint, "y": ty.mint,
                "symbol_x": tx.symbol, "symbol_y": ty.symbol,
                "lower_bin": p.lower_bin_id, "upper_bin": p.upper_bin_id, "active_bin": pair.active_id,
                "bin_step": pair.bin_step, "width": p.width,
                "price_lower": dlmm.bin_price(p.lower_bin_id, pair.bin_step, tx.decimals, ty.decimals),
                "price_upper": dlmm.bin_price(p.upper_bin_id, pair.bin_step, tx.decimals, ty.decimals),
                "price": dlmm.bin_price(pair.active_id, pair.bin_step, tx.decimals, ty.decimals),
                "decimals_x": tx.decimals, "decimals_y": ty.decimals,
                "in_range": in_range, "side": side,
                "amount_x": ax, "amount_y": ay, "fee_x": fx, "fee_y": fy,
                "value_usd": None if vx is None or vy is None else vx + vy,
                "fees_usd": None if fvx is None or fvy is None else fvx + fvy,
                "has_liquidity": bool(am.amount_x or am.amount_y),
                "opened_at": self.state["positions"][p.address].get("opened_at"),
            }
            pos_rows.append(row)
            st = self.state["positions"][p.address]
            if row["has_liquidity"] and st.get("in_range") is not None and st["in_range"] != in_range and emit:
                events.append({"type": "IN_RANGE" if in_range else "OUT_OF_RANGE", "ts": _now(), "sig": None,
                               "position": p.address, "lb_pair": p.lb_pair, "wallet": self.label})
            st.update(in_range=in_range, lower=p.lower_bin_id, upper=p.upper_bin_id,
                      pair=row["pair"], last_value_usd=row["value_usd"])

        spot_rows = []
        for mint, amt in spot.items():
            t = self.tokens.get(mint)
            spot_rows.append({"mint": mint, "symbol": t.symbol, "amount": amt, "value_usd": self.prices.usd(mint, amt)})

        total = sum(r["value_usd"] or 0 for r in pos_rows) + sum(r["value_usd"] or 0 for r in spot_rows)
        self.snapshot = {"address": self.address, "label": self.label, "ts": _now(), "total_usd": total,
                         "positions": pos_rows, "spot": spot_rows, "holders": list(self.state["holders"]),
                         "price_source": self.prices.source}
        self._remember(events)
        return self.snapshot, events
