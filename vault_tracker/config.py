"""Settings stored in .env, shared by the CLI and the web UI."""

import os

FIELDS = {
    # key: (default, secret)
    "VAULT_ADDRESS": ("", False),
    "FOLLOWER_ADDRESS": ("", False),
    "TRACKER_NAME": ("", False),
    "COPY_MODE": ("mirror", False),
    "POLL_INTERVAL": ("30", False),
    "HISTORY_LIMIT": ("300", False),
    "MIN_ALERT_USD": ("5", False),
    "ALERT_TYPES": ("", False),
    "KEEP_AWAKE": ("1", False),
    "SOLANA_RPC_URL": ("", True),
    "TELEGRAM_BOT_TOKEN": ("", True),
    "TELEGRAM_CHAT_ID": ("", False),
    "DISCORD_WEBHOOK_URL": ("", True),
    "JUPITER_API_KEY": ("", True),
    # copytrade bot (see copier.py); the private key itself never goes in .env, only its file path
    "COPY_RUN": ("off", False),
    "COPY_KEYPAIR": ("", False),
    "COPY_BUDGET": ("SOL", False),
    "COPY_SIZE_MODE": ("", False),  # empty: "ratio" when COPY_SIZE_RATIO > 0, else "usd"
    "COPY_SIZE_USD": ("20", False),
    "COPY_SIZE_RATIO": ("0", False),
    "COPY_FOLLOW_PCT": ("100", False),
    "COPY_MAX_USD": ("100", False),
    "COPY_MAX_OPEN": ("3", False),
    "COPY_RESERVE_SOL": ("0.1", False),
    "COPY_MAX_PRICE_MOVE": ("2", False),
    "COPY_MAX_EVENT_AGE": ("180", False),
    "COPY_STRATEGY": ("spot", False),
    "COPY_SLIPPAGE_BPS": ("100", False),
    "COPY_EXIT_SWAP": ("1", False),
    "COPY_PRIORITY": ("50000", False),
    "COPY_MAX_NEW_BIN_ARRAYS": ("1", False),
    "COPY_FOLLOW_LIQUIDITY": ("1", False),
    "COPY_MIN_ACTION_USD": ("2", False),
    "COPY_TP_PCT": ("0", False),
    "COPY_SL_PCT": ("0", False),
    "COPY_OOR_CLOSE_MIN": ("0", False),
    "COPY_TG_COMMANDS": ("1", False),
}
SECRETS = {k for k, (_, secret) in FIELDS.items() if secret}


def _parse_line(line):
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None, None
    k, v = s.split("=", 1)
    return k.strip(), v.strip().strip('"').strip("'")


def load(path=".env"):
    """Current settings: .env file, then environment, then defaults."""
    values = {}
    try:
        with open(path) as f:
            for line in f:
                k, v = _parse_line(line)
                if k:
                    values[k] = v
    except OSError:
        pass
    out = {}
    for k, (default, _) in FIELDS.items():
        out[k] = values.get(k, os.environ.get(k, default))
    return out


def save(updates, path=".env"):
    """Write the given keys into .env, keeping every other line (comments included)."""
    updates = {k: str(v).replace("\n", "").replace("\r", "").strip() for k, v in updates.items() if k in FIELDS}
    lines, seen = [], set()
    try:
        with open(path) as f:
            for line in f:
                k, _ = _parse_line(line)
                if k in updates:
                    lines.append(f"{k}={updates[k]}\n")
                    seen.add(k)
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    except OSError:
        pass
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}\n")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)  # holds bot tokens / API keys
    except OSError:
        pass
    for k, v in updates.items():  # Notifier / PriceOracle / Rpc read these from the environment
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
