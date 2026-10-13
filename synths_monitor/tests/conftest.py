import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synthmon.config import load_config  # noqa: E402
from synthmon.db import DB  # noqa: E402

WALLET = "0x1111111111111111111111111111111111111111"
OWN = "0xc0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0"
COIN_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
COIN_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(f'[bot]\nwallet = "{WALLET}"\nown_coin = "{OWN}"\n')
    return load_config(path, env={})


@pytest.fixture
def db():
    d = DB(":memory:")
    yield d
    d.close()
