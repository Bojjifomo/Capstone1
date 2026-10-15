"""USD prices: Jupiter Price API first, on-chain DLMM pool prices as fallback."""

import os

import requests

from .dlmm import account_bytes, bin_price, decode_lb_pair
from .tokens import SOFT_STABLES, SOL_MINT, STABLES

# Deep SOL-USDC DLMM pool used to price SOL when Jupiter is unreachable.
SOL_USDC_POOL = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6"


class PriceOracle:
    def __init__(self, rpc, tokens):
        self.rpc = rpc
        self.tokens = tokens
        self.prices = {}
        self.source = "-"
        self.jup_ok = None

    def _jupiter(self, mints):
        key = os.environ.get("JUPITER_API_KEY")
        if key:
            base, headers = "https://api.jup.ag/price/v3", {"x-api-key": key}
        else:
            base, headers = "https://lite-api.jup.ag/price/v3", {}
        out = {}
        mints = list(mints)
        for i in range(0, len(mints), 50):
            r = requests.get(base, params={"ids": ",".join(mints[i:i + 50])}, headers=headers, timeout=10)
            r.raise_for_status()
            for mint, v in (r.json() or {}).items():
                if v and v.get("usdPrice") is not None:
                    out[mint] = float(v["usdPrice"])
        return out

    def refresh(self, mints, pools=()):
        """pools: iterable of LbPair used to derive prices of their token X."""
        mints = set(mints) | {SOL_MINT}
        prices = {m: 1.0 for m in mints if m in STABLES}
        if self.jup_ok is not False:
            try:
                prices.update(self._jupiter(mints - set(prices)))
                self.jup_ok = True
                self.source = "jupiter"
            except Exception:
                self.jup_ok = False
        if SOL_MINT not in prices:
            try:
                pair = decode_lb_pair(SOL_USDC_POOL, account_bytes(
                    self.rpc.call("getAccountInfo", [SOL_USDC_POOL, {"encoding": "base64"}])["value"]))
                prices[SOL_MINT] = bin_price(pair.active_id, pair.bin_step, 9, 6)
                self.source = "on-chain DLMM"
            except Exception:
                pass
        for m in mints & SOFT_STABLES:
            prices.setdefault(m, 1.0)
        # Anything still missing: derive it from a pool whose other side is priced.
        for pair in pools:
            x, y = pair.token_x_mint, pair.token_y_mint
            if (x in prices) == (y in prices):
                continue
            tx, ty = self.tokens.get(x), self.tokens.get(y)
            px = bin_price(pair.active_id, pair.bin_step, tx.decimals, ty.decimals)  # Y per X
            if y in prices:
                prices[x] = px * prices[y]
            elif px:
                prices[y] = prices[x] / px
        self.prices.update(prices)
        return self.prices

    def usd(self, mint, ui_amount):
        if not ui_amount:
            return 0.0
        p = self.prices.get(mint)
        return None if p is None else p * ui_amount
