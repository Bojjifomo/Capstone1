"""Settings, contract addresses and the .env file.

Keys live in elysium_wallet_finder/.env (git-ignored), never in the repo: the
repo is public. The app's Settings tab writes the same file.

Addresses were checked against the public RPC (chain id 99901) on 10 Nov 2026:
each one has code, the v3 NPMs' factory() and WETH9() match, and both v4
PositionManagers' poolManager() is the Uniswap v4 PoolManager.
"""
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
DATA_DIR = HERE / "data"
DB_PATH = Path(os.environ.get("ELYWF_DB") or DATA_DIR / "wallets.db")

CHAIN_ID = 99901  # Elysium (Kinetiq)
GAS = "HYPE"  # native gas, bridged 1:1 from HyperEVM

# Block 1 is 19 Oct 2026 12:00 UTC. Public mainnet opened 9 Nov 2026; Uniswap's
# own deployment landed around block 256_000. "ALL" history can't start earlier
# than block 1.
CHAIN_LAUNCH_TS = 1792411200

# --- Uniswap v4 (official deployments page, developers.uniswap.org) ---
# A second PositionManager with the same name() and the same PoolManager showed
# up on chain (nextTokenId in the thousands). Both are indexed; more are picked
# up automatically when nextTokenId() and poolManager() match.
POOL_MANAGER = "0xe2e105d3f7209a9da11f83ccf3e7398a753823f1"
V4_PMS = (
    "0x1d1c51b9d07fd27d36412a2b66cc23d19eb3e518",  # Uniswap docs: PositionManager
    "0x73a3098520404f4ea174788bab5f0e04810e6460",  # second "Uniswap v4 Positions NFT"
)
POSITION_MANAGER = V4_PMS[0]  # the one poolKeys() is tried on first
STATE_VIEW = "0x27ed73ae0652fdcf71d6793412a7525f32be9dfe"
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"

# --- v3 NFT position managers ---
# (npm, factory, init code hash, label)
# ElysiumSwap: docs.elysiumswap.finance/contracts.html (deployed 31 Oct 2026).
# Official Uniswap v3: factory()/WETH9() read back from the NPM on 10 Nov 2026;
# the deployer is Uniswap's deployment wallet 0x2179…27b6 (same as the v4 deploy).
V3_VENUES = (
    {"npm": "0xc6dc519451f58dfd27bb2920eefae57597bd91f5",
     "factory": "0x7266c0ecff2a687b4ad0ead7b24840929540b697",
     "init_hash": "0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54",
     "label": "elysiumswap-v3"},
    {"npm": "0xf025e0fe9e331a0ef05c2ad3c4e9c64b625cda6f",
     "factory": "0x1707327f626496a7b5b3872e7e4d2879df7d7a9f",
     "init_hash": "0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54",
     "label": "uniswap-v3"},
)
V3_NPM = {v["npm"]: v for v in V3_VENUES}

# ElysiumPad locker. Launch positions (v3, 1% fee) are minted here and the swap
# fee is paid to that position, then split to the creator and ElysiumPad — not
# to other LPs. docs.elysiumswap.finance/pad/fees.html
LOCKER = "0xa83212cac640cb27005b956531c6cb251f14465c"
LAUNCHER = "0x3318b09e6e4d222513d8fbcf76205b3c5566d76c"

ZERO = "0x" + "0" * 40  # native HYPE as a V4 currency
WHYPE = "0xba876237b0f021a6c6d852d6c26e107997cc5e81"
USDC = "0xb47c4c8f73f009cc0c0d3f92b388d13e293f0b65"  # 6 decimals
# Names the scorer and the copied pool tests use for "the gas token" and "the stable".
WETH = WHYPE
USDG = USDC

# Tokens worth $1. Only addresses, never symbols.
STABLES = {USDC}
# Native gas and its wrapper. The scorer still calls this side "eth" internally
# (the gas-token side of a pair); the UI says HYPE.
ETH_LIKE = {ZERO, WHYPE}

# HYPE/USDC v3 pools, deepest first (liquidity read 10 Nov 2026). token0 is
# USDC, token1 is WHYPE, so the human price of token0 in token1 is WHYPE per
# USDC and the HYPE price in USD is the reciprocal. If the first has no price
# at an old block the next one is tried.
# kind v3 -> pool.slot0(); a 32-byte id would be StateView.getSlot0.
HYPE_USD_SOURCES = (
    {"pool": "0xec2e846b66eee17bc856129aa9e9b1baee4d521d", "kind": "v3", "dec0": 6, "dec1": 18},
    {"pool": "0x5bbf2d96a995fa3e97372ca4f611bad86c670fd6", "kind": "v3", "dec0": 6, "dec1": 18},
)
# Pool ids the scorer prepends (and tests count). Same order as HYPE_USD_SOURCES.
ETH_USD_POOLS = [s["pool"] for s in HYPE_USD_SOURCES]

# No seeded reference wallets: the chain is days old and the Robinhood examples
# don't exist here. Any wallet can be marked a reference in its dossier.
REFERENCE_WALLETS = {}

EXPLORER = "https://elyscan.live"
PUBLIC_RPC = "https://rpc.elysium.kinetiq.xyz"
# A bare Alchemy API key in an RPC field becomes this link.
ALCHEMY_RPC = "https://elysium-mainnet.g.alchemy.com/v2/{key}"
GECKO_NETWORK = "elysium"  # GeckoTerminal lists it; the pool finder degrades if a call 404s

# How long after a launch pool's locked position a new LP still counts as early.
EARLY_MINUTES_DEFAULT = "30"

DEFAULTS = {
    "RPC_URL": PUBLIC_RPC,  # public endpoint does eth_getLogs and archive eth_call
    "CALL_RPC_URL": "",
    # Web showcase on Vercel.
    "VERCEL_PUBLISH_URL": "",
    "VERCEL_BLOB_TOKEN": "",
    "PUBLISH_MINUTES": "30",
    # 0 = from block 1. The chain is only a few weeks old, so that is the
    # whole history and it is what a new database loads.
    "BACKFILL_DAYS": "0",
    "METRIC_DAYS": "30",
    "ACTIVE_DAYS": "7",
    "SELL_SLIPPAGE_PCT": "1.0",
    "SCORE_POSITIONS_PER_RUN": "4000",
    "RPC_CALLS_PER_DAY": "0",
    "CALL_SPLIT": "share",
    # A young chain: one closed position is enough to show up. Raise it in
    # Settings once the boards get noisy.
    "MIN_CLOSED": "1",
    "MULTIDAY_HOURS": "2",
    "EARLY_MINUTES": EARLY_MINUTES_DEFAULT,
    "RPC_WORKERS": "4",  # the public RPC is shared; don't stampede it
    "LOG_CHUNK": "8000",
    "SYNC_MINUTES": "5",
    "PORT": "8502",  # 8501 is the Robinhood finder's default
}


def _read_env(path):
    out = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def load_env():
    """Settings from the environment, then .env, then DEFAULTS."""
    vals = dict(DEFAULTS)
    vals.update({k: v for k, v in _read_env(ENV_FILE).items() if v})
    for k in vals:
        if os.environ.get(k):
            vals[k] = os.environ[k]
    vals["RPC_SOURCE"] = "settings" if _read_env(ENV_FILE).get("RPC_URL") or os.environ.get("RPC_URL") else "public"
    for k in ("RPC_URL", "CALL_RPC_URL"):
        vals[k] = rpc_link(vals[k])
    vals["VERCEL_BLOB_TOKEN"] = blob_token(vals["VERCEL_BLOB_TOKEN"])
    vals["VERCEL_PUBLISH_URL"] = publish_link(vals["VERCEL_PUBLISH_URL"])
    return vals


BLOB_TOKEN_RE = re.compile(r"vercel_blob_rw_([A-Za-z0-9]+)_([^\s\"'`]+)")


def blob_token(v):
    """The Blob read-write token out of whatever was pasted."""
    m = BLOB_TOKEN_RE.search(v or "")
    return m.group(0) if m else (v or "").strip().strip("\"'")


def blob_store_id(token):
    m = BLOB_TOKEN_RE.fullmatch(token or "")
    return m.group(1) if m else None


PUBLISH_LINK_RE = re.compile(r"https?://[^\s\"'`<>]+")


def publish_link(v):
    m = PUBLISH_LINK_RE.search(v or "")
    return m.group(0).rstrip(".,;)") if m else ""


def split_publish_link(link):
    """'https://site.vercel.app/api/publish?key=K' -> ('https://site.vercel.app/api/publish', 'K')."""
    from urllib.parse import parse_qs, urlsplit, urlunsplit
    u = urlsplit(link or "")
    key = (parse_qs(u.query).get("key") or [""])[0]
    path = u.path if u.path.rstrip("/") else "/api/publish"
    return urlunsplit((u.scheme, u.netloc, path, "", "")), key


def rpc_link(v):
    """An RPC link as typed: a bare Alchemy API key becomes its Elysium link."""
    v = (v or "").strip()
    if v and "://" not in v and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", v):
        return ALCHEMY_RPC.format(key=v)
    return v


def save_env(updates):
    """Write keys to .env, keeping lines and comments that aren't changed."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    done = set()
    out = []
    for line in lines:
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in updates:
            out.append(f"{k}={updates[k]}")
            done.add(k)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    ENV_FILE.write_text("\n".join(out) + "\n")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def mask(url):
    """Show an RPC URL without its key."""
    if not url:
        return ""
    parts = url.split("/")
    for i, p in enumerate(parts):
        if len(p) >= 20 and i > 2:
            parts[i] = p[:7] + "…" + p[-4:]
    return "/".join(parts)


def pos_key(venue, token_id):
    return f"{venue}:{token_id}"
