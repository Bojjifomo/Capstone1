"""LP Agent (lpagent.io) check for a wallet on Robinhood Chain.

Without a key: just the portfolio link. With LPAGENT_API_KEY (from the LP
Agent portal) the dashboard pulls their own numbers next to ours, through
this server so the key never reaches the browser:
  GET /lp-positions/overview?owner=..&protocol=uniswap_v4|uniswap_v3&chain=ROBINHOOD
  GET /lp-positions/opening?owner=..&chain=ROBINHOOD
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config

CACHE_S = 600
OVERVIEW_FIELDS = [
    ("total_pnl", "Total PnL", "usd"), ("win_rate", "Win rate", "ratio"), ("total_fee", "Fees earned", "usd"),
    ("closed_lp", "Closed positions", "int"), ("avg_inflow", "Avg invested", "usd"),
    ("avg_monthly_pnl", "Avg monthly PnL", "usd"), ("expected_value", "Expected value", "usd"),
    ("total_inflow", "Total in", "usd"), ("total_outflow", "Total out", "usd"),
]


def link(addr):
    return config.LPAGENT_APP.format(addr=addr)


def _get(path, params, key):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    req = urllib.request.Request(f"{config.LPAGENT_API}{path}?{q}", headers={
        "x-api-key": key, "accept": "application/json", "user-agent": "rh-wallet-finder/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise LPAgentError("LP Agent rejected the API key (401). Check it in Settings.") from e
        if e.code == 429:
            raise LPAgentError("LP Agent rate limit (429). Try again in a minute.") from e
        body = e.read()[:300].decode("utf-8", "replace")
        raise LPAgentError(f"LP Agent HTTP {e.code}: {body}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise LPAgentError(f"Can't reach LP Agent: {e}") from e


class LPAgentError(Exception):
    pass


def _pick(v):
    """Fields come as a number or as {"ALL": n, "7D": n, ...}."""
    if isinstance(v, dict):
        for k in ("ALL", "all", "1M", "30D"):
            if k in v:
                return v[k]
        return next(iter(v.values()), None)
    return v


def _num(v):
    try:
        return float(_pick(v))
    except (TypeError, ValueError):
        return None


def _rows(payload):
    d = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(d, dict):
        for k in ("data", "positions", "items", "list"):
            if isinstance(d.get(k), list):
                return d[k]
        return [d]
    return d if isinstance(d, list) else []


def check(con, addr, key, fresh=False):
    addr = addr.lower()
    out = {"addr": addr, "link": link(addr), "has_key": bool(key)}
    if not key:
        return out
    if not fresh:
        r = con.execute("SELECT fetched, data FROM lpagent_cache WHERE addr=?", (addr,)).fetchone()
        if r and time.time() - r[0] < CACHE_S:
            return {**json.loads(r[1]), "cached": True}

    overview = {}
    errors = []
    for proto in ("uniswap_v4", "uniswap_v3"):
        try:
            rows = _rows(_get("/lp-positions/overview",
                              {"owner": addr, "protocol": proto, "chain": config.LPAGENT_CHAIN}, key))
        except LPAgentError as e:
            errors.append(str(e))
            continue
        if rows and isinstance(rows[0], dict):
            vals = {f: _num(rows[0].get(f)) for f, _, _ in OVERVIEW_FIELDS}
            if any(v for v in vals.values()):
                overview[proto] = vals
    opening = []
    try:
        for p in _rows(_get("/lp-positions/opening", {"owner": addr, "chain": config.LPAGENT_CHAIN}, key)):
            if not isinstance(p, dict):
                continue
            pnl = p.get("pnl") if isinstance(p.get("pnl"), dict) else {}
            opening.append({
                "pair": p.get("pairName") or p.get("pair") or "?",
                "value": _num(p.get("value")), "pnl": _num(pnl.get("value")), "pnl_pct": _num(pnl.get("percent")),
                "fees": (_num(p.get("collectedFee")) or 0) + (_num(p.get("unCollectedFee")) or 0),
                "in_range": p.get("inRange"), "age_h": _num(p.get("age")), "protocol": p.get("protocol"),
            })
    except LPAgentError as e:
        errors.append(str(e))
    out.update(overview=overview, opening=opening, fields=OVERVIEW_FIELDS,
               errors=sorted(set(errors)), fetched=int(time.time()))
    if overview or opening or not errors:
        con.execute("INSERT INTO lpagent_cache(addr, fetched, data) VALUES(?,?,?) ON CONFLICT(addr) "
                    "DO UPDATE SET fetched=excluded.fetched, data=excluded.data",
                    (addr, int(time.time()), json.dumps(out)))
        con.commit()
    return out
