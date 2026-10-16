#!/usr/bin/env python3
"""LP copy-trade checker for Uniswap V4 positions.

Finds wallets that open/close LP in the same pool with the EXACT same tick
ranges a few seconds after the target wallet (copy-LP bots), and how much of
the pool token they dump after the target wallet closes.

Used from the CLI below or from streamlit_app.py via analyze().

Examples:
  ALCHEMY_API_KEY=xxx python3 checker.py 0xYourWallet
  python3 checker.py 0xYourWallet --events 10 --window 60
  python3 checker.py 0xYourWallet --rpc https://base-mainnet.g.alchemy.com/v2/KEY
"""
import argparse
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

MODIFY_LIQUIDITY = "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
INITIALIZE = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
POOL_KEYS = "0x86b6be7d"  # PositionManager.poolKeys(bytes25)
ZERO = "0x" + "0" * 40
FULL_RANGE_TICK = 887000  # full-range positions are too common, easy to match by chance
QUOTE_SYMBOLS = {"DAI", "WBTC", "CBBTC"}  # plus anything with "USD" / ending in "ETH"; not counted as dumps


class RPCError(Exception):
    pass


class CheckerError(Exception):
    """Error whose message is safe to show to the user as-is."""


class RPC:
    def __init__(self, url, delay):
        self.url = url
        self.delay = delay
        self.calls = 0

    def __call__(self, method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(8):
            time.sleep(self.delay)
            self.calls += 1
            req = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                try:
                    out = json.load(e)  # Alchemy puts a JSON error in the HTTP 400 body
                except ValueError:
                    raise RPCError(f"HTTP {e.code} on {method}") from e
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.5 * 2 ** attempt)
                continue
            err = out.get("error")
            if err:
                if err.get("code") == 429 or "compute units" in str(err.get("message", "")):
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise RPCError(err.get("message", str(err)))
            return out["result"]
        raise RPCError(f"{method} kept failing after retries")


def signed(word):
    v = int(word, 16)
    return v - (1 << 256) if v >= 1 << 255 else v


def short(addr):
    return addr[:8] + "…" + addr[-6:]


def compact(v):
    """801505 -> 801.5K, 3134003 -> 3.1M, 705.77 -> 705.77."""
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.1f}".removesuffix(".0") + suffix
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def fmt_amounts(amounts):
    return " · ".join(f"{compact(v)} {sym}" for sym, v in amounts.items() if v) or "–"


def is_quote(symbol):
    s = symbol.upper()
    return "USD" in s or s.endswith("ETH") or s in QUOTE_SYMBOLS


class Checker:
    def __init__(self, rpc, wallet, log_chunk):
        self.rpc = rpc
        self.wallet = wallet.lower()
        self.log_chunk = log_chunk
        self.tx_from = {}
        self.tokens = {}
        self.pools = {}

    def currencies(self, position_manager, pool, fallback):
        """[currency0, currency1] of a V4 pool; ZERO = native ETH."""
        if pool not in self.pools:
            found = None
            if position_manager:
                try:
                    out = self.rpc("eth_call", [{"to": position_manager,
                                                 "data": POOL_KEYS + pool[2:52] + "0" * 14}, "latest"])
                    c0, c1 = "0x" + out[26:66], "0x" + out[90:130]
                    if c1 != ZERO:
                        found = [c0, c1]
                except (RPCError, TypeError):
                    pass
            self.pools[pool] = found or fallback
        return self.pools[pool]

    def token(self, addr):
        if addr == ZERO:
            return ("ETH", 18)
        if addr not in self.tokens:
            try:
                meta = self.rpc("alchemy_getTokenMetadata", [addr]) or {}
            except RPCError:
                meta = {}
            self.tokens[addr] = (meta.get("symbol") or addr[:8], meta.get("decimals") or 18)
        return self.tokens[addr]

    def block_time(self):
        latest = int(self.rpc("eth_blockNumber", []), 16)
        span = min(200_000, latest - 1)
        t1 = int(self.rpc("eth_getBlockByNumber", [hex(latest), False])["timestamp"], 16)
        t0 = int(self.rpc("eth_getBlockByNumber", [hex(latest - span), False])["timestamp"], 16)
        return latest, (t1 - t0) / span

    def lp_nft_txs(self, limit):
        """Tx hashes where the wallet minted/burned an NFT (LP open/close candidates), newest first."""
        blocks = {}
        directions = ({"fromAddress": ZERO, "toAddress": self.wallet},
                      {"fromAddress": self.wallet, "toAddress": ZERO})
        for direction in directions:
            seen, page = set(), None
            while len(seen) < limit * 2:
                p = {"fromBlock": "0x0", "toBlock": "latest", "category": ["erc721"],
                     "order": "desc", "maxCount": "0x64", **direction}
                if page:
                    p["pageKey"] = page
                res = self.rpc("alchemy_getAssetTransfers", [p])
                for t in res["transfers"]:
                    seen.add(t["hash"])
                    blocks[t["hash"]] = int(t["blockNum"], 16)
                page = res.get("pageKey")
                if not page:
                    break
        return sorted(blocks, key=blocks.get, reverse=True)

    def lp_actions(self, hashes, limit):
        """Read ModifyLiquidity events from the wallet's txs, grouped per tx."""
        actions = []
        for h in hashes:
            rcpt = self.rpc("eth_getTransactionReceipt", [h])
            mods = [l for l in rcpt["logs"] if l["topics"] and l["topics"][0] == MODIFY_LIQUIDITY]
            if not mods:
                continue
            pool_manager = mods[0]["address"].lower()
            groups = defaultdict(set)
            for l in mods:
                d = l["data"][2:]
                lo, hi, delta = signed(d[0:64]), signed(d[64:128]), signed(d[128:192])
                if delta == 0:  # fee collection only
                    continue
                groups[(l["topics"][1], "OPEN" if delta > 0 else "CLOSE")].add((lo, hi))
            if not groups:
                continue
            pool_tokens, received, position_manager = set(), defaultdict(int), None
            for l in rcpt["logs"]:
                if l["topics"] and l["topics"][0] == TRANSFER and len(l["topics"]) == 4:
                    position_manager = position_manager or l["address"].lower()  # position NFT
                if l["topics"] and l["topics"][0] == INITIALIZE and l["address"].lower() == pool_manager:
                    # pool created in the same tx (launchpad): take currencies from the event
                    self.pools[l["topics"][1]] = ["0x" + l["topics"][2][-40:], "0x" + l["topics"][3][-40:]]
                if not l["topics"] or l["topics"][0] != TRANSFER or len(l["topics"]) != 3:
                    continue
                src, dst = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
                if pool_manager in (src, dst):
                    pool_tokens.add(l["address"].lower())
                if dst == self.wallet and l["data"] != "0x":
                    received[l["address"].lower()] += int(l["data"], 16)
            block = int(rcpt["blockNumber"], 16)
            ts = int(self.rpc("eth_getBlockByNumber", [hex(block), False])["timestamp"], 16)
            actions.append({
                "tx": h, "block": block, "tx_index": int(rcpt["transactionIndex"], 16), "ts": ts,
                "pool_manager": pool_manager, "position_manager": position_manager,
                "pool_tokens": sorted(pool_tokens),
                "received": dict(received), "groups": dict(groups),
            })
            if len(actions) >= limit:
                break
        return actions

    def pool_label(self, tokens):
        syms = [self.token(t)[0] for t in tokens]
        if len(syms) == 1:
            syms = ["?"] + syms
        return "/".join(syms) or "?"

    def get_logs(self, address, topics, start, end):
        logs, b = [], start
        while b <= end:
            to = min(end, b + self.log_chunk - 1)
            try:
                logs += self.rpc("eth_getLogs", [{"address": address, "fromBlock": hex(b),
                                                  "toBlock": hex(to), "topics": topics}])
            except RPCError as e:
                m = re.search(r"up to a (\d+) block range", str(e))
                new = int(m.group(1)) if m else max(1, self.log_chunk // 4)
                if new >= self.log_chunk:
                    raise
                self.log_chunk = new  # shrink the chunk (Alchemy free tier: 10 blocks)
                continue
            b = to + 1
        return logs

    def sender(self, tx):
        if tx not in self.tx_from:
            self.tx_from[tx] = self.rpc("eth_getTransactionByHash", [tx])["from"].lower()
        return self.tx_from[tx]

    def outflows(self, addr, tokens, start, end):
        out = defaultdict(float)
        if not tokens:
            return out
        res = self.rpc("alchemy_getAssetTransfers", [{
            "fromBlock": hex(start), "toBlock": hex(end), "fromAddress": addr,
            "contractAddresses": tokens, "category": ["erc20"], "maxCount": "0x3e8"}])
        for t in res["transfers"]:
            out[t.get("asset") or t["rawContract"]["address"][:8]] += t.get("value") or 0
        return out


def analyze(rpc_url, wallet, events=8, window=60.0, chunk=2000, delay=0.12,
            include_full_range=False, progress=None):
    """Run the check and return the result as a dict (no printing)."""
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet or ""):
        raise CheckerError("That doesn't look like a wallet address (0x followed by 40 hex characters).")
    progress = progress or (lambda frac, msg: None)

    c = Checker(RPC(rpc_url, delay), wallet, chunk)
    latest, bt = c.block_time()
    window_blocks = max(1, math.ceil(window / bt))

    progress(0.02, "Finding recent LP opens and closes…")
    actions = c.lp_actions(c.lp_nft_txs(events), events)
    if not actions:
        raise CheckerError("No Uniswap V4 LP activity found for this wallet "
                           "(no position NFTs minted or burned).")
    actions.sort(key=lambda a: a["block"])

    followers = defaultdict(lambda: {"actions": set(), "opens": 0, "closes": 0, "delays": [],
                                     "ranges": 0, "dumped": defaultdict(float)})
    rows = []
    for i, a in enumerate(actions, 1):
        progress(0.05 + 0.93 * (i - 1) / len(actions), f"Scanning LP action {i} of {len(actions)}…")
        end = min(latest, a["block"] + window_blocks)
        pools = sorted({pool for pool, _ in a["groups"]})
        logs = c.get_logs(a["pool_manager"], [MODIFY_LIQUIDITY, pools], a["block"], end)
        received = {c.token(t)[0]: v / 10 ** c.token(t)[1] for t, v in a["received"].items()}

        for (pool, side), ranges in a["groups"].items():
            currencies = c.currencies(a["position_manager"], pool, a["pool_tokens"])
            label = c.pool_label(currencies)
            dump_tokens = [t for t in currencies if t != ZERO and not is_quote(c.token(t)[0])]
            watch = {r for r in ranges if include_full_range
                     or max(abs(r[0]), abs(r[1])) < FULL_RANGE_TICK}
            hits = defaultdict(lambda: {"ranges": set(), "delays": []})
            for l in logs:
                blk, txi = int(l["blockNumber"], 16), int(l["transactionIndex"], 16)
                if (blk, txi) <= (a["block"], a["tx_index"]) or l["topics"][1] != pool:
                    continue
                d = l["data"][2:]
                lo, hi, delta = signed(d[0:64]), signed(d[64:128]), signed(d[128:192])
                if (lo, hi) not in watch or delta == 0 or (delta > 0) != (side == "OPEN"):
                    continue
                who = c.sender(l["transactionHash"])
                if who == c.wallet:
                    continue
                hits[who]["ranges"].add((lo, hi))
                hits[who]["delays"].append((blk - a["block"]) * bt)

            row = {"n": i, "ts": a["ts"], "tx": a["tx"], "side": side, "pool": label,
                   "ranges": len(ranges), "received": received if side == "CLOSE" else {},
                   "followers": []}
            for who, h in sorted(hits.items(), key=lambda kv: min(kv[1]["delays"])):
                f = followers[who]
                f["actions"].add(i)
                f["opens" if side == "OPEN" else "closes"] += 1
                f["delays"].append(min(h["delays"]))
                f["ranges"] += len(h["ranges"])
                out = c.outflows(who, dump_tokens, a["block"], end) if side == "CLOSE" else {}
                for sym, v in out.items():
                    f["dumped"][sym] += v
                row["followers"].append({"wallet": who, "delay": min(h["delays"]),
                                         "matched": len(h["ranges"]), "dumped": dict(out)})
            rows.append(row)

    def level(f):
        if len(f["actions"]) >= 2:
            return "COPIER"
        return "SUSPECT" if f["ranges"] >= 2 else "WEAK"

    summary = [{"wallet": who, "status": level(f), "copied": len(f["actions"]),
                "opens": f["opens"], "closes": f["closes"],
                "median_delay": statistics.median(f["delays"]),
                "dumped": {s: v for s, v in f["dumped"].items() if v}}
               for who, f in followers.items()]
    summary.sort(key=lambda s: (-s["copied"], s["median_delay"]))

    copiers = [s for s in summary if s["status"] == "COPIER"]
    suspects = [s for s in summary if s["status"] == "SUSPECT"]
    dumped_total = defaultdict(float)
    for s in summary:
        for sym, v in s["dumped"].items():
            dumped_total[sym] += v
    copier_delays = [d for w in copiers for d in followers[w["wallet"]]["delays"]]
    progress(1.0, "Done")
    return {
        "wallet": c.wallet, "block_time": bt, "window": window, "n_actions": len(actions),
        "actions": rows, "followers": summary,
        "verdict": "COPIED" if copiers else "SUSPECT" if suspects else "WEAK" if summary else "CLEAN",
        "n_copy": len(copiers), "n_sus": len(suspects),
        "median_delay": statistics.median(copier_delays) if copier_delays else None,
        "dumped_total": dict(dumped_total), "rpc_calls": c.rpc.calls,
    }


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def verdict_text(res):
    if res["verdict"] == "COPIED":
        n, s = res["n_copy"], res["n_sus"]
        text = (f"{plural(n, 'wallet')} {'keeps' if n == 1 else 'keep'} copying these LP moves, "
                f"usually {res['median_delay']:.1f} s after each one.")
        return text + (f" {plural(s, 'more wallet')} {'looks' if s == 1 else 'look'} suspicious." if s else "")
    if res["verdict"] == "SUSPECT":
        return (f"{plural(res['n_sus'], 'wallet')} copied several of these exact ranges, but only once so far. "
                "Check more LP actions to confirm.")
    if res["verdict"] == "WEAK":
        return "Only one-off single-range matches. Probably a coincidence."
    return f"Nobody copied the last {plural(res['n_actions'], 'LP action')}."


def print_report(res):
    print(f"\nWallet {res['wallet']}\nLast {plural(res['n_actions'], 'LP action')}, "
          f"watching {res['window']:.0f} s after each\n")
    for a in res["actions"]:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(a["ts"]))
        print(f"[{a['n']}] {when} UTC  {a['side']:<5} {a['pool']}  {plural(a['ranges'], 'range')}  "
              f"tx {a['tx'][:12]}…")
        if a["received"]:
            print("    you received: " + fmt_amounts(a["received"]))
        if not a["followers"]:
            print("    ✅ nobody copied this\n")
            continue
        delays = [f["delay"] for f in a["followers"]]
        print(f"    🚨 {plural(len(delays), 'wallet')} made the same {a['side'].lower()}, "
              f"{min(delays):.1f}–{max(delays):.1f} s later")
        for f in a["followers"]:
            line = f"      {short(f['wallet'])}  +{f['delay']:5.1f} s  {f['matched']}/{a['ranges']} ranges"
            if f["dumped"]:
                line += "  dumped " + fmt_amounts(f["dumped"])
            print(line)
        print()

    if res["followers"]:
        print("=" * 100)
        print(f"{'wallet':<44} {'status':<8} {'copied':>6} {'opens':>6} {'closes':>7} {'median':>8}  "
              "dumped after your closes")
        print("-" * 100)
        for f in res["followers"]:
            print(f"{f['wallet']:<44} {f['status']:<8} {f['copied']:>4}/{res['n_actions']:<1} {f['opens']:>6} "
                  f"{f['closes']:>7} {f['median_delay']:>6.1f} s  {fmt_amounts(f['dumped'])}")
        print("=" * 100)
    icon = {"COPIED": "🚨", "SUSPECT": "⚠️ ", "WEAK": "ℹ️ ", "CLEAN": "✅"}[res["verdict"]]
    print(f"\n{icon} {res['verdict']}: {verdict_text(res)}")
    print("Tip: close and swap in one transaction (multicall) so bots can't dump before you, "
          "or rotate wallets.")


def main():
    ap = argparse.ArgumentParser(description="Check whether a wallet's Uniswap V4 LP is being copy-traded.")
    ap.add_argument("wallet")
    ap.add_argument("--network", default="robinhood-mainnet",
                    help="Alchemy network name (default: robinhood-mainnet)")
    ap.add_argument("--rpc", help="full Alchemy RPC URL (overrides --network/ALCHEMY_API_KEY)")
    ap.add_argument("--events", type=int, default=8, help="how many recent LP open/close txs to check")
    ap.add_argument("--window", type=float, default=60, help="seconds to watch after each of your actions")
    ap.add_argument("--chunk", type=int, default=2000, help="blocks per eth_getLogs call (shrinks automatically)")
    ap.add_argument("--delay", type=float, default=0.12, help="pause between requests, in seconds")
    ap.add_argument("--include-full-range", action="store_true",
                    help="also match full-range positions (prone to false positives)")
    args = ap.parse_args()

    url = args.rpc
    if not url:
        key = os.environ.get("ALCHEMY_API_KEY")
        if not key:
            sys.exit("Set ALCHEMY_API_KEY or pass --rpc https://<network>.g.alchemy.com/v2/<key>")
        url = f"https://{args.network}.g.alchemy.com/v2/{key}"

    def progress(frac, msg):
        print(f"[{frac:4.0%}] {msg}", file=sys.stderr, flush=True)

    try:
        res = analyze(url, args.wallet, args.events, args.window, args.chunk, args.delay,
                      args.include_full_range, progress)
    except (CheckerError, RPCError) as e:
        sys.exit(str(e))
    print_report(res)
    print(f"({res['rpc_calls']} RPC calls)", file=sys.stderr)


if __name__ == "__main__":
    main()
