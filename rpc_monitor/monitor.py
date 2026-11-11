"""RPC Monitor: which RPC every bot uses, how much of each plan is spent, which one is close to its limit.

Four sources, none of them needs the bots to be changed to run:
  1. the bots' own config (.env, RH Secrets, launchd plists, Keychain for Sol Copy Bot): which RPC
     link each bot talks to. Two bots on the same key become one row, because they share one quota.
  2. the meter (meter.py) the bots in Capstone1 and lp_exit_watcher write to: real request counts,
     429s and "quota used up" replies, per minute.
  3. the bots' log files (from their launchd plists): new lines that say 429 / rate limit / quota.
  4. a probe every few minutes: one cheap request (eth_chainId / getHealth) per RPC, its latency,
     HTTP status and any rate-limit headers the provider sends.

Standard library only. Keys never leave this process: links are shown masked, ids are hashed.
"""
import calendar
import json
import os
import plistlib
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import meter

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

RH_PUBLIC = "https://rpc.mainnet.chain.robinhood.com"
RH_PUBLICNODE = "https://robinhood-rpc.publicnode.com"
SOL_PUBLIC = "https://api.mainnet-beta.solana.com"
ALCHEMY_RPC = "https://robinhood-mainnet.g.alchemy.com/v2/{key}"
SCRIPTABLE = Path.home() / "Library" / "Mobile Documents" / "iCloud~dk~simonbs~Scriptable" / "Documents"

LEVELS = ["?", "aman", "waspada", "hampir", "limit"]  # worst last
LEVEL_TEXT = {"?": "belum ada data", "aman": "aman", "waspada": "waspada", "hampir": "hampir limit", "limit": "LIMIT"}

# Free tiers as of late 2026. They change: check the provider's dashboard and set yours in config.json.
#   monthly  allowance per month in the provider's unit, per_call = units one request costs on average
#   daily    requests per day (0 = none), rps = requests per second (0 = unknown)
DEFAULT_PLANS = [
    {"name": "Helius Free", "match": "helius", "monthly": 1_000_000, "unit": "credit", "per_call": 1, "rps": 10},
    {"name": "Alchemy Free", "match": "alchemy.com", "monthly": 30_000_000, "unit": "CU", "per_call": 20, "rps": 25},
    {"name": "QuickNode", "match": "quiknode.pro", "monthly": 0, "unit": "credit", "per_call": 20, "rps": 15},
    {"name": "SolidRPC", "match": "solidrpc", "monthly": 0, "unit": "req", "per_call": 1, "rps": 0},
    {"name": "Robinhood publik", "match": "rpc.mainnet.chain.robinhood.com", "public": True},
    {"name": "PublicNode", "match": "publicnode.com", "public": True},
    {"name": "Solana publik", "match": "api.mainnet-beta.solana.com", "rps": 10, "public": True},
    {"name": "dRPC publik", "match": "drpc.org", "public": True},
    {"name": "Pocket", "match": "pocket.network", "public": True},
]

RPC_HOST_HINT = re.compile(r"rpc|helius|alchemy|quiknode|quicknode|drpc|ankr|publicnode|solana|chainstack|"
                           r"infura|triton|pocket|globalstake|syndica|shyft", re.I)
LIMITED_RE = re.compile(r"\b429\b|too many requests|rate.?limit|throttl|exceeded its compute units per second", re.I)
QUOTA_RE = re.compile(r"quota|monthly capacity|out of credits|insufficient credits|plan limit|credits? (exhausted|used up)|"
                      r"max(imum)? usage|upgrade your plan|capacity limit exceeded", re.I)
KEYISH = re.compile(r"[A-Za-z0-9_\-]{20,}")


# ------------------------------------------------------------------ helpers ----
def read_env(path):
    out = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lstrip("﻿")
        if k.startswith("export "):
            k = k[7:].strip()
        out[k] = v.strip().strip('"').strip("'")
    return out


def rpc_link(v):
    """Same rule as the bots: a bare Alchemy key becomes its Robinhood Chain link."""
    v = (v or "").strip()
    if v and "://" not in v and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", v):
        return ALCHEMY_RPC.format(key=v)
    return v


def split_urls(v):
    return [rpc_link(p) for p in re.split(r"[,\s]+", v or "") if p.strip()]


def mask(url):
    """https://rpc.solidrpc.io/ak_1234…abcd/evm/4663 and ?api-key=1234…abcd"""
    if not url:
        return ""
    url = re.sub(r"([?&](?:api[-_]?key|key|token)=)([^&]+)",
                 lambda m: m.group(1) + (m.group(2)[:4] + "…" + m.group(2)[-4:] if len(m.group(2)) > 10 else "…"), url)
    parts = url.split("/")
    for i, p in enumerate(parts):
        if i > 2 and len(p) >= 20:
            parts[i] = p[:7] + "…" + p[-4:]
    return "/".join(parts)


def scrub(text):
    """A log line without anything that looks like a key."""
    return KEYISH.sub(lambda m: m.group(0)[:4] + "…" + m.group(0)[-3:], text)[:220]


def chain_of(url, default="evm"):
    h = meter.host_of(url)
    if "solana" in h or "helius" in h or "triton" in h or "syndica" in h or "shyft" in h:
        return "solana"
    if "robinhood" in h or "/4663" in url or "alchemy" in h:
        return "evm"
    return default


def plan_for(url, cfg):
    host = meter.host_of(url)
    for p in list(cfg.get("plans") or []) + DEFAULT_PLANS:
        if p.get("match") and p["match"].lower() in url.lower():
            return dict(p)
    return {"name": host, "unit": "req", "per_call": 1, "monthly": 0, "rps": 0}


# ------------------------------------------------------------------ launchd ----
def launchd_agents(folder=None):
    """Every non-Apple LaunchAgent: label, logs, working dir, env, program args."""
    folder = Path(folder or Path.home() / "Library" / "LaunchAgents")
    out = []
    for p in sorted(folder.glob("*.plist")) if folder.is_dir() else []:
        try:
            with open(p, "rb") as f:
                d = plistlib.load(f)
        except Exception:
            continue
        label = str(d.get("Label") or p.stem)
        if label.startswith(("com.apple.", "com.google.", "com.microsoft.", "homebrew.")):
            continue
        out.append({
            "label": label,
            "args": [str(a) for a in (d.get("ProgramArguments") or [d.get("Program") or ""])],
            "workdir": str(d.get("WorkingDirectory") or ""),
            "env": {str(k): str(v) for k, v in (d.get("EnvironmentVariables") or {}).items()},
            "logs": list(dict.fromkeys(str(x) for x in (d.get("StandardOutPath"), d.get("StandardErrorPath")) if x)),
        })
    return out


def launchctl_pids():
    """{label: pid or None} for loaded agents; None when launchctl can't be asked (not a Mac)."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    pids = {}
    for line in out.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) == 3:
            pids[cols[2]] = int(cols[0]) if cols[0].strip().isdigit() else None
    return pids


def keychain(service, account):
    try:
        cmd = ["security", "find-generic-password", "-s", service] + (["-a", account] if account else []) + ["-w"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def scriptable_secret(filename, key):
    """One string value from a Scriptable secrets file (a JS object literal), without running it."""
    for name in ([filename] if isinstance(filename, str) else filename):
        p = SCRIPTABLE / name
        try:
            txt = re.sub(r"//[^\n]*", "", p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        m = re.search(r"\b%s\s*:\s*[\"']([^\"']*)[\"']" % re.escape(key), txt)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


# ------------------------------------------------------------------ bots ----
def _agent_for(agents, hints):
    for a in agents:
        hay = " ".join([a["label"], a["workdir"]] + a["args"]).lower()
        if any(h.lower() in hay for h in hints):
            return a
    return None


def _envs(*dicts):
    out = {}
    for d in dicts:  # first one wins, like the bots: shell/plist, then the bot's .env, then the root .env
        for k, v in d.items():
            if v and k not in out:
                out[k] = v
    return out


def discover(cfg, agents=None):
    """[{id, name, chain, labels, logs, endpoints: [{url, role}], notes}] for every bot found."""
    agents = launchd_agents(cfg.get("launch_agents_dir")) if agents is None else agents
    used = set()
    bots = []

    def add(bid, name, chain, eps, agent_hints, logs=(), notes=()):
        a = _agent_for([x for x in agents if x["label"] not in used], agent_hints) if agent_hints else None
        if a:
            used.add(a["label"])
        seen, clean = set(), []
        for url, role in eps:
            u = meter.normalize(url)
            if u and u not in seen:
                seen.add(u)
                clean.append({"url": u, "role": role})
        bots.append({"id": bid, "name": name, "chain": chain, "endpoints": clean,
                     "labels": [a["label"]] if a else [],
                     "logs": list(dict.fromkeys(list(a["logs"] if a else []) + [str(x) for x in logs])),
                     "notes": list(notes)})
        return a

    root = read_env(REPO / ".env")

    # RH Wallet Finder (+ its Streamlit app): RPC_URL for getLogs, CALL_RPC_URL for eth_call.
    e = read_env(REPO / "rh_wallet_finder" / ".env")
    eps = [(rpc_link(e.get("RPC_URL")), "utama (getLogs)"), (rpc_link(e.get("CALL_RPC_URL")), "eth_call")]
    add("rh_wallet_finder", "RH Wallet Finder", "evm", [x for x in eps if x[0]], ["rh_wallet_finder", "wallet-finder"],
        notes=[] if e.get("RPC_URL") else ["RPC_URL kosong di rh_wallet_finder/.env"])

    # LP Call Bot: own .env only, public Robinhood RPC when empty, backups after it.
    e = read_env(REPO / "lp_call_bot" / ".env")
    eps = [(rpc_link(e.get("RPC_URL")) or RH_PUBLIC, "utama")]
    eps += [(u, "cadangan") for u in split_urls(e.get("BACKUP_RPC_URLS")) + split_urls(e.get("CALL_RPC_URL"))]
    add("lp_call_bot", "LP Call Bot", "evm", eps, ["lp-call-bot", "lp_call_bot"], logs=[REPO / "lp_call_bot" / "bot.log"])

    # RH Terminal: both public RPCs, then RH_RPC_URL, then Alchemy (key from .env or RH Secrets on iCloud).
    e = _envs(read_env(REPO / "rh_terminal" / ".env"), root)
    key = (e.get("ALCHEMY_API_KEY") or scriptable_secret(["RH Secrets.js", "RHSecrets.js"], "ALCHEMY_API_KEY")).strip()
    eps = [(RH_PUBLIC, "publik"), (RH_PUBLICNODE, "publik")]
    eps += [(rpc_link(e.get("RH_RPC_URL")), "custom")] if e.get("RH_RPC_URL") else []
    eps += [(ALCHEMY_RPC.format(key=key), "Alchemy (kirim tx)")] if re.fullmatch(r"[A-Za-z0-9_-]+", key or "-") and key else []
    add("rh_terminal", "RH Terminal", "evm", eps, ["rh_terminal", "rh-terminal"],
        notes=["Widget RH Portfolio/RH Pool di iPhone pakai key Alchemy yang sama tapi nggak kehitung meter"] if key else [])

    # DLMM Terminal (+ signal): reads go to the public Solana node first, trades to SOLANA_RPC_URL (Helius).
    e = _envs(read_env(REPO / "dlmm_terminal" / ".env"), root)
    sol = e.get("SOLANA_RPC_URL") or ""
    eps = [(SOL_PUBLIC, "publik (baca)")] + ([(sol, "Helius (trade)")] if sol else [])
    add("dlmm_terminal", "DLMM Terminal (Solana)", "solana", eps, ["dlmm-signal", "dlmm_terminal"],
        logs=[Path.home() / ".dlmm-signal" / "signal.log"],
        notes=["Widget MET Portfolio di iPhone pakai Helius yang sama tapi nggak kehitung meter"] if sol else [])

    # LP Exit Watcher (its own repo): RH_RPC_URL + RH_RPC_FALLBACKS from its plist env or .env.
    a = _agent_for(agents, ["lp-exit-watcher", "lp_exit_watcher"])
    folders = [Path(cfg["lp_exit_watcher_dir"]).expanduser()] if cfg.get("lp_exit_watcher_dir") else []
    if a and a["workdir"]:
        folders.append(Path(a["workdir"]))
    folders += [Path.home() / "lp_exit_watcher", Path.home() / "Projects" / "lp_exit_watcher"]
    folder = next((f for f in folders if (f / ".env").is_file() or (f / "lp_exit_watcher").is_dir()), None)
    if a or folder:
        envfile = (a or {}).get("env", {}).get("LP_ENV_FILE") or (folder and folder / ".env")
        e = _envs((a or {}).get("env", {}), read_env(envfile) if envfile else {})
        eps = [(e.get("RH_RPC_URL") or RH_PUBLIC, "utama")] + [(u, "cadangan") for u in split_urls(e.get("RH_RPC_FALLBACKS"))]
        logs = [folder / "var" / "logs" / "launchd.out.log", folder / "var" / "logs" / "launchd.err.log"] if folder else []
        add("lp_exit_watcher", "LP Exit Watcher", "evm", eps, ["lp-exit-watcher", "lp_exit_watcher"], logs=logs)

    # Bots set up in config.json (Sol Copy Bot: links in the Keychain, code not in any repo).
    for x in cfg.get("extra_bots") or []:
        urls = list(x.get("urls") or [])
        if x.get("env_file"):
            ev = read_env(Path(x["env_file"]).expanduser())
            urls += [u for k, v in ev.items() if "RPC" in k.upper() for u in split_urls(v)]
        for kc in x.get("keychain") or []:
            for acct in kc.get("accounts") or [kc.get("account", "")]:
                urls += re.findall(r"https?://[^\s,;\"'\]]+", keychain(kc.get("service", ""), acct))
        add(x.get("id") or x.get("name", "bot"), x.get("name") or x.get("id"), x.get("chain", "evm"),
            [(u, "") for u in urls], x.get("labels") or [], logs=x.get("logs") or [],
            notes=[] if urls else ["RPC-nya belum ketemu: isi \"urls\" atau \"keychain\" di rpc_monitor/config.json"])

    # Any other LaunchAgent that has an RPC-looking link in its plist env or .env.
    for a in agents:
        if a["label"] in used or a["label"].startswith("com.bojjifomo.rpc-monitor"):
            continue
        env = _envs(a["env"], read_env(Path(a["workdir"]) / ".env") if a["workdir"] else {})
        urls = [u for k, v in env.items() for u in split_urls(v)
                if u.startswith("http") and ("RPC" in k.upper() or RPC_HOST_HINT.search(meter.host_of(u)))]
        if urls:
            add(a["label"], a["label"].split(".")[-1], "evm", [(u, "") for u in urls], [a["label"]])
    return bots


# ------------------------------------------------------------------ usage (meter files) ----
def read_usage(now=None, folder=None):
    """{ep: {...windows, per bot, per minute}} from the meter files of this month and the last two days."""
    now = time.time() if now is None else now
    folder = Path(folder or meter.folder())
    y, m = time.gmtime(now)[:2]
    month0 = calendar.timegm((y, m, 1, 0, 0, 0))
    day0 = now - now % 86400
    start = min(month0, now - 2 * 86400)
    eps = {}
    t = start - start % 86400
    while t <= now:
        p = folder / ("usage-%s.jsonl" % time.strftime("%Y-%m-%d", time.gmtime(t)))
        t += 86400
        try:
            fh = open(p, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    ts, ep = int(r["t"]), r["ep"]
                except (ValueError, KeyError, TypeError):
                    continue
                u = eps.setdefault(ep, {"host": r.get("host", "?"), "first": ts, "last": 0, "bots": {}, "minutes": {},
                                        "w": {w: dict.fromkeys(meter.OUTCOMES, 0) for w in ("1h", "24h", "today", "month")},
                                        "last_limited": 0, "last_quota": 0, "last_ok": 0})
                u["first"], u["last"] = min(u["first"], ts), max(u["last"], ts)
                n = {o: int(r.get(o) or 0) for o in meter.OUTCOMES}
                if n["limited"]:
                    u["last_limited"] = max(u["last_limited"], ts)
                if n["quota"]:
                    u["last_quota"] = max(u["last_quota"], ts)
                if n["ok"]:
                    u["last_ok"] = max(u["last_ok"], ts)
                for w, since in (("1h", now - 3600), ("24h", now - 86400), ("today", day0), ("month", month0)):
                    if ts >= since:
                        for o in meter.OUTCOMES:
                            u["w"][w][o] += n[o]
                if ts >= now - 86400:
                    b = u["bots"].setdefault(r.get("bot", "?"), {"calls": 0, "limited": 0, "last": 0})
                    b["calls"] += sum(n.values())
                    b["limited"] += n["limited"] + n["quota"]
                    b["last"] = max(b["last"], ts)
                if ts >= now - 3600:
                    u["minutes"][ts] = u["minutes"].get(ts, 0) + sum(n.values())
    return eps


# ------------------------------------------------------------------ logs ----
class LogWatch:
    """Reads only what the bots' logs got since the last look; keeps 48 h of rate-limit lines."""

    def __init__(self, state_path):
        self.path = Path(state_path)
        self.offsets, self.events = {}, []
        try:
            s = json.loads(self.path.read_text())
            self.offsets, self.events = s.get("offsets", {}), s.get("events", [])
        except (OSError, ValueError):
            pass

    def scan(self, bots, now=None):
        now = time.time() if now is None else now
        for b in bots:
            for log in b["logs"]:
                for line in self._new_lines(log):
                    kind = "quota" if QUOTA_RE.search(line) else "limited" if LIMITED_RE.search(line) else None
                    if kind:
                        hosts = [meter.host_of(e["url"]) for e in b["endpoints"]]
                        host = next((h for h in hosts if h != "?" and h in line), None)
                        self.events.append({"t": int(now), "bot": b["id"], "kind": kind, "host": host, "line": scrub(line)})
        self.events = [e for e in self.events if e["t"] >= now - 2 * 86400][-2000:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"offsets": self.offsets, "events": self.events}))
        except OSError:
            pass

    def _new_lines(self, log, cap=4_000_000):
        try:
            st = os.stat(log)
        except OSError:
            return []
        prev = self.offsets.get(log)
        if prev is None:  # first look at this log: start at its end, only new lines count
            self.offsets[log] = {"ino": st.st_ino, "pos": st.st_size}
            return []
        pos = prev["pos"] if prev.get("ino") == st.st_ino and prev.get("pos", 0) <= st.st_size else 0  # rotated
        start = max(pos, st.st_size - cap)
        try:
            with open(log, "rb") as f:
                f.seek(start)
                data = f.read(cap)
        except OSError:
            return []
        self.offsets[log] = {"ino": st.st_ino, "pos": start + len(data)}
        return data.decode("utf-8", "replace").splitlines()


# ------------------------------------------------------------------ probe ----
def probe(url, chain, timeout=10):
    """One cheap request: latency, HTTP status, outcome and the provider's rate-limit headers."""
    method = "getHealth" if chain == "solana" else "eth_chainId"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": []}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json", "user-agent": "rpc-monitor/1"})
    t0 = time.time()
    res = {"t": int(t0), "method": method, "http": None, "ms": None, "outcome": "error", "msg": "", "headers": {}}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res["http"], text, headers = r.status, r.read(20000).decode("utf-8", "replace"), r.headers
    except urllib.error.HTTPError as e:
        res["http"], headers = e.code, e.headers
        try:
            text = e.read(2000).decode("utf-8", "replace")
        except Exception:
            text = ""
    except Exception as e:  # DNS (TrustPositif), timeout, TLS
        res["ms"] = int((time.time() - t0) * 1000)
        res["msg"] = scrub(str(getattr(e, "reason", e)) or e.__class__.__name__)
        return res
    res["ms"] = int((time.time() - t0) * 1000)
    res["headers"] = {k.lower(): v for k, v in (headers.items() if headers else [])
                      if "ratelimit" in k.lower() or "rate-limit" in k.lower() or k.lower() == "retry-after"}
    try:
        j = json.loads(text)
        err = j.get("error") if isinstance(j, dict) else None
    except ValueError:
        j, err = None, None
    msg = json.dumps(err)[:200] if err else ("" if j else text[:200])
    if QUOTA_RE.search(msg):
        res["outcome"] = "quota"
    elif res["http"] == 429 or LIMITED_RE.search(msg):
        res["outcome"] = "limited"
    elif res["http"] == 200 and isinstance(j, dict) and "result" in j:
        res["outcome"] = "ok"
    elif res["http"] == 200 and chain == "solana" and err:  # getHealth "node is behind" still answers
        res["outcome"] = "ok"
    res["msg"] = scrub(msg)
    return res


def header_room(headers):
    """(remaining, limit) from x-ratelimit-* style headers, or (None, None)."""
    rem = lim = None
    for k, v in headers.items():
        m = re.match(r"\s*(\d+)", str(v))
        if not m:
            continue
        if "remaining" in k and rem is None:
            rem = int(m.group(1))
        elif k.endswith("limit") and lim is None:
            lim = int(m.group(1))
    return rem, lim


# ------------------------------------------------------------------ assess ----
def _days_in_month(now):
    y, m = time.gmtime(now)[:2]
    cur = calendar.timegm((y, m, 1, 0, 0, 0))
    nxt = calendar.timegm((y + (m == 12), m % 12 + 1, 1, 0, 0, 0))
    return (nxt - cur) / 86400, (now - cur) / 86400


def assess(ep, cfg, now=None):
    """Fills ep["level"], ep["reasons"] and the numbers the page shows."""
    now = time.time() if now is None else now
    plan, u, pr, logs = ep["plan"], ep.get("usage"), ep.get("probe"), ep.get("log_events", [])
    th = {"warn_pct": 70, "near_pct": 90, "limited_near": 10}
    th.update(cfg.get("thresholds") or {})
    level, why = "?", []

    def bump(lv, text):
        nonlocal level
        if LEVELS.index(lv) > LEVELS.index(level):
            level = lv
        if text:
            why.append(text)

    if pr or u:
        bump("aman", "")
    per_call = float(plan.get("per_call") or 1)
    days, elapsed = _days_in_month(now)
    nums = {}
    if u:
        w = u["w"]
        nums.update(calls_1h=sum(w["1h"].values()), calls_24h=sum(w["24h"].values()), calls_today=sum(w["today"].values()),
                    calls_month=sum(w["month"].values()), limited_1h=w["1h"]["limited"], quota_24h=w["24h"]["quota"],
                    errors_1h=w["1h"]["error"], last_call=u["last"])
        span_d = max(1 / 24, min(1.0, (now - u["first"]) / 86400))  # rate from up to the last 24 h
        recent = w["24h"]["ok"] if now - u["first"] >= 86400 else w["month"]["ok"]
        per_day = recent / span_d
        nums["per_day"] = round(per_day)
        peak = max(u["minutes"].values()) / 60 if u["minutes"] else 0
        nums["peak_rps"] = round(peak, 2)
        monthly = float(plan.get("monthly") or 0)
        if monthly:
            used = w["month"]["ok"] * per_call
            proj = used + per_day * per_call * max(0, days - elapsed)
            nums.update(used_pct=round(used / monthly * 100, 1), proj_pct=round(proj / monthly * 100, 1),
                        used_units=round(used), monthly=monthly, unit=plan.get("unit", "req"))
            if used >= monthly:
                bump("limit", "kuota bulan ini udah habis (%s %s)" % (fmt(used), plan.get("unit", "")))
            elif nums["proj_pct"] >= th["near_pct"]:
                bump("hampir", "dengan laju sekarang (~%s/hari) kuota bulan ini kepake %d%% sebelum akhir bulan"
                     % (fmt(per_day), nums["proj_pct"]))
            elif nums["proj_pct"] >= th["warn_pct"]:
                bump("waspada", "proyeksi akhir bulan %d%% dari kuota" % nums["proj_pct"])
        daily = float(plan.get("daily") or 0)
        if daily:
            pct = sum(w["today"].values()) / daily * 100
            nums["daily_pct"] = round(pct, 1)
            if pct >= 100:
                bump("limit", "batas harian (%s) udah lewat" % fmt(daily))
            elif pct >= th["near_pct"]:
                bump("hampir", "hari ini udah %d%% dari batas harian %s" % (pct, fmt(daily)))
            elif pct >= th["warn_pct"]:
                bump("waspada", "hari ini %d%% dari batas harian" % pct)
        rps = float(plan.get("rps") or 0)
        if rps and peak:
            nums["rps_pct"] = round(peak / rps * 100)
            if nums["rps_pct"] >= th["near_pct"]:
                bump("hampir", "menit tersibuk %.1f req/detik, batasnya %g" % (peak, rps))
            elif nums["rps_pct"] >= th["warn_pct"]:
                bump("waspada", "menit tersibuk %.1f req/detik (batas %g)" % (peak, rps))
        if u["last_quota"] >= now - 3600:
            bump("limit", "provider bilang kuota habis, %s" % ago(u["last_quota"], now))
        if w["1h"]["limited"] >= th["limited_near"]:
            bump("hampir", "%d kali kena 429 dalam 1 jam terakhir" % w["1h"]["limited"])
        elif w["1h"]["limited"]:
            bump("waspada", "%d kali kena 429 dalam 1 jam terakhir" % w["1h"]["limited"])
    lq = [e for e in logs if e["kind"] == "quota" and e["t"] >= now - 3600]
    ll = [e for e in logs if e["kind"] == "limited" and e["t"] >= now - 3600]
    if lq:
        bump("limit", "log %s: \"%s\"" % (lq[-1]["bot"], lq[-1]["line"][:90]))
    if len(ll) >= th["limited_near"]:
        bump("hampir", "%d baris rate limit di log bot dalam 1 jam" % len(ll))
    elif ll:
        bump("waspada", "%d baris rate limit di log bot dalam 1 jam" % len(ll))
    if pr:
        rem, lim = header_room(pr.get("headers") or {})
        if rem is not None and lim:
            nums["header_room_pct"] = round(rem / lim * 100)
            if rem / lim <= 0.1:
                bump("hampir", "header provider: sisa %d dari %d" % (rem, lim))
        if pr["outcome"] == "quota":
            bump("limit", "probe: provider bilang kuota habis (%s)" % pr["msg"][:80])
        elif pr["outcome"] == "limited":
            bump("hampir", "probe kena 429 %s" % ago(pr["t"], now))
        elif pr["outcome"] == "error":
            bump("waspada", "probe gagal: %s" % ((pr.get("msg") or "HTTP %s" % pr.get("http"))[:80]))
    if plan.get("public") and level in ("aman", "?"):
        why.append("RPC publik: nggak ada kuota bulanan, limitnya per IP dan nggak diumumin; dipantau dari 429")
    elif u and not plan.get("monthly") and not plan.get("daily") and not plan.get("public"):
        why.append("kuota plan belum diisi: set \"monthly\" / \"daily\" buat %s di config.json" % ep["id"])
    if not u and not plan.get("public"):
        why.append("belum ada hitungan dari meter (bot belum jalan sejak meter dipasang, atau bot ini nggak pakai meter)")
    ep.update(level=level, level_text=LEVEL_TEXT[level], reasons=why, nums=nums)
    return ep


def fmt(n):
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "jt"), (1e3, "rb")):
        if abs(n) >= div:
            return ("%.1f" % (n / div)).rstrip("0").rstrip(".") + suf
    return "%d" % n


def ago(ts, now=None):
    s = int((time.time() if now is None else now) - ts)
    if s < 90:
        return "%d detik lalu" % s
    if s < 5400:
        return "%d menit lalu" % (s // 60)
    if s < 172800:
        return "%d jam lalu" % (s // 3600)
    return "%d hari lalu" % (s // 86400)


# ------------------------------------------------------------------ the whole picture ----
class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.data = Path(cfg.get("data_dir") or meter.folder())
        self.logs = LogWatch(self.data / "monitor-state.json")
        self.probes = {}
        self.bots, self.agents, self.pids = [], [], {}
        self.found_at = 0
        self.lock = threading.Lock()
        self.snapshot = None

    def refresh_bots(self, force=False):
        if force or time.time() - self.found_at > 300:
            self.agents = launchd_agents(self.cfg.get("launch_agents_dir"))
            self.bots = discover(self.cfg, self.agents)
            self.found_at = time.time()
        self.pids = launchctl_pids()

    def endpoints(self):
        eps = {}
        daily_caps = {}
        wf = read_env(REPO / "rh_wallet_finder" / ".env")
        if wf.get("RPC_CALLS_PER_DAY") and wf.get("RPC_URL"):
            try:
                daily_caps[meter.endpoint_id(rpc_link(wf["RPC_URL"]))] = float(wf["RPC_CALLS_PER_DAY"])
            except ValueError:
                pass
        for b in self.bots:
            for e in b["endpoints"]:
                eid = meter.endpoint_id(e["url"])
                ep = eps.get(eid)
                if not ep:
                    plan = plan_for(e["url"], self.cfg)
                    plan.update((self.cfg.get("endpoints") or {}).get(eid) or {})
                    if eid in daily_caps and not plan.get("daily"):
                        plan["daily"] = daily_caps[eid]
                    ep = eps[eid] = {"id": eid, "url": e["url"], "masked": mask(e["url"]), "host": meter.host_of(e["url"]),
                                     "chain": chain_of(e["url"], b["chain"]), "plan": plan, "bots": []}
                ep["bots"].append({"id": b["id"], "name": b["name"], "role": e["role"]})
        return eps

    def probe_due(self, eps, force=False):
        every = float(self.cfg.get("probe_every_s", 300))
        for eid, ep in eps.items():
            last = self.probes.get(eid)
            if ep["plan"].get("probe") is False:
                continue
            if force or not last or time.time() - last["t"] >= every:
                self.probes[eid] = probe(ep["url"], ep["chain"])

    def tick(self, probe_now=False):
        with self.lock:
            self.refresh_bots(force=probe_now)
            self.logs.scan(self.bots)
            eps = self.endpoints()
            self.probe_due(eps, force=probe_now)
            snap = self.build(eps)
            self.snapshot = snap
            return snap

    def build(self, eps, now=None):
        now = time.time() if now is None else now
        usage = read_usage(now, self.data)
        for eid, ep in eps.items():
            ep["usage"] = usage.get(eid)
            ep["probe"] = self.probes.get(eid)
            bot_ids = {b["id"] for b in ep["bots"]}
            # A log line counts for this RPC when it names its host, or when the bot has no other RPC.
            single = {b["id"] for b in self.bots if len(b["endpoints"]) == 1}
            ep["log_events"] = [e for e in self.logs.events if e["bot"] in bot_ids and
                                (e["host"] == ep["host"] or (e["host"] is None and e["bot"] in single))]
            assess(ep, self.cfg, now)
        bots = []
        for b in self.bots:
            mine = [eps[meter.endpoint_id(e["url"])] for e in b["endpoints"]]
            last_call = max([(ep.get("usage") or {}).get("bots", {}).get(b["id"], {}).get("last", 0) for ep in mine] or [0])
            known = self.pids is not None and b["labels"]
            running = [lb for lb in b["labels"] if (self.pids or {}).get(lb)]
            worst = max([ep["level"] for ep in mine] or ["?"], key=LEVELS.index)
            bots.append({"id": b["id"], "name": b["name"], "chain": b["chain"], "labels": b["labels"],
                         "running": bool(running) if known else None, "last_call": last_call or None,
                         "level": worst, "level_text": LEVEL_TEXT[worst], "notes": b["notes"],
                         "endpoints": [{"id": ep["id"], "masked": ep["masked"], "role": e["role"], "level": ep["level"]}
                                       for ep, e in zip(mine, b["endpoints"])],
                         "log_events_24h": sum(1 for e in self.logs.events if e["bot"] == b["id"] and e["t"] >= now - 86400)})
        out_eps = []
        for ep in sorted(eps.values(), key=lambda x: (-LEVELS.index(x["level"]), x["host"])):
            u = ep.get("usage") or {}
            out_eps.append({k: ep[k] for k in ("id", "masked", "host", "chain", "bots", "level", "level_text", "reasons", "nums", "probe")}
                           | {"plan": {k: v for k, v in ep["plan"].items() if k != "match"},
                              "per_bot_24h": u.get("bots", {}),
                              "log_lines": [{"t": e["t"], "bot": e["bot"], "kind": e["kind"], "line": e["line"]}
                                            for e in ep["log_events"][-5:]]})
        counts = {lv: sum(1 for e in out_eps if e["level"] == lv) for lv in LEVELS}
        return {"now": now, "endpoints": out_eps, "bots": bots, "counts": counts,
                "meter_dir": str(self.data), "probe_every_s": self.cfg.get("probe_every_s", 300)}
