"""Token metadata (decimals + symbol) straight from chain, cached in memory."""

from solders.pubkey import Pubkey

from .dlmm import account_bytes

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
METADATA_PROGRAM = Pubkey.from_string("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s")

KNOWN = {
    SOL_MINT: ("SOL", 9),
    USDC_MINT: ("USDC", 6),
    USDT_MINT: ("USDT", 6),
}
STABLES = {USDC_MINT, USDT_MINT}
JUPUSD_MINT = "JuprjznTrTSp2UFa3ZBUFgwdAmtZCq4MQCwysN55USD"
SOFT_STABLES = {JUPUSD_MINT}  # priced at $1 only when Jupiter gives no price


class TokenInfo:
    __slots__ = ("mint", "symbol", "name", "decimals")

    def __init__(self, mint, symbol, name, decimals):
        self.mint, self.symbol, self.name, self.decimals = mint, symbol, name, decimals

    def ui(self, raw):
        return raw / 10 ** self.decimals


def _metadata_pda(mint):
    pda, _ = Pubkey.find_program_address(
        [b"metadata", bytes(METADATA_PROGRAM), bytes(Pubkey.from_string(mint))], METADATA_PROGRAM)
    return str(pda)


def _borsh_str(b, off):
    n = int.from_bytes(b[off:off + 4], "little")
    s = b[off + 4:off + 4 + n].decode("utf-8", "replace").rstrip("\x00").strip()
    return s, off + 4 + n


def _parse_metaplex(data):
    # key(1) update_authority(32) mint(32) name symbol uri ...
    try:
        name, off = _borsh_str(data, 65)
        symbol, _ = _borsh_str(data, off)
        return name, symbol
    except Exception:
        return None, None


class TokenRegistry:
    def __init__(self, rpc):
        self.rpc = rpc
        self.cache = {}

    def get(self, mint):
        if mint not in self.cache:
            self.load([mint])
        return self.cache[mint]

    def load(self, mints):
        todo = [m for m in dict.fromkeys(mints) if m not in self.cache]
        if not todo:
            return
        mint_accs = self.rpc.get_multiple_accounts(todo, encoding="jsonParsed")
        meta_accs = self.rpc.get_multiple_accounts([_metadata_pda(m) for m in todo])
        for mint, macc, meta in zip(todo, mint_accs, meta_accs):
            decimals, symbol, name = 0, None, None
            if macc and isinstance(macc.get("data"), dict):
                info = macc["data"].get("parsed", {}).get("info", {})
                decimals = info.get("decimals", 0)
                for ext in info.get("extensions") or []:
                    if ext.get("extension") == "tokenMetadata":
                        st = ext.get("state", {})
                        symbol, name = st.get("symbol") or None, st.get("name") or None
            if not symbol and meta:
                name, symbol = _parse_metaplex(account_bytes(meta))
            if mint in KNOWN:
                symbol, decimals = KNOWN[mint]
            symbol = symbol or (mint[:4] + "…")
            self.cache[mint] = TokenInfo(mint, symbol, name or symbol, decimals)
