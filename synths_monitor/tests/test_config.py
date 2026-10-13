import pytest

from synthmon.config import ConfigError, load_config, rpc_url

from .conftest import WALLET


def write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_defaults_and_wallet_normalized(tmp_path):
    cfg = load_config(write(tmp_path, f'[bot]\nwallet = "{WALLET.upper().replace("0X", "0x")}"\n'), env={})
    assert cfg["bot"]["wallet"] == WALLET
    assert cfg["poll"]["interval_seconds"] == 60
    assert cfg["coin"]["open_threshold_eth"] == 0.5
    assert cfg["alerts"]["progress_thresholds_pct"] == [50, 90]


@pytest.mark.parametrize("text", [
    '[bot]\nprivate_key = "x"\n',
    '[wallet]\nmnemonic = "abandon abandon"\n',
    '[bot]\nseed_phrase = "x"\n',
])
def test_refuses_private_key_fields(tmp_path, text):
    with pytest.raises(ConfigError, match="read-only"):
        load_config(write(tmp_path, text), env={})


def test_refuses_private_key_shaped_value(tmp_path):
    with pytest.raises(ConfigError, match="private key"):
        load_config(write(tmp_path, '[bot]\nwallet = "0x' + "ab" * 32 + '"\n'), env={})


def test_refuses_private_key_shaped_env_value(tmp_path):
    with pytest.raises(ConfigError, match="private key"):
        load_config(write(tmp_path, ""), env={"SYNTHMON_WALLET": "cd" * 32})


def test_rejects_bad_address(tmp_path):
    with pytest.raises(ConfigError, match="0x address"):
        load_config(write(tmp_path, '[bot]\nwallet = "0x1234"\n'), env={})


def test_env_overrides_and_rpc(tmp_path):
    cfg = load_config(write(tmp_path, ""), env={"SYNTHMON_WALLET": WALLET, "SYNTHMON_DISCORD_WEBHOOK": "https://d/x"})
    assert cfg["bot"]["wallet"] == WALLET
    assert cfg["alerts"]["discord_webhook_url"] == "https://d/x"
    assert rpc_url(cfg, env={"ALCHEMY_API_KEY": "k"}) == "https://robinhood-mainnet.g.alchemy.com/v2/k"
    assert rpc_url(cfg, env={}) is None


def test_example_config_loads():
    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.example.toml", env={})
    assert cfg["bot"]["handle"] == "bojjibot"
