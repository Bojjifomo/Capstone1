"""`synthmon discover`: open synths.lol in a real browser and record the JSON it fetches.

It visits the home page, the synth's profile (clicking the posts / bags /
trades / launched tabs), one coin page and the leaderboard, captures every
fetch/XHR response that parses as JSON, and writes:

  discovery/endpoints.json         every captured request with a shape summary
  discovery/samples/NNN.json       the response bodies
  discovery/suggested_config.toml  endpoint templates to paste into config.toml

Read the samples before trusting the suggestions: they are keyword guesses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .util import is_address

TABS = ("posts", "bags", "trades", "launched", "holdings", "activity", "coins")
PROFILE_PATHS = ("/{handle}", "/@{handle}", "/synth/{handle}", "/synths/{handle}", "/agent/{handle}",
                 "/agents/{handle}", "/u/{handle}", "/profile/{handle}")
LEADERBOARD_PATHS = ("/leaderboard", "/leaderboards", "/rankings", "/top")
COIN_LINK_RE = re.compile(r"/(coin|coins|token|tokens|c|t)/(0x[0-9a-fA-F]{40})")

# Keyword -> endpoint name, first match wins; tested against the URL path and the page that triggered it.
KINDS = (
    ("leaderboard", ("leaderboard", "ranking", "rank")),
    ("holders", ("holder",)),
    ("posts", ("post", "feed", "tweet", "thought", "message")),
    ("bags", ("bag", "holding", "position", "portfolio", "balance")),
    ("launched", ("launched", "launches", "created", "deployed")),
    ("trades", ("trade", "swap", "activity", "transaction", "txs", "history")),
    ("coin", ("coin", "token", "market", "curve")),
    ("profile", ("synth", "agent", "profile", "user")),
)


def shape(value: Any, depth: int = 0) -> Any:
    """A compact type sketch of a JSON value (keys and types, first list element)."""
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"len={len(value)}"] if value else []
    if isinstance(value, str):
        if is_address(value):
            return "address"
        if re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
            return "hash"
        return "str"
    return type(value).__name__


def templatize(url: str, handle: str, wallet: str | None, coins: set[str]) -> str:
    """Replace the concrete handle / wallet / coin in a captured URL with placeholders."""
    out = url
    if wallet:
        out = re.sub(re.escape(wallet), "{wallet}", out, flags=re.I)
    for coin in coins:
        out = re.sub(re.escape(coin), "{coin}", out, flags=re.I)
    out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(handle)}(?![A-Za-z0-9_])", "{handle}", out, flags=re.I)
    return out


def classify(url: str, page_label: str) -> str | None:
    path = urlparse(url).path.lower() + "?" + (urlparse(url).query or "").lower()
    for kind, words in KINDS:
        if any(w in path for w in words):
            return kind
    for kind, words in KINDS:
        if any(w in page_label for w in words):
            return kind
    return None


def _score(kind: str, template: str, page_label: str) -> int:
    """Prefer requests scoped to this synth / coin over global feeds of the same kind."""
    score = 0
    if kind in ("posts", "bags", "trades", "launched", "profile") and ("{handle}" in template or "{wallet}" in template):
        score += 2
    if kind in ("coin", "holders") and "{coin}" in template:
        score += 2
    if kind in page_label:
        score += 1
    return score


def run_discovery(base_url: str, handle: str, out_dir: str, wallet: str | None = None, headful: bool = False,
                  wait_ms: int = 4000) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # optional dependency
        raise SystemExit("discover needs Playwright: pip install playwright && playwright install chromium") from exc

    out = Path(out_dir)
    (out / "samples").mkdir(parents=True, exist_ok=True)
    captured: list[dict[str, Any]] = []
    websockets: list[str] = []
    coins: set[str] = set()
    coin_paths: list[str] = []
    load_errors: list[str] = []
    label = {"page": "home"}

    def on_response(resp: Any) -> None:
        req = resp.request
        if req.resource_type not in ("fetch", "xhr", "other"):
            return
        try:
            body = resp.json()
        except Exception:
            return
        n = len(captured)
        (out / "samples" / f"{n:03d}.json").write_text(json.dumps(body, indent=2)[:2_000_000])
        captured.append({
            "n": n, "page": label["page"], "method": req.method, "url": resp.url, "status": resp.status,
            "post_data": req.post_data, "shape": shape(body),
        })

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        page = browser.new_page()
        page.on("response", on_response)
        page.on("websocket", lambda ws: websockets.append(ws.url))
        base = base_url.rstrip("/")

        def visit(path: str, name: str) -> bool:
            label["page"] = name
            url = urljoin(base + "/", path.lstrip("/"))
            try:
                resp = page.goto(url, wait_until="networkidle", timeout=45_000)
            except PWError as exc:
                load_errors.append(f"{url}: {str(exc).splitlines()[0]}")
                return False
            page.wait_for_timeout(wait_ms)
            return resp is not None and resp.status < 400

        def collect_coin_links() -> None:
            for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))"):
                m = COIN_LINK_RE.search(href or "")
                if m:
                    coins.add(m.group(2).lower())
                    coin_paths.append(m.group(0))

        if not visit("/", "home") and load_errors:
            browser.close()
            raise SystemExit(f"could not open {base_url}: {load_errors[-1]}")
        profile_found = False
        for template in PROFILE_PATHS:
            if visit(template.format(handle=handle), "profile") and handle.lower() in page.content().lower():
                profile_found = True
                break
        if profile_found:
            collect_coin_links()
            for tab in TABS:
                label["page"] = f"profile:{tab}"
                loc = page.get_by_role("tab", name=re.compile(tab, re.I))
                if loc.count() == 0:
                    loc = page.get_by_text(re.compile(rf"^\s*{tab}\s*$", re.I))
                if loc.count():
                    try:
                        loc.first.click(timeout=5_000)
                        page.wait_for_timeout(wait_ms)
                        collect_coin_links()
                    except PWError:
                        pass
        if coin_paths:
            visit(coin_paths[0], "coin")
        for path in LEADERBOARD_PATHS:
            if visit(path, "leaderboard"):
                break
        browser.close()

    best: dict[str, tuple[int, str]] = {}
    for c in captured:
        if c["method"] != "GET" or c["status"] >= 400:
            continue
        kind = classify(c["url"], c["page"])
        c["guess"] = kind
        c["template"] = templatize(c["url"], handle, wallet, coins)
        if kind:
            score = _score(kind, c["template"], c["page"])
            if kind not in best or score > best[kind][0]:
                best[kind] = (score, c["template"])
    suggestions = {kind: template for kind, (_, template) in best.items()}

    (out / "endpoints.json").write_text(json.dumps(
        {"base_url": base_url, "handle": handle, "profile_found": profile_found, "coins_seen": sorted(coins),
         "websockets": websockets, "load_errors": load_errors, "requests": captured}, indent=2))
    toml = ["# Suggested by `synthmon discover`. Check each against discovery/samples before use.", "[synths.endpoints]"]
    toml += [f'{k} = "{v}"' for k, v in suggestions.items()]
    if any(c["method"] == "POST" for c in captured):
        toml.append("# Some JSON came from POST requests (GraphQL?); see endpoints.json - they need a custom query.")
    (out / "suggested_config.toml").write_text("\n".join(toml) + "\n")
    return {"captured": len(captured), "suggestions": suggestions, "coins": sorted(coins),
            "profile_found": profile_found, "websockets": websockets}
