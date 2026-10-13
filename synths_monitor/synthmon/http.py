"""HTTP helpers with API-key redaction and per-source backoff."""

from __future__ import annotations

import random
import re
import time
from typing import Any

import requests

from . import __version__

USER_AGENT = f"synthmon/{__version__} (read-only monitor)"


class SourceError(RuntimeError):
    """A data source failed; the poller falls back to the next source."""


class RateLimited(SourceError):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


_KEY_IN_PATH = re.compile(r"(/v[23]/)[^/?#]+")
_KEY_IN_QUERY = re.compile(r"((?:api_?key|apikey|token|key)=)[^&#]+", re.I)
_TELEGRAM_TOKEN = re.compile(r"(/bot)[^/]+")
_DISCORD_TOKEN = re.compile(r"(/api/webhooks/\d+/)[^/?#]+")


def redact(url: str) -> str:
    """Strip API keys and webhook tokens from a URL before it is logged or stored."""
    url = _KEY_IN_PATH.sub(r"\1***", url)
    url = _KEY_IN_QUERY.sub(r"\1***", url)
    url = _TELEGRAM_TOKEN.sub(r"\1***", url)
    return _DISCORD_TOKEN.sub(r"\1***", url)


class Http:
    def __init__(self, timeout: float = 20.0, headers: dict[str, str] | None = None):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        if headers:
            self.session.headers.update(headers)

    def _check(self, resp: requests.Response, url: str) -> Any:
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After")
            raise RateLimited(f"429 from {redact(url)}", float(retry) if retry and retry.isdigit() else None)
        if resp.status_code >= 400:
            raise SourceError(f"HTTP {resp.status_code} from {redact(url)}: {resp.text[:200]!r}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"non-JSON response from {redact(url)}: {resp.text[:120]!r}") from exc

    def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SourceError(f"GET {redact(url)} failed: {type(exc).__name__}") from exc
        return self._check(resp, url)

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
        try:
            resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SourceError(f"POST {redact(url)} failed: {type(exc).__name__}") from exc
        return self._check(resp, url)


class Backoff:
    """Exponential backoff with jitter, tracked per source.

    After a failure the source is skipped until `next_try`, doubling the wait
    each time up to `max_delay`. One success resets it.
    """

    def __init__(self, base: float = 60.0, max_delay: float = 900.0, clock=time.time):
        self.base = base
        self.max_delay = max_delay
        self.clock = clock
        self.failures = 0
        self.next_try = 0.0

    def ready(self) -> bool:
        return self.clock() >= self.next_try

    def delay(self, retry_after: float | None = None) -> float:
        raw = min(self.max_delay, self.base * (2 ** max(self.failures - 1, 0)))
        if retry_after:
            raw = max(raw, retry_after)
        return raw * random.uniform(0.9, 1.1)

    def failed(self, retry_after: float | None = None) -> float:
        self.failures += 1
        wait = self.delay(retry_after)
        self.next_try = self.clock() + wait
        return wait

    def succeeded(self) -> None:
        self.failures = 0
        self.next_try = 0.0
