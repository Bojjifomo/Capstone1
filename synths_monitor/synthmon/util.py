"""Small parsing helpers shared by the data sources."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable

WEI = 10**18
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TX_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")
ZERO_ADDRESS = "0x" + "0" * 40


def now() -> int:
    return int(time.time())


def is_address(value: Any) -> bool:
    return isinstance(value, str) and bool(ADDRESS_RE.match(value))


def norm_addr(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value.lower() if is_address(value) else None


def to_ts(value: Any) -> int | None:
    """Unix seconds from ISO strings, unix seconds or unix milliseconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        return int(v / 1000) if v > 1e11 else int(v)
    if isinstance(value, str):
        s = value.strip()
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return to_ts(float(s))
        if re.fullmatch(r"0x[0-9a-fA-F]+", s):
            return to_ts(int(s, 16))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return None


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if re.fullmatch(r"0x[0-9a-fA-F]+", s):
            return float(int(s, 16))
        try:
            return float(s)
        except ValueError:
            return None
    return None


def to_eth(value: Any, unit: str = "auto") -> float | None:
    """ETH amount from a decimal ETH value or an integer wei value.

    With unit="auto", integers of 1e12 or more are taken as wei: no realistic
    trade on a launchpad moves a trillion ETH, and 1e12 wei is 0.000001 ETH.
    """
    if isinstance(value, dict):
        return None
    if unit == "wei":
        f = to_float(value)
        return None if f is None else f / WEI
    f = to_float(value)
    if f is None:
        return None
    if unit == "eth":
        return f
    looks_integer = isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r"\d+", value.strip()))
    if looks_integer and abs(f) >= 1e12:
        return f / WEI
    return f


def fmt_ts(ts: int | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{s:02d}s"
    hours, m = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h{m:02d}m"
    days, h = divmod(hours, 24)
    return f"{days}d{h:02d}h"


def parse_since(value: str, reference: int | None = None) -> int:
    """Parse --since: '24h', '7d', '90m', '2w', an ISO date/datetime or unix seconds."""
    reference = now() if reference is None else reference
    s = value.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhdw])", s, re.IGNORECASE)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2).lower()]
        return int(reference - float(m.group(1)) * mult)
    ts = to_ts(s)
    if ts is None:
        raise ValueError(f"cannot parse time {value!r}; use e.g. 24h, 7d, 2026-10-01 or 2026-10-01T12:00Z")
    return ts


def median(values: Iterable[float]) -> float | None:
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
