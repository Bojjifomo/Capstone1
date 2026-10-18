"""Settings, contract addresses and the .env file.

Keys live in rh_wallet_finder/.env (git-ignored), never in the repo: the repo
is public. The dashboard's Settings panel writes the same file.
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
DATA_DIR = HERE / "data"
DB_PATH = Path(os.environ.get("RHWF_DB") or DATA_DIR / "wallets.db")

CHAIN_ID = 4663  # Robinhood Chain
LPAGENT_CHAIN = "ROBINHOOD"

# Uniswap V4 on Robinhood Chain (checked on chain: poolKeys, StateView reads,
# and pool ids hashed from PoolKeys all match).
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"

ZERO = "0x" + "0" * 40  # native ETH as a V4 currency
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"

# Tokens worth $1. Only addresses, never symbols: anyone can deploy a token
# called "USDC".
STABLES = {USDG}
ETH_LIKE = {ZERO, WETH}

# ETH/USDG pools used for the ETH price, deepest first. If the first has no
# price at some old block (not created yet) the next one is tried.
ETH_USD_POOLS = [
    "0x387bf619da4d3fb62bb276482693dba1b9b3520f573cabdfe033384a24125982",  # ETH/USDG 0.05%
    "0x24107d152f14a76d292123265ae3f3c71f863fc2f4ef7ba49d64e78d28ea379e",  # ETH/USDG 0.01%
    "0xfcfae8fa0bd6da961bcf5d990f27690932deac4f093e99bf3e871691c6586593",  # WETH/USDG 0.05%
]

EXPLORER = "https://robinhoodchain.blockscout.com"
LPAGENT_APP = "https://app.lpagent.io/portfolio?address={addr}&chain=ROBINHOOD"
LPAGENT_API = "https://api.lpagent.io/open-api/v1"

DEFAULTS = {
    # eth_getLogs goes to RPC_URL (SolidRPC takes ~25k-block ranges). Calls
    # (archive eth_call, multicalls) go to CALL_RPC_URL when it is set, e.g.
    # Alchemy, the same split RH Smart LP Finder uses; otherwise RPC_URL too.
    "RPC_URL": "",
    "CALL_RPC_URL": "",
    "LPAGENT_API_KEY": "",
    # How far back the first sync goes, and the window the metrics cover.
    "BACKFILL_DAYS": "30",
    "METRIC_DAYS": "30",
    # Real PnL: what it costs to sell back the token side of a position, on
    # top of the pool's own fee.
    "SELL_SLIPPAGE_PCT": "1.0",
    # Scoring budget per run, in positions valued.
    "SCORE_POSITIONS_PER_RUN": "4000",
    "MIN_CLOSED": "3",
    "RPC_WORKERS": "6",
    "LOG_CHUNK": "20000",
    "PORT": "4664",
}


# RH Smart LP Finder's own variable names: when this tool has no RPC set, it
# borrows that project's (logs = SolidRPC, calls = Alchemy).
SMART_LP_ENV = {"RPC_URL": ("RH_RPC_BACKUP1_LP_FINDER", "RH_RPC_BACKUP1"),
                "CALL_RPC_URL": ("RH_RPC_MAIN_LP_FINDER", "RH_RPC_MAIN")}


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


def smart_lp_finder_dir():
    """The RH Smart LP Finder project next to this folder, if it's there."""
    if os.environ.get("SMART_LP_FINDER_DIR"):
        d = Path(os.environ["SMART_LP_FINDER_DIR"]).expanduser()
        return d if d.is_dir() else None
    for d in [HERE.parent / "RH Smart LP Finder", *sorted(HERE.parent.iterdir())]:
        if d.is_dir() and (d / "lp-finder" / "route.ts").exists():
            return d
    return None


def load_env():
    """Settings from the environment, then .env, then RH Smart LP Finder's
    .env files (RPC links only), then DEFAULTS."""
    vals = dict(DEFAULTS)
    vals.update({k: v for k, v in _read_env(ENV_FILE).items() if v})
    for k in vals:
        if os.environ.get(k):
            vals[k] = os.environ[k]
    vals["RPC_SOURCE"] = "settings" if vals["RPC_URL"] else ""
    if not vals["RPC_URL"]:
        d = smart_lp_finder_dir()
        theirs = {}
        if d:  # lp-finder/.env wins over the root one, as in that project
            theirs.update(_read_env(d / ".env"))
            theirs.update({k: v for k, v in _read_env(d / "lp-finder" / ".env").items() if v})
        theirs.update({k: v for k, v in os.environ.items() if k.startswith("RH_RPC_")})
        for ours, names in SMART_LP_ENV.items():
            v = next((theirs[n] for n in names if theirs.get(n)), "")
            if v and not vals[ours]:
                vals[ours] = v
        if vals["RPC_URL"]:
            vals["RPC_SOURCE"] = f"{d.name}/.env" if d else "RH_RPC_* environment"
    return vals


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
    """Show an RPC URL without its key: https://rpc.solidrpc.io/ak_1234…abcd/evm/4663"""
    if not url:
        return ""
    parts = url.split("/")
    for i, p in enumerate(parts):
        if len(p) >= 20 and i > 2:
            parts[i] = p[:7] + "…" + p[-4:]
    return "/".join(parts)
