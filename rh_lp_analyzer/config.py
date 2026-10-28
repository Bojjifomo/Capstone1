"""Settings, contract addresses and the .env file.

Keys live in rh_lp_analyzer/.env (git-ignored) or Streamlit secrets, never in
the repo: the repo is public.
"""
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
DATA_DIR = HERE / "data"
DB_PATH = Path(os.environ.get("RHLA_DB") or DATA_DIR / "cache.db")

CHAIN_ID = 4663  # Robinhood Chain

# Uniswap V4 on Robinhood Chain
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"

# Public mainnet launch (1 Jul 2026): no LP history before it.
CHAIN_LAUNCH_TS = 1782864000

ZERO = "0x" + "0" * 40  # native ETH as a V4 currency
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"

# Tokens worth $1. Only addresses, never symbols: anyone can deploy a token
# called "USDC".
STABLES = {USDG}
ETH_LIKE = {ZERO, WETH}

# ETH/USDG pools used for the ETH price, deepest first.
ETH_USD_POOLS = [
    "0x387bf619da4d3fb62bb276482693dba1b9b3520f573cabdfe033384a24125982",  # ETH/USDG 0.05%
    "0x24107d152f14a76d292123265ae3f3c71f863fc2f4ef7ba49d64e78d28ea379e",  # ETH/USDG 0.01%
    "0xfcfae8fa0bd6da961bcf5d990f27690932deac4f093e99bf3e871691c6586593",  # WETH/USDG 0.05%
]

EXPLORER = "https://robinhoodchain.blockscout.com"
ALCHEMY_RPC = os.environ.get("RHLA_ALCHEMY_RPC") or "https://robinhood-mainnet.g.alchemy.com/v2/{key}"

DEFAULTS = {
    # RPC_URL does eth_getLogs (and calls); CALL_RPC_URL, when set, shares the
    # work as a pool (a bare Alchemy API key works too).
    "RPC_URL": "",
    "CALL_RPC_URL": "",
    # Optional password for the page (worth setting when it's on Streamlit Cloud).
    "APP_PASSWORD": "",
    # Real PnL: what it costs to sell back the token side, on top of the pool fee.
    "SELL_SLIPPAGE_PCT": "1.0",
    # Median hold (hours) from which a wallet is a Multiday player; below it, Degen.
    "MULTIDAY_HOURS": "2",
    # Positions opened up to this many days before the period still count when
    # they close (or are still open) inside it.
    "LOOKBACK_DAYS": "14",
    # Read the wallet's positions from the Blockscout explorer API first (saves
    # the RPC most of the work); 0 = always use eth_getLogs.
    "EXPLORER_API": "1",
    # RH Wallet Finder's database, read-only, as the first source: empty = the
    # one next to this folder (rh_wallet_finder/data/wallets.db), 0 = don't.
    "WALLET_FINDER_DB": "",
    "RPC_WORKERS": "6",
    "LOG_CHUNK": "20000",
    # Public mode: every visitor connects their own Alchemy key (and, if they like,
    # another RPC); nothing of theirs is written to disk and the RPC links above are
    # never used. auto = on when running on Streamlit Community Cloud.
    "BYO_KEYS": "auto",
}
SETTING_KEYS = ("RPC_URL", "CALL_RPC_URL", "SELL_SLIPPAGE_PCT", "MULTIDAY_HOURS", "LOOKBACK_DAYS", "EXPLORER_API",
                "RPC_WORKERS")
SECRET_KEYS = ("RPC_URL", "CALL_RPC_URL", "APP_PASSWORD")


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
    """Settings from the environment (Streamlit secrets land there too), then
    .env, then DEFAULTS."""
    vals = dict(DEFAULTS)
    vals.update({k: v for k, v in _read_env(ENV_FILE).items() if v})
    for k in vals:
        if os.environ.get(k):
            vals[k] = os.environ[k]
    for k in ("RPC_URL", "CALL_RPC_URL"):
        vals[k] = rpc_link(vals[k])
    return vals


def rpc_link(v):
    """An RPC link as typed: a bare Alchemy API key becomes its Robinhood Chain link."""
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
    """An RPC URL without its key: https://rpc.solidrpc.io/ak_1234…abcd/evm/4663"""
    if not url:
        return ""
    parts = url.split("/")
    for i, p in enumerate(parts):
        if len(p) >= 20 and i > 2:
            parts[i] = p[:7] + "…" + p[-4:]
    return "/".join(parts)


def byo_keys(cfg=None):
    """True when each visitor brings their own keys (see BYO_KEYS)."""
    v = str((cfg or load_env()).get("BYO_KEYS", "auto")).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return Path("/mount/src").exists()  # Streamlit Community Cloud checks the repo out here


ALCHEMY_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")


def alchemy_key(v):
    """The API key out of whatever was pasted: the bare key or its Robinhood Chain link."""
    v = (v or "").strip()
    m = re.search(r"alchemy\.com/v2/([A-Za-z0-9_-]+)", v)
    if m:
        return m.group(1)
    return v if ALCHEMY_KEY_RE.fullmatch(v) else None
