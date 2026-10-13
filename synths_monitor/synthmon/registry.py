"""Find a synth's on-chain account from the wallet that created it.

Creating a synth on synths.lol is a call to its registry, which emits

    topic0 = SYNTH_CREATED_TOPIC
    topic1 = synth id (bytes32)
    topic2 = the synth's account contract (where its ETH sits and it trades from)
    topic3 = the creator / owner wallet
    data   = bytes32, then strings: handle, name, model, avatar URL, ...

(Read off a live registration on Robinhood Chain mainnet.) People
usually know their own wallet, not the synth's account, so `find-synth` and
`setup` look the account up from the owner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sources.chain import RpcChain
from .util import norm_addr, to_ts

REGISTRY = "0xb81c51ba01c5b723ad162fb63a1a0905977f64ff"
SYNTH_CREATED_TOPIC = "0xb8450bc7752fd2fdcba8392c4d77ce77e8bf69c5a43db5fdf52d97ac6fbd6c8f"


@dataclass
class SynthRecord:
    synth_id: str
    account: str
    owner: str
    tx_hash: str
    block: int
    ts: int | None = None
    strings: list[str] = field(default_factory=list)

    @property
    def handle(self) -> str | None:
        return self.strings[0] if self.strings else None


def abi_strings(data: str) -> list[str]:
    """The dynamic strings of an ABI-encoded (bytes32, string, string, ...) payload."""
    raw = bytes.fromhex(data[2:] if data.startswith("0x") else data)
    words = [int.from_bytes(raw[i:i + 32], "big") for i in range(0, len(raw) - len(raw) % 32, 32)]
    out = []
    # Head: one static bytes32, then one offset per string, until the first offset is reached.
    first_offset = None
    for i, w in enumerate(words[1:], start=1):
        if first_offset is not None and i * 32 >= first_offset:
            break
        if w % 32 or w >= len(raw):
            break
        first_offset = w if first_offset is None else first_offset
        length = int.from_bytes(raw[w:w + 32], "big")
        out.append(raw[w + 32:w + 32 + length].decode("utf-8", errors="replace"))
    return out


def find_synths(rpc: RpcChain, owner: str, registry: str = REGISTRY) -> list[SynthRecord]:
    owner = owner.lower()
    sent, _ = rpc._asset_transfers(category=["external"], fromAddress=owner, fromBlock="0x0", toBlock="latest")
    calls = {}
    for t in sent:
        if norm_addr(t.get("to")) == registry:
            calls[t["hash"].lower()] = to_ts((t.get("metadata") or {}).get("blockTimestamp"))
    records = []
    for (tx_hash, ts), receipt in zip(calls.items(), rpc.batch([("eth_getTransactionReceipt", [h]) for h in calls]),
                                      strict=True):
        for log in (receipt or {}).get("logs", []):
            topics = log.get("topics") or []
            if len(topics) < 4 or topics[0].lower() != SYNTH_CREATED_TOPIC:
                continue
            if "0x" + topics[3][-40:].lower() != owner:
                continue
            records.append(SynthRecord(
                synth_id=topics[1], account="0x" + topics[2][-40:].lower(), owner=owner, tx_hash=tx_hash,
                block=int(receipt["blockNumber"], 16), ts=ts, strings=abi_strings(log.get("data") or "0x"),
            ))
    return records
