"""Configuration: TOML file + environment overrides + the private-key guard."""

from __future__ import annotations

import copy
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from .util import is_address, norm_addr

DEFAULTS: dict[str, Any] = {
    "bot": {
        "handle": "bojjibot",
        # The synth's own account (its trading wallet), not the owner's wallet. See `find-synth`.
        "wallet": "",
        # The wallet that created the synth. Informational only.
        "owner": "",
        # Coin the bot launched. Empty = take the newest coin from the synth's "launched" list.
        "own_coin": "",
    },
    "poll": {"interval_seconds": 60, "max_backoff_seconds": 900, "timeout_seconds": 20},
    "storage": {"db_path": "synthmon.sqlite3", "keep_raw_responses": True},
    "synths": {
        "enabled": True,
        "base_url": "https://synths.lol",
        # URL templates captured by `synthmon discover`. Placeholders: {handle} {wallet} {coin}.
        "endpoints": {
            "profile": "",
            "posts": "",
            "trades": "",
            "bags": "",
            "launched": "",
            "coin": "",
            "holders": "",
            "leaderboard": "",
        },
        # Optional exact field paths per endpoint, e.g. fields.trades.eth_amount = "amounts.eth".
        "fields": {},
        "headers": {},
        # "auto" | "wei" | "eth": how ETH amounts in the API are denominated.
        "eth_unit": "auto",
        # "auto" | "fraction" (0-1) | "percent" (0-100) for a progress field.
        "progress_scale": "auto",
    },
    "chain": {
        "name": "Robinhood Chain",
        "chain_id": 4663,
        # Etherscan-compatible block explorer API (Blockscout serves this at <explorer>/api).
        "explorer_api_url": "",
        "explorer_api_key": "",
        # JSON-RPC. Empty = Alchemy's robinhood-mainnet endpoint when ALCHEMY_API_KEY is set.
        "rpc_url": "",
        "alchemy_network": "robinhood-mainnet",
        # Wrapped ETH on Robinhood Chain; WETH movements count as ETH when pricing trades.
        "weth": ["0x0bd7d308f8e1639fab988df18a8011f41eacad73"],
        # First block to scan for the wallet's history (0 = from genesis).
        "start_block": 0,
        "token_decimals": 18,
    },
    "coin": {
        # The coin "opens" once its bonding curve has raised this much ETH.
        "open_threshold_eth": 0.5,
        # Optional: the curve contract that holds the raised ETH (on-chain progress fallback).
        "curve_address": "",
    },
    "alerts": {
        "discord_webhook_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "progress_thresholds_pct": [50, 90],
        "wallet_drop_pct": 15.0,
        # The drop alert re-arms once the wallet recovers to within this many % of baseline.
        "wallet_drop_rearm_pct": 10.0,
        # "total" = ETH + value of bags (falls back to ETH when bag values are unknown), "eth" = ETH only.
        "wallet_metric": "total",
        # 0 = use the first value the monitor observes.
        "baseline_eth": 0.0,
        "no_trade_hours": 2.0,
    },
    "fees": {
        # Platform trading fee used for the fee estimate. Set it to synths.lol's actual fee.
        "protocol_fee_bps": 100,
    },
    "exits": {
        # Extra regexes (case-insensitive) per exit type, tried before the built-in ones.
        "take_profit": [],
        "stop_loss": [],
        "timer": [],
        "ai": [],
        # How far a post may be from its sell to be linked to it.
        "match_before_minutes": 15,
        "match_after_minutes": 30,
    },
    "roundtrips": {
        # A position counts as closed once it is below this fraction of its peak size.
        "dust_fraction": 0.005,
    },
}

ENV_OVERRIDES = {
    "SYNTHMON_WALLET": ("bot", "wallet"),
    "SYNTHMON_OWN_COIN": ("bot", "own_coin"),
    "SYNTHMON_DB": ("storage", "db_path"),
    "SYNTHMON_DISCORD_WEBHOOK": ("alerts", "discord_webhook_url"),
    "SYNTHMON_TELEGRAM_BOT_TOKEN": ("alerts", "telegram_bot_token"),
    "SYNTHMON_TELEGRAM_CHAT_ID": ("alerts", "telegram_chat_id"),
    "SYNTHMON_RPC_URL": ("chain", "rpc_url"),
    "SYNTHMON_EXPLORER_API_URL": ("chain", "explorer_api_url"),
    "SYNTHMON_EXPLORER_API_KEY": ("chain", "explorer_api_key"),
}

# Key names that only ever hold signing secrets. The monitor is read-only and refuses them.
FORBIDDEN_KEY_RE = re.compile(r"priv(ate)?[_-]?key|secret[_-]?key|mnemonic|seed[_-]?phrase|^seed$|passphrase|keystore", re.I)
# 32 bytes of hex is the shape of a raw private key. Addresses are 20 bytes.
PRIVATE_KEY_VALUE_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


class ConfigError(ValueError):
    pass


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def guard_no_private_keys(obj: Any, path: str = "") -> None:
    """Refuse configs that contain anything shaped like a signing key."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            where = f"{path}.{key}" if path else str(key)
            if FORBIDDEN_KEY_RE.search(str(key)):
                raise ConfigError(
                    f"config key {where!r} looks like a place for a private key or seed. "
                    "This monitor is read-only and never takes one; remove it."
                )
            guard_no_private_keys(value, where)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            guard_no_private_keys(value, f"{path}[{i}]")
    elif isinstance(obj, str) and PRIVATE_KEY_VALUE_RE.match(obj.strip()):
        raise ConfigError(
            f"config value at {path!r} is 64 hex characters, the shape of a private key. "
            "The monitor only needs the wallet's public address (0x + 40 hex); remove it."
        )


def load_config(path: str | os.PathLike | None, env: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if env is None else env
    data: dict[str, Any] = {}
    if path is not None and Path(path).exists():
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    guard_no_private_keys(data)
    cfg = _merge(DEFAULTS, data)
    for var, (section, key) in ENV_OVERRIDES.items():
        if env.get(var):
            cfg[section][key] = env[var]
    guard_no_private_keys(cfg)

    wallet = cfg["bot"]["wallet"].strip()
    if wallet and not is_address(wallet):
        raise ConfigError(f"bot.wallet must be a 0x address (42 characters), got {wallet!r}")
    cfg["bot"]["wallet"] = norm_addr(wallet) or ""
    owner = cfg["bot"]["owner"].strip()
    if owner and not is_address(owner):
        raise ConfigError(f"bot.owner must be a 0x address, got {owner!r}")
    cfg["bot"]["owner"] = norm_addr(owner) or ""
    own = cfg["bot"]["own_coin"].strip()
    if own and not is_address(own):
        raise ConfigError(f"bot.own_coin must be a 0x token address, got {own!r}")
    cfg["bot"]["own_coin"] = norm_addr(own) or ""
    cfg["bot"]["handle"] = cfg["bot"]["handle"].lstrip("@")
    cfg["chain"]["weth"] = [a for a in (norm_addr(w) for w in cfg["chain"]["weth"]) if a]
    curve = cfg["coin"]["curve_address"].strip()
    cfg["coin"]["curve_address"] = norm_addr(curve) or ""
    return cfg


def rpc_url(cfg: dict[str, Any], env: dict[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    if cfg["chain"]["rpc_url"]:
        return cfg["chain"]["rpc_url"]
    key = env.get("ALCHEMY_API_KEY")
    if key:
        return f"https://{cfg['chain']['alchemy_network']}.g.alchemy.com/v2/{key}"
    return None


def has_webhook(cfg: dict[str, Any]) -> bool:
    a = cfg["alerts"]
    return bool(a["discord_webhook_url"] or (a["telegram_bot_token"] and a["telegram_chat_id"]))
