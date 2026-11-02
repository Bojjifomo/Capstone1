"""One door for Grokbot: the rest of Capstone1, read-only, behind the same bearer token.

Agent Office already lets Grokbot read the town and file quests. This module adds the
pieces that lived outside it:

  - RH Wallet Finder   finder.py status / top / check / twin / pools / pool-results /
                       smart-lp / web-check, always the --json, read-only forms
  - LP Call Bot        heartbeat age, open positions (the same calls followers already got),
                       follower count. Subscriber chat ids never leave the Mac.
  - Chain              program health and open incidents, in a short form

Nothing here writes, syncs, spends RPC quota on purpose, or publishes. A sync, score,
import or publish stays with the orchestrator on the Mac (`python3 -m orchestrator`).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import briefing

EVM_RE = re.compile(r"0x[0-9a-fA-F]{40}")
SYMBOL_RE = re.compile(r"[A-Za-z0-9]{1,12}")
MAX_OUTPUT = 60_000
CACHE_SECONDS = 90
RUN_TIMEOUT = 180


class GatewayError(ValueError):
    """A bad parameter from the caller: 400."""


class Missing(LookupError):
    """The program is not on this Mac: 404 with the fix in the message."""


def _one(query, key, default=None):
    v = (query or {}).get(key)
    if isinstance(v, list):
        v = v[0] if v else None
    return default if v in (None, "") else v


def _int(query, key, default, lo, hi):
    v = _one(query, key, default)
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise GatewayError("%s must be a whole number" % key)
    if not lo <= v <= hi:
        raise GatewayError("%s must be between %d and %d" % (key, lo, hi))
    return str(v)


def _num(query, key, default):
    v = _one(query, key, default)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise GatewayError("%s must be a number" % key)
    if v < 0 or v > 1e12:
        raise GatewayError("%s is out of range" % key)
    return str(v)


def _flag(query, key):
    return str(_one(query, key, "")).lower() in ("1", "true", "yes", "on")


def _choice(query, key, default, allowed):
    v = str(_one(query, key, default))
    if v not in allowed:
        raise GatewayError("%s must be one of %s" % (key, ", ".join(allowed)))
    return v


def _wallet(query):
    w = str(_one(query, "wallet", "")).strip()
    if not EVM_RE.fullmatch(w):
        raise GatewayError("wallet must be an EVM address (0x + 40 hex)")
    return w.lower()


# ---- Wallet Finder: each tool turns query parameters into finder.py arguments ----------

def _top(q):
    argv = ["top", "--json", "-n", _int(q, "n", 20, 1, 200)]
    kind = _one(q, "kind")
    if kind:
        argv += ["--kind", _choice(q, "kind", "", ("multiday", "degen"))]
    if _flag(q, "real"):
        argv.append("--real")
    if _flag(q, "last_24h"):
        argv.append("--1d")
    return argv


def _wallet_check(q):
    argv = ["check", _wallet(q), "--json", "--positions", _int(q, "positions", 30, 0, 300)]
    if _flag(q, "real"):
        argv.append("--real")
    return argv


def _pools(q):
    argv = ["pools", "--json", "-n", _int(q, "n", 20, 1, 200),
            "--min-mcap", _num(q, "min_mcap", 500_000), "--min-tvl", _num(q, "min_tvl", 5_000),
            "--min-vol-1h", _num(q, "min_vol_1h", 0),
            "--sort", _choice(q, "sort", "blend", ("blend", "1h", "24h", "pace", "tvl", "vol1h", "vol24h"))]
    quote = str(_one(q, "quote", "")).strip()
    if quote:
        if not SYMBOL_RE.fullmatch(quote):
            raise GatewayError("quote is a token symbol, e.g. USDG or WETH")
        argv += ["--quote", quote]
    return argv


def _pool_results(q):
    argv = ["pool-results", "--json", "-n", _int(q, "n", 15, 1, 100), "--days", _int(q, "days", 7, 1, 90),
            "--min-positions", _int(q, "min_positions", 10, 1, 1000),
            "--min-wallets", _int(q, "min_wallets", 3, 1, 100)]
    sort = _one(q, "sort")
    if sort:
        argv += ["--sort", _choice(q, "sort", "", ("fee_apr", "net_apr", "fees", "pnl", "wallets"))]
    return argv


LP_TOOLS = {
    "status": ("Sync / ranking state, price queue, RPC used today", lambda q: ["status"]),
    "top": ("Top wallets: n, kind=multiday|degen, real, last_24h", _top),
    "wallet": ("One wallet: wallet=0x…, positions, real", _wallet_check),
    "twin": ("Wallets with the closest LP style: wallet=0x…, n",
             lambda q: ["twin", _wallet(q), "-n", _int(q, "n", 15, 1, 100)]),
    "pools": ("Best fee per $ liquidity: n, sort, min_mcap, min_tvl, min_vol_1h, quote", _pools),
    "pool-results": ("What LPs really kept per pool: n, days, min_positions, min_wallets, sort", _pool_results),
    "smart-lp": ("Smart LP Finder rankings next to our scores: n",
                 lambda q: ["smart-lp", "-n", _int(q, "n", 30, 1, 200)]),
    "web-check": ("The live web snapshot vs the last publish", lambda q: ["web-check"]),
}


def _parse(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


class Gateway:
    def __init__(self, cfg, home=None):
        self.cfg = cfg or {}
        self.home = Path(home or os.path.expanduser("~"))
        self.lock = threading.Lock()
        self.cache = {}
        self.slots = threading.BoundedSemaphore(2)  # a public caller cannot fan out finder.py

    # where things are
    def capstone_root(self):
        return briefing.orchestrator_root(self.cfg)

    def finder_dir(self):
        explicit = str(self.cfg.get("wallet_finder_dir") or "").strip()
        if explicit:
            d = Path(explicit).expanduser()
        else:
            root = self.capstone_root()
            d = root / "rh_wallet_finder" if root else None
        return d if d and (d / "finder.py").is_file() else None

    def lpbot_dir(self):
        explicit = str(self.cfg.get("lp_call_bot_dir") or "").strip()
        candidates = [Path(explicit).expanduser()] if explicit else [self.home / "Projects" / "lp_call_bot"]
        root = self.capstone_root()
        if root and not explicit:
            candidates.append(root / "lp_call_bot")
        for d in candidates:
            if (d / "bot.py").is_file():
                return d
        return None

    # Wallet Finder
    def lp(self, tool, query):
        if tool not in LP_TOOLS:
            raise LookupError("no such LP tool")
        argv = LP_TOOLS[tool][1](query)
        d = self.finder_dir()
        if not d:
            raise Missing("RH Wallet Finder is not on this Mac; set wallet_finder_dir or orchestrator_root in config.json")
        key = tuple(argv)
        now = time.time()
        with self.lock:
            hit = self.cache.get(key)
            if hit and now - hit["at"] < CACHE_SECONDS:
                return dict(hit["result"], cached=True)
        if not self.slots.acquire(timeout=30):
            raise RuntimeError("the LP tools are busy, try again shortly")
        try:
            result = self._run(d, argv)
        finally:
            self.slots.release()
        if result["ok"]:
            with self.lock:
                self.cache[key] = {"at": now, "result": result}
                for k in [k for k, v in self.cache.items() if now - v["at"] >= CACHE_SECONDS]:
                    del self.cache[k]
        return dict(result, cached=False)

    def _run(self, d, argv):
        env = {k: v for k, v in os.environ.items() if k != "AGENT_OFFICE_TOKEN"}
        env.update(NO_COLOR="1", TERM="dumb", PYTHONUNBUFFERED="1")
        started = time.time()
        try:
            p = subprocess.run([sys.executable, "finder.py", *argv], cwd=str(d), capture_output=True, text=True,
                               timeout=RUN_TIMEOUT, env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "tool": argv[0], "error": "stopped after %ds" % RUN_TIMEOUT}
        out = (p.stdout or "").strip()
        data = _parse(out)
        result = {"ok": p.returncode == 0, "tool": argv[0], "seconds": round(time.time() - started, 1)}
        if data is not None:
            result["data"] = data
        else:
            result["text"] = out[:MAX_OUTPUT]
        if p.returncode:
            result["error"] = ((p.stderr or "").strip().splitlines() or ["exit code %d" % p.returncode])[-1][:300]
        return result

    # LP Call Bot
    def lpbot(self):
        d = self.lpbot_dir()
        if not d:
            raise Missing("LP Call Bot is not installed; run sh lp_call_bot/install.sh on the Mac")
        hb = d / "heartbeat"
        age = time.time() - hb.stat().st_mtime if hb.exists() else None
        state = {}
        try:
            state = json.loads((d / "state.json").read_text())
        except (OSError, ValueError):
            pass
        positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
        open_ = [{k: p.get(k) for k in ("coin", "ca", "fee", "range", "updated")}
                 for p in positions.values() if isinstance(p, dict) and p.get("status") == "open"]
        open_.sort(key=lambda p: p.get("updated") or 0, reverse=True)
        subs = state.get("subscribers")
        return {
            "alive": age is not None and age < 15 * 60,
            "heartbeat_age_s": None if age is None else int(age),
            "open_positions": open_,
            "followers": len(subs) if isinstance(subs, list) else None,
        }

    # Chain
    @staticmethod
    def chain_summary(chain):
        view = chain.public()
        interns = [{"name": i.get("name"), "health": i.get("health"),
                    "incident": (i.get("incident") or {}).get("status")} for i in view.get("interns", [])]
        return {"interns": interns, "open_incidents": view.get("open", 0), "needs_you": view.get("needs_you", 0),
                "situation": chain.situation()}

    def manifest(self, chain=None):
        """What Grokbot can reach through this door, and whether each piece is on this Mac."""
        return {
            "name": "Capstone1 hub",
            "pieces": {
                "agent_office": True,
                "wallet_finder": bool(self.finder_dir()),
                "lp_call_bot": bool(self.lpbot_dir()),
                "orchestrator": bool(self.capstone_root()),
                "chain": chain is not None,
            },
            "lp_tools": {name: desc for name, (desc, _) in LP_TOOLS.items()},
            "routes": {
                "read": ["GET /api/hub", "GET /api/hub/status", "GET /api/lp/<tool>", "GET /api/lpbot",
                         "GET /api/chain/situation", "GET /api/chain/scorecard", "GET /api/state", "GET /api/team",
                         "GET /api/petitions", "GET /api/ledger", "GET /api/orchestrator", "GET /api/bots"],
                "write": ["POST /api/ledger", "POST /api/petitions", "POST /api/petitions/<id>/decide",
                          "POST /api/team/ask", "POST /api/job"],
            },
        }
