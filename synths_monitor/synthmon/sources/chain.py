"""On-chain fallback: the synth's wallet read straight from Robinhood Chain.

Two backends share one interface and are tried in order:

* ExplorerChain - the chain's block explorer API (Etherscan-compatible, which
  Blockscout serves at <explorer>/api). It lists internal ETH transfers, so
  sell proceeds are exact.
* RpcChain - plain JSON-RPC plus Alchemy's alchemy_getAssetTransfers. Internal
  transfers are not indexed on this network, so a trade's ETH leg is taken
  from the wallet's balance change across the block, plus gas when the wallet
  sent the transaction.

Both turn the wallet's activity into per-transaction token and ETH deltas;
`trades_from_activity` turns those into buys and sells.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from ..http import Http, RateLimited, SourceError
from ..models import TokenTransfer, Trade
from ..util import WEI, ZERO_ADDRESS, norm_addr, to_ts

RATE_LIMIT_PAUSES = (1.0, 2.0, 4.0, 8.0)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ETH_EPSILON = 1e-9  # below this an ETH leg counts as zero


@dataclass
class TxActivity:
    tx_hash: str
    block: int
    ts: int | None
    token_deltas: dict[str, float] = field(default_factory=dict)  # token -> signed amount for the wallet
    symbols: dict[str, str] = field(default_factory=dict)
    eth_delta: float | None = None  # signed ETH for the wallet, gas excluded
    gas_fee_eth: float | None = None
    approximate: bool = False


def trades_from_activity(activity: list[TxActivity], weth: list[str], source: str) -> list[Trade]:
    """Classify each transaction's deltas as buys, sells or plain transfers."""
    trades = []
    for a in activity:
        tokens = {t: d for t, d in a.token_deltas.items() if t not in weth and abs(d) > 0}
        if not tokens:
            continue
        eth = a.eth_delta or 0.0
        # With several tokens in one tx (a token-for-token swap) the ETH leg cannot be split; price none.
        single = len(tokens) == 1
        for token, delta in tokens.items():
            if delta > 0:
                side = "buy" if single and eth < -ETH_EPSILON else "transfer_in"
            else:
                side = "sell" if single and eth > ETH_EPSILON else "transfer_out"
            eth_amount = abs(eth) if side in ("buy", "sell") else None
            trades.append(Trade(
                id=f"tx:{a.tx_hash}:{token}", ts=a.ts or 0, coin=token, side=side, eth_amount=eth_amount,
                token_amount=abs(delta), symbol=a.symbols.get(token), tx_hash=a.tx_hash, block=a.block,
                gas_fee_eth=a.gas_fee_eth if single else None, source=source,
                raw={"eth_delta": a.eth_delta, "approximate": a.approximate} if a.approximate else None,
            ))
    return trades


def _rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, RateLimited) or any(w in text for w in ("429", "rate limit", "exceeded", "too many"))


class RpcChain:
    name = "rpc"

    def __init__(self, url: str, http: Http, decimals_default: int = 18, sleep: Callable[[float], None] = time.sleep):
        self.url = url
        self.http = http
        self.decimals_default = decimals_default
        self.sleep = sleep
        self._decimals: dict[str, int] = {}
        self._id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        body = self.http.post_json(self.url, {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        if not isinstance(body, dict):
            raise SourceError(f"{method}: malformed JSON-RPC response")
        if body.get("error"):
            err = body["error"]
            raise SourceError(f"{method}: {err.get('message', err) if isinstance(err, dict) else err}")
        return body.get("result")

    def batch(self, calls: list[tuple[str, list[Any]]], chunk: int = 25) -> list[Any]:
        """Several calls per HTTP request (JSON-RPC batch); results in call order.

        A chunk that hits the provider's rate limit is retried after a pause.
        """
        results: list[Any] = []
        for i in range(0, len(calls), chunk):
            part = calls[i:i + chunk]
            for attempt in range(len(RATE_LIMIT_PAUSES) + 1):
                try:
                    results.extend(self._batch_once(part))
                    break
                except SourceError as exc:
                    if attempt == len(RATE_LIMIT_PAUSES) or not _rate_limited(exc):
                        raise
                    self.sleep(RATE_LIMIT_PAUSES[attempt])
        return results

    def _batch_once(self, part: list[tuple[str, list[Any]]]) -> list[Any]:
        payload = [{"jsonrpc": "2.0", "id": n, "method": m, "params": p} for n, (m, p) in enumerate(part)]
        body = self.http.post_json(self.url, payload)
        if not isinstance(body, list):
            if isinstance(body, dict) and body.get("error"):
                raise SourceError(f"batch: {body['error']}")
            return [self.call(m, p) for m, p in part]  # endpoint without batch support
        by_id = {item.get("id"): item for item in body if isinstance(item, dict)}
        out = []
        for n, (method, _) in enumerate(part):
            item = by_id.get(n)
            if item is None or item.get("error"):
                err = (item or {}).get("error", "missing from batch response")
                raise SourceError(f"{method}: {err.get('message', err) if isinstance(err, dict) else err}")
            out.append(item.get("result"))
        return out

    def latest_block(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def eth_balance(self, address: str, block: int | str = "latest") -> float:
        tag = hex(block) if isinstance(block, int) else block
        return int(self.call("eth_getBalance", [address, tag]), 16) / WEI

    def erc20_balance(self, token: str, holder: str) -> float:
        data = "0x70a08231" + holder[2:].rjust(64, "0")
        raw = self.call("eth_call", [{"to": token, "data": data}, "latest"])
        return int(raw or "0x0", 16) / 10 ** self.decimals(token)

    def decimals(self, token: str) -> int:
        if token not in self._decimals:
            try:
                meta = self.call("alchemy_getTokenMetadata", [token]) or {}
                self._decimals[token] = int(meta.get("decimals") or self.decimals_default)
            except SourceError:
                raw = self.call("eth_call", [{"to": token, "data": "0x313ce567"}, "latest"])
                self._decimals[token] = int(raw, 16) if raw and raw != "0x" else self.decimals_default
        return self._decimals[token]

    def is_contract(self, address: str) -> bool:
        code = self.call("eth_getCode", [address, "latest"])
        return bool(code and code != "0x")

    def is_contract_many(self, addresses: list[str]) -> dict[str, bool]:
        codes = self.batch([("eth_getCode", [a, "latest"]) for a in addresses])
        return {a: bool(code and code != "0x") for a, code in zip(addresses, codes, strict=True)}

    def token_balances(self, address: str) -> dict[str, float]:
        res = self.call("alchemy_getTokenBalances", [address, "erc20"]) or {}
        out = {}
        for entry in res.get("tokenBalances", []):
            raw = int(entry.get("tokenBalance") or "0x0", 16)
            token = norm_addr(entry.get("contractAddress"))
            if raw and token:
                out[token] = raw / 10 ** self.decimals(token)
        return out

    def _asset_transfers(self, max_pages: int | None = None, **flt: Any) -> tuple[list[dict], bool]:
        """Transfers matching the filter, oldest first. The flag is False when max_pages cut the list short."""
        params = {"withMetadata": True, "excludeZeroValue": False, "maxCount": hex(1000), "order": "asc", **flt}
        out: list[dict] = []
        pages = 0
        while True:
            res = self.call("alchemy_getAssetTransfers", [params]) or {}
            out.extend(res.get("transfers", []))
            pages += 1
            if not res.get("pageKey"):
                return out, True
            if max_pages is not None and pages >= max_pages:
                return out, False
            params["pageKey"] = res["pageKey"]

    @staticmethod
    def _amount(t: dict) -> float:
        rc = t.get("rawContract") or {}
        if rc.get("value") and rc.get("decimal"):
            return int(rc["value"], 16) / 10 ** int(rc["decimal"], 16)
        return float(t.get("value") or 0.0)

    def wallet_activity(self, wallet: str, from_block: int, to_block: int, weth: list[str]) -> list[TxActivity]:
        rng = {"fromBlock": hex(from_block), "toBlock": hex(to_block)}
        by_tx: dict[str, TxActivity] = {}
        ext_eth: dict[str, float] = defaultdict(float)
        blocks_txs: dict[int, set[str]] = defaultdict(set)
        seen: set[str] = set()  # a self-transfer is returned for both directions
        for direction in ("fromAddress", "toAddress"):
            transfers, _ = self._asset_transfers(category=["erc20", "external"], **{direction: wallet}, **rng)
            for t in transfers:
                tx = t["hash"].lower()
                block = int(t["blockNum"], 16)
                act = by_tx.get(tx) or by_tx.setdefault(
                    tx, TxActivity(tx_hash=tx, block=block, ts=to_ts((t.get("metadata") or {}).get("blockTimestamp")))
                )
                blocks_txs[block].add(tx)
                key = t.get("uniqueId") or f"{tx}:{t.get('from')}:{t.get('to')}:{t.get('value')}"
                if key in seen:
                    continue
                seen.add(key)
                sign = -1.0 if norm_addr(t.get("from")) == wallet else 1.0
                if norm_addr(t.get("from")) == wallet and norm_addr(t.get("to")) == wallet:
                    sign = 0.0
                if t["category"] == "external":
                    ext_eth[tx] += sign * float(t.get("value") or 0.0)
                    continue
                token = norm_addr((t.get("rawContract") or {}).get("address"))
                if not token:
                    continue
                act.token_deltas[token] = act.token_deltas.get(token, 0.0) + sign * self._amount(t)
                if t.get("asset"):
                    act.symbols[token] = t["asset"]

        weth_delta = {tx: sum(d for tok, d in a.token_deltas.items() if tok in weth) for tx, a in by_tx.items()}
        trading = [a for a in by_tx.values() if any(tok not in weth and d for tok, d in a.token_deltas.items())]
        for act in by_tx.values():
            act.eth_delta = ext_eth[act.tx_hash] + weth_delta[act.tx_hash]

        receipts = self.batch([("eth_getTransactionReceipt", [a.tx_hash]) for a in trading])
        for act, receipt in zip(trading, receipts, strict=True):
            receipt = receipt or {}
            if norm_addr(receipt.get("from")) == wallet:
                gas_price = int(receipt.get("effectiveGasPrice") or "0x0", 16)
                act.gas_fee_eth = int(receipt.get("gasUsed") or "0x0", 16) * gas_price / WEI
        # The balance change across a block is the tx's ETH leg (internal transfers included) plus
        # the gas it paid. That only isolates one tx when it is the wallet's only tx in the block.
        alone = [a for a in trading if len(blocks_txs[a.block]) == 1]
        balances = self.batch([("eth_getBalance", [wallet, hex(b)]) for a in alone for b in (a.block, a.block - 1)])
        for i, act in enumerate(alone):
            diff = (int(balances[2 * i], 16) - int(balances[2 * i + 1], 16)) / WEI
            act.eth_delta = diff + (act.gas_fee_eth or 0.0) + weth_delta[act.tx_hash]
        for act in trading:
            if len(blocks_txs[act.block]) > 1:
                act.approximate = True  # only the visible (external + WETH) legs are known
        return sorted(by_tx.values(), key=lambda a: (a.block, a.tx_hash))

    def token_transfers(self, token: str, from_block: int, to_block: int,
                        max_pages: int = 20) -> tuple[list[TokenTransfer], int]:
        """Transfers of `token`, and the block to resume from (re-reading a partial block is harmless)."""
        transfers, complete = self._asset_transfers(
            max_pages=max_pages, category=["erc20"], contractAddresses=[token], fromBlock=hex(from_block),
            toBlock=hex(to_block),
        )
        out = [
            TokenTransfer(
                uid=t.get("uniqueId") or f"{t['hash']}:{t.get('from')}:{t.get('to')}",
                coin=token, block=int(t["blockNum"], 16), ts=to_ts((t.get("metadata") or {}).get("blockTimestamp")),
                tx_hash=t["hash"].lower(), from_addr=norm_addr(t.get("from")) or ZERO_ADDRESS,
                to_addr=norm_addr(t.get("to")) or ZERO_ADDRESS, amount=self._amount(t),
            )
            for t in transfers
        ]
        return out, (to_block + 1 if complete or not out else out[-1].block)


class ExplorerChain:
    """Etherscan-compatible block explorer API (module=account&action=...)."""

    name = "explorer"
    page_size = 1000

    def __init__(self, api_url: str, http: Http, api_key: str = "", decimals_default: int = 18):
        self.api_url = api_url.rstrip("/")
        self.http = http
        self.api_key = api_key
        self.decimals_default = decimals_default

    def get(self, **params: Any) -> Any:
        if self.api_key:
            params["apikey"] = self.api_key
        body = self.http.get_json(self.api_url, params=params)
        if not isinstance(body, dict):
            raise SourceError("explorer: malformed response")
        if "jsonrpc" in body:  # module=proxy answers in JSON-RPC form
            if body.get("error"):
                raise SourceError(f"explorer {params.get('action')}: {body['error']}")
            return body.get("result")
        result = body.get("result")
        if str(body.get("status")) == "1":
            return result
        message = str(body.get("message", ""))
        if "no " in message.lower() and "found" in message.lower():
            return []
        raise SourceError(f"explorer {params.get('action')}: {message} {str(result)[:120]}")

    def _paged(self, **params: Any) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            rows = self.get(page=page, offset=self.page_size, sort="asc", **params) or []
            out.extend(rows)
            if len(rows) < self.page_size:
                return out
            page += 1

    def latest_block(self) -> int:
        return int(self.get(module="proxy", action="eth_blockNumber"), 16)

    def eth_balance(self, address: str, block: int | str = "latest") -> float:
        if block != "latest":
            raise SourceError("explorer: historical balances not supported")
        return int(self.get(module="account", action="balance", address=address)) / WEI

    def erc20_balance(self, token: str, holder: str) -> float:
        raw = int(self.get(module="account", action="tokenbalance", contractaddress=token, address=holder))
        return raw / 10 ** self.decimals_default

    def is_contract(self, address: str) -> bool:
        code = self.get(module="proxy", action="eth_getCode", address=address, tag="latest")
        return bool(code and code != "0x")

    def is_contract_many(self, addresses: list[str]) -> dict[str, bool]:
        return {a: self.is_contract(a) for a in addresses}

    def token_balances(self, address: str) -> dict[str, float]:
        out = {}
        for row in self.get(module="account", action="tokenlist", address=address) or []:
            token = norm_addr(row.get("contractAddress"))
            if token and row.get("type", "ERC-20") in ("ERC-20", "ERC20") and int(row.get("balance") or 0):
                out[token] = int(row["balance"]) / 10 ** int(row.get("decimals") or self.decimals_default)
        return out

    def wallet_activity(self, wallet: str, from_block: int, to_block: int, weth: list[str]) -> list[TxActivity]:
        rng = {"address": wallet, "startblock": from_block, "endblock": to_block}
        by_tx: dict[str, TxActivity] = {}

        def act_for(row: dict) -> TxActivity:
            tx = row["hash"].lower() if row.get("hash") else row["transactionHash"].lower()
            return by_tx.setdefault(tx, TxActivity(
                tx_hash=tx, block=int(row["blockNumber"]), ts=to_ts(row.get("timeStamp")), eth_delta=0.0
            ))

        def sign(row: dict) -> float:
            frm, to = norm_addr(row.get("from")), norm_addr(row.get("to"))
            if frm == wallet and to == wallet:
                return 0.0
            return -1.0 if frm == wallet else 1.0

        for row in self._paged(module="account", action="txlist", **rng):
            act = act_for(row)
            if str(row.get("isError", "0")) == "1":
                act.eth_delta = act.eth_delta or 0.0
            else:
                act.eth_delta += sign(row) * int(row.get("value") or 0) / WEI
            if norm_addr(row.get("from")) == wallet:
                act.gas_fee_eth = int(row.get("gasUsed") or 0) * int(row.get("gasPrice") or 0) / WEI
        for row in self._paged(module="account", action="txlistinternal", **rng):
            if str(row.get("isError", "0")) != "1":
                act_for(row).eth_delta += sign(row) * int(row.get("value") or 0) / WEI
        for row in self._paged(module="account", action="tokentx", **rng):
            act = act_for(row)
            token = norm_addr(row.get("contractAddress"))
            if not token:
                continue
            amount = int(row.get("value") or 0) / 10 ** int(row.get("tokenDecimal") or self.decimals_default)
            act.token_deltas[token] = act.token_deltas.get(token, 0.0) + sign(row) * amount
            if row.get("tokenSymbol"):
                act.symbols[token] = row["tokenSymbol"]
        for act in by_tx.values():
            act.eth_delta = (act.eth_delta or 0.0) + sum(d for tok, d in act.token_deltas.items() if tok in weth)
        return sorted(by_tx.values(), key=lambda a: (a.block, a.tx_hash))

    def token_transfers(self, token: str, from_block: int, to_block: int,
                        max_pages: int = 20) -> tuple[list[TokenTransfer], int]:
        out: dict[str, TokenTransfer] = {}
        start = from_block
        for _ in range(max_pages):
            logs = self.get(module="logs", action="getLogs", address=token, fromBlock=start, toBlock=to_block,
                            topic0=TRANSFER_TOPIC) or []
            for log in logs:
                topics = log.get("topics") or []
                if len(topics) < 3:
                    continue
                block = int(str(log["blockNumber"]), 0)
                uid = f"{log['transactionHash'].lower()}:log:{int(str(log.get('logIndex') or '0x0'), 0)}"
                out[uid] = TokenTransfer(
                    uid=uid, coin=token, block=block, ts=to_ts(log.get("timeStamp")),
                    tx_hash=log["transactionHash"].lower(), from_addr="0x" + topics[1][-40:].lower(),
                    to_addr="0x" + topics[2][-40:].lower(),
                    amount=int(log.get("data") or "0x0", 16) / 10 ** self.decimals_default,
                )
            if len(logs) < self.page_size:
                return sorted(out.values(), key=lambda t: (t.block, t.uid)), to_block + 1
            next_start = max(int(str(log["blockNumber"]), 0) for log in logs)
            if next_start == start:
                raise SourceError("explorer getLogs: more than one page of logs in a single block")
            start = next_start
        return sorted(out.values(), key=lambda t: (t.block, t.uid)), start


class FallbackChain:
    """Calls each backend in order until one answers; remembers which one did."""

    def __init__(self, backends: list[Any], on_error: Callable[[str], None] | None = None):
        self.backends = backends
        self.on_error = on_error or (lambda msg: None)
        self.last_source = ""

    def __bool__(self) -> bool:
        return bool(self.backends)

    def call(self, method: str, *args: Any) -> Any:
        errors = []
        for backend in self.backends:
            try:
                result = getattr(backend, method)(*args)
            except SourceError as exc:
                errors.append(f"{backend.name}: {exc}")
                continue
            self.last_source = backend.name
            return result
        msg = f"chain.{method} failed on every backend: " + "; ".join(errors) if errors else "no chain backend configured"
        self.on_error(msg)
        raise SourceError(msg)
