"""Client for the JSON endpoints the synths.lol web app calls.

The endpoint URLs are not hard-coded: `synthmon discover` records what the app
actually requests and writes URL templates into the config. The response
shapes are read with tolerant field lookup (camelCase, snake_case and common
synonyms). When discovery shows a field the heuristics miss, pin it in config:

    [synths.fields.trades]
    eth_amount = "amounts.native"
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from ..http import Http, SourceError, redact
from ..models import Bag, CoinInfo, CoinStats, Post, Trade
from ..util import TX_HASH_RE, is_address, norm_addr, to_eth, to_float, to_ts

LIST_KEYS = (
    "data", "items", "results", "rows", "nodes", "edges", "records", "list",
    "posts", "trades", "swaps", "activity", "transactions", "txs",
    "bags", "holdings", "positions", "balances", "portfolio",
    "coins", "tokens", "launched", "launches",
    "leaderboard", "entries", "rankings", "synths", "agents", "holders",
)

FIELDS: dict[str, dict[str, list[str]]] = {
    "trades": {
        "id": ["id", "_id", "tradeId", "uuid"],
        "ts": ["timestamp", "createdAt", "time", "blockTimestamp", "executedAt", "date", "ts"],
        "side": ["side", "type", "action", "direction", "tradeType", "kind", "isBuy"],
        # Not "address": on a trade record that is as likely to be the trader as the coin.
        "coin": ["coin.address", "token.address", "coinAddress", "tokenAddress", "coin.id", "token.id", "mint",
                 "contractAddress", "coin", "token"],
        "symbol": ["coin.symbol", "token.symbol", "symbol", "ticker", "coin.ticker", "token.ticker"],
        "eth_amount": ["ethAmount", "amountEth", "eth", "ethValue", "valueEth", "nativeAmount", "amountNative",
                       "quoteAmount", "cost", "proceeds", "total", "value"],
        "token_amount": ["tokenAmount", "amountToken", "tokens", "baseAmount", "amount", "quantity", "qty"],
        "tx_hash": ["txHash", "transactionHash", "hash", "tx", "txid", "signature"],
        "gas": ["gasFeeEth", "gasFee", "gas", "fee", "networkFee"],
        "reason": ["reason", "exitReason", "exitType", "trigger", "strategy", "note", "rationale"],
    },
    "posts": {
        "id": ["id", "_id", "postId", "uuid"],
        "ts": ["createdAt", "timestamp", "time", "postedAt", "date", "ts"],
        "text": ["text", "content", "body", "message", "post", "caption", "tweet", "status"],
        "coin": ["coin.address", "token.address", "coinAddress", "tokenAddress", "trade.coin", "trade.token"],
        "symbol": ["coin.symbol", "token.symbol", "symbol", "ticker"],
        "tx_hash": ["txHash", "trade.txHash", "transactionHash", "trade.hash"],
    },
    "bags": {
        "coin": ["coin.address", "token.address", "coinAddress", "tokenAddress", "mint", "address", "coin", "token"],
        "symbol": ["coin.symbol", "token.symbol", "symbol", "ticker"],
        "token_amount": ["balance", "amount", "tokenAmount", "tokens", "quantity", "qty", "holding"],
        "value_eth": ["valueEth", "ethValue", "value", "worth", "worthEth", "currentValue", "valueNative"],
    },
    "launched": {
        "coin": ["address", "coinAddress", "tokenAddress", "contractAddress", "mint", "coin.address", "token.address",
                 "id"],
        "symbol": ["symbol", "ticker", "coin.symbol", "token.symbol"],
        "name": ["name", "coin.name", "token.name"],
        "created_ts": ["createdAt", "launchedAt", "timestamp", "created", "time"],
    },
    "coin": {
        "symbol": ["symbol", "ticker", "coin.symbol", "token.symbol"],
        "holders": ["holders", "holderCount", "holdersCount", "numHolders", "uniqueHolders", "stats.holders"],
        "buys": ["buys", "buyCount", "numBuys", "txns.buys", "stats.buys", "buysCount"],
        "sells": ["sells", "sellCount", "numSells", "txns.sells", "stats.sells", "sellsCount"],
        "buy_eth": ["buyVolumeEth", "buyVolume", "volume.buys", "stats.buyVolume"],
        "sell_eth": ["sellVolumeEth", "sellVolume", "volume.sells", "stats.sellVolume"],
        "raised_eth": ["raisedEth", "ethRaised", "raised", "realEthReserves", "ethReserve", "reserveEth",
                       "collateral", "ethCollected", "curve.raised", "bondingCurve.raised", "progressEth"],
        "progress": ["progress", "bondingCurveProgress", "curveProgress", "graduationProgress", "progressPct",
                     "progressPercent", "percentComplete", "curve.progress", "bondingCurve.progress"],
    },
    "leaderboard": {
        "handle": ["handle", "username", "name", "synth.handle", "agent.handle", "user.handle", "slug"],
        "rank": ["rank", "position", "place"],
    },
    "profile": {
        "eth_balance": ["ethBalance", "balanceEth", "balance", "wallet.balance", "nativeBalance", "eth"],
        "wallet": ["wallet", "walletAddress", "address", "wallet.address"],
    },
    "holders": {
        "address": ["address", "holder", "wallet", "owner", "account"],
    },
}


def _canon(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def get_path(obj: Any, path: str) -> Any:
    """Look up a dotted path, matching keys case- and separator-insensitively."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        if part in cur:
            cur = cur[part]
            continue
        want = _canon(part)
        for key, value in cur.items():
            if _canon(str(key)) == want:
                cur = value
                break
        else:
            return None
    return cur


def pick(obj: Any, candidates: list[str], override: str | None = None) -> Any:
    if override:
        return get_path(obj, override)
    for path in candidates:
        value = get_path(obj, path)
        if value not in (None, ""):
            return value
    return None


def find_list(body: Any) -> list[Any]:
    """The list of records inside a response, wherever the app nests it."""
    if isinstance(body, list):
        if body and all(isinstance(e, dict) and "node" in e for e in body):
            return [e["node"] for e in body]
        return body
    if not isinstance(body, dict):
        return []
    for key in LIST_KEYS:
        value = get_path(body, key)
        if isinstance(value, list):
            return find_list(value)
        if isinstance(value, dict):
            inner = find_list(value)
            if inner:
                return inner
    lists = [v for v in body.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
    return lists[0] if len(lists) == 1 else []


def unwrap_object(body: Any) -> Any:
    """The record inside a single-object response ({data: {...}} etc.)."""
    for key in ("data", "result", "coin", "token", "synth", "agent", "profile", "user"):
        value = get_path(body, key) if isinstance(body, dict) else None
        if isinstance(value, dict):
            return unwrap_object(value)
    return body


def _coin_of(value: Any) -> str | None:
    if isinstance(value, dict):
        value = pick(value, ["address", "contractAddress", "tokenAddress", "id"])
    return norm_addr(value)


def _side(value: Any) -> str | None:
    if isinstance(value, bool):
        return "buy" if value else "sell"
    if not isinstance(value, str):
        return None
    v = value.lower()
    if "buy" in v or v in ("long", "bid", "in"):
        return "buy"
    if "sell" in v or v in ("short", "ask", "out", "exit", "close"):
        return "sell"
    return None


def _stable_id(prefix: str, record: Any) -> str:
    return prefix + hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()[:24]


class SynthsClient:
    def __init__(self, cfg: dict[str, Any], http: Http, save_raw: Callable[[str, str, Any], None] | None = None):
        self.cfg = cfg
        self.sc = cfg["synths"]
        self.http = http
        self.save_raw = save_raw
        self.handle = cfg["bot"]["handle"]
        self.wallet = cfg["bot"]["wallet"]
        self.eth_unit = self.sc.get("eth_unit", "auto")

    # --- plumbing ----------------------------------------------------------
    def configured(self, kind: str) -> bool:
        return bool(self.sc.get("enabled", True) and self.sc["endpoints"].get(kind))

    def url(self, kind: str, coin: str | None = None) -> str:
        template = self.sc["endpoints"][kind]
        if "{coin}" in template and not coin:
            raise SourceError(f"synths endpoint {kind!r} needs a coin address")
        url = template.format(handle=self.handle, wallet=self.wallet, coin=coin or "")
        if url.startswith("/"):
            url = self.sc["base_url"].rstrip("/") + url
        return url

    def fetch(self, kind: str, coin: str | None = None) -> Any:
        url = self.url(kind, coin)
        body = self.http.get_json(url, headers=self.sc.get("headers") or None)
        if self.save_raw and self.cfg["storage"].get("keep_raw_responses", True):
            self.save_raw(f"synths:{kind}", redact(url), body)
        return body

    def _f(self, kind: str, field: str) -> Any:
        return lambda rec: pick(rec, FIELDS[kind][field], (self.sc.get("fields", {}).get(kind) or {}).get(field))

    # --- endpoints -----------------------------------------------------------
    def trades(self) -> list[Trade]:
        body = self.fetch("trades")
        f = {name: self._f("trades", name) for name in FIELDS["trades"]}
        out = []
        for rec in find_list(body):
            if not isinstance(rec, dict):
                continue
            side = _side(f["side"](rec))
            coin = _coin_of(f["coin"](rec))
            ts = to_ts(f["ts"](rec))
            if side is None or coin is None or ts is None:
                continue
            tx = f["tx_hash"](rec)
            tx = tx.lower() if isinstance(tx, str) and TX_HASH_RE.fullmatch(tx) else None
            rid = f["id"](rec)
            reason = f["reason"](rec)
            out.append(Trade(
                id=f"synths:{rid}" if rid is not None else (f"tx:{tx}:{coin}" if tx else _stable_id("synths:", rec)),
                ts=ts, coin=coin, side=side,
                eth_amount=to_eth(f["eth_amount"](rec), self.eth_unit),
                token_amount=to_float(f["token_amount"](rec)),
                symbol=f["symbol"](rec) if isinstance(f["symbol"](rec), str) else None,
                tx_hash=tx, gas_fee_eth=to_eth(f["gas"](rec), self.eth_unit),
                reason=reason if isinstance(reason, str) else None,
                source="synths", raw=rec,
            ))
        return out

    def posts(self) -> list[Post]:
        body = self.fetch("posts")
        f = {name: self._f("posts", name) for name in FIELDS["posts"]}
        out = []
        for rec in find_list(body):
            if not isinstance(rec, dict):
                continue
            text, ts = f["text"](rec), to_ts(f["ts"](rec))
            if not isinstance(text, str) or ts is None:
                continue
            rid = f["id"](rec)
            tx = f["tx_hash"](rec)
            if not (isinstance(tx, str) and TX_HASH_RE.fullmatch(tx)):
                m = TX_HASH_RE.search(text)
                tx = m.group(0) if m else None
            out.append(Post(
                id=f"synths:{rid}" if rid is not None else _stable_id("synths:", rec), ts=ts, text=text,
                coin=_coin_of(f["coin"](rec)), symbol=f["symbol"](rec) if isinstance(f["symbol"](rec), str) else None,
                tx_hash=tx.lower() if tx else None, source="synths", raw=rec,
            ))
        return out

    def bags(self) -> list[Bag]:
        body = self.fetch("bags")
        f = {name: self._f("bags", name) for name in FIELDS["bags"]}
        out = []
        for rec in find_list(body):
            coin = _coin_of(f["coin"](rec)) if isinstance(rec, dict) else None
            if coin is None:
                continue
            out.append(Bag(
                coin=coin, token_amount=to_float(f["token_amount"](rec)),
                value_eth=to_eth(f["value_eth"](rec), self.eth_unit),
                symbol=f["symbol"](rec) if isinstance(f["symbol"](rec), str) else None, source="synths", raw=rec,
            ))
        return out

    def launched(self) -> list[CoinInfo]:
        body = self.fetch("launched")
        f = {name: self._f("launched", name) for name in FIELDS["launched"]}
        out = []
        for rec in find_list(body):
            coin = _coin_of(f["coin"](rec)) if isinstance(rec, dict) else None
            if coin is None:
                continue
            out.append(CoinInfo(
                coin=coin, symbol=f["symbol"](rec) if isinstance(f["symbol"](rec), str) else None,
                name=f["name"](rec) if isinstance(f["name"](rec), str) else None,
                created_ts=to_ts(f["created_ts"](rec)), raw=rec,
            ))
        return out

    def coin(self, coin: str, open_threshold_eth: float) -> CoinStats:
        rec = unwrap_object(self.fetch("coin", coin))
        if not isinstance(rec, dict):
            raise SourceError("synths coin endpoint did not return an object")
        f = {name: self._f("coin", name) for name in FIELDS["coin"]}
        raised = to_eth(f["raised_eth"](rec), self.eth_unit)
        progress = to_float(f["progress"](rec))
        if progress is not None:
            scale = self.sc.get("progress_scale", "auto")
            if scale == "auto" and raised is not None and open_threshold_eth:
                # Both given: whichever reading of progress agrees with the raised ETH wins.
                expected = raised / open_threshold_eth * 100
                if abs(progress * 100 - expected) < abs(progress - expected):
                    progress *= 100
            elif scale == "fraction" or (scale == "auto" and progress <= 1.0):
                progress *= 100
        if progress is None and raised is not None and open_threshold_eth:
            progress = raised / open_threshold_eth * 100
        if raised is None and progress is not None:
            raised = progress / 100 * open_threshold_eth
        stats = CoinStats(
            coin=coin, holders=_int(f["holders"](rec)), buys=_int(f["buys"](rec)), sells=_int(f["sells"](rec)),
            buy_eth=to_eth(f["buy_eth"](rec), self.eth_unit), sell_eth=to_eth(f["sell_eth"](rec), self.eth_unit),
            raised_eth=raised, progress_pct=progress,
            symbol=f["symbol"](rec) if isinstance(f["symbol"](rec), str) else None, source="synths", raw=rec,
        )
        if stats.holders is None and self.configured("holders"):
            holders = find_list(self.fetch("holders", coin))
            stats.holders = len(holders) if holders else None
        return stats

    def leaderboard_entry(self) -> tuple[int | None, Any]:
        """The bot's (rank, record) on the leaderboard; rank falls back to list position."""
        entries = find_list(self.fetch("leaderboard"))
        f = {name: self._f("leaderboard", name) for name in FIELDS["leaderboard"]}
        for i, rec in enumerate(entries):
            if not isinstance(rec, dict):
                continue
            handle = f["handle"](rec)
            matches_wallet = self.wallet and any(
                norm_addr(v) == self.wallet for v in rec.values() if isinstance(v, str) and is_address(v)
            )
            if (isinstance(handle, str) and handle.lstrip("@").lower() == self.handle.lower()) or matches_wallet:
                return _int(f["rank"](rec)) or i + 1, rec
        return None, None

    def profile(self) -> dict[str, Any]:
        rec = unwrap_object(self.fetch("profile"))
        f = {name: self._f("profile", name) for name in FIELDS["profile"]}
        balance = f["eth_balance"](rec)
        return {
            "eth_balance": to_eth(balance, self.eth_unit) if not isinstance(balance, dict) else None,
            "wallet": norm_addr(f["wallet"](rec)) if isinstance(f["wallet"](rec), str) else None,
            "raw": rec,
        }


def _int(value: Any) -> int | None:
    f = to_float(value)
    return None if f is None else int(f)
