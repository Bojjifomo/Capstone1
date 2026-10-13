from synthmon.registry import REGISTRY, SYNTH_CREATED_TOPIC, abi_strings, find_synths
from synthmon.sources.chain import RpcChain

OWNER = "0xa1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
ACCOUNT = "0xb2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2"
TX = "0x" + "9" * 64


def encode(strings):
    """ABI-encode (bytes32, string...) the way the registry event does."""
    head = [b"\x11" * 32]
    tail = b""
    offset = 32 * (1 + len(strings))
    for s in strings:
        raw = s.encode()
        head.append(offset.to_bytes(32, "big"))
        chunk = len(raw).to_bytes(32, "big") + raw + b"\x00" * (-len(raw) % 32)
        tail += chunk
        offset += len(chunk)
    return "0x" + (b"".join(head) + tail).hex()


FIELDS = ["bojjibot", "bojjibot", "deepseek/deepseek-v4-pro", "https://example/ipfs/x", "A disciplined trader."]


def test_abi_strings_roundtrip():
    assert abi_strings(encode(FIELDS)) == FIELDS


class FakeHttp:
    def post_json(self, url, payload):
        if isinstance(payload, list):
            return [{"id": p["id"], **self.post_json(url, p)} for p in payload]
        if payload["method"] == "alchemy_getAssetTransfers":
            return {"result": {"transfers": [
                {"hash": TX, "to": REGISTRY, "metadata": {"blockTimestamp": "2026-10-13T03:06:00Z"}},
                {"hash": "0x" + "8" * 64, "to": "0x" + "1" * 40, "metadata": {}},
            ]}}
        if payload["method"] == "eth_getTransactionReceipt":
            return {"result": {"blockNumber": hex(87185860), "logs": [{
                "address": REGISTRY,
                "topics": [SYNTH_CREATED_TOPIC, "0x" + "ab" * 32, "0x" + "0" * 24 + ACCOUNT[2:], "0x" + "0" * 24 + OWNER[2:]],
                "data": encode(FIELDS),
            }]}}
        raise AssertionError(payload["method"])


def test_find_synths_from_owner():
    (rec,) = find_synths(RpcChain("https://rpc.example/v2/k", FakeHttp()), OWNER.upper().replace("0X", "0x"))
    assert rec.account == ACCOUNT and rec.owner == OWNER and rec.handle == "bojjibot"
    assert rec.block == 87185860 and rec.tx_hash == TX and rec.strings[2] == "deepseek/deepseek-v4-pro"
