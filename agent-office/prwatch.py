"""Pull-request status for petitions. `gh` when it is installed, else the public API.

Offline, missing, or rate-limited answers come back as None. Callers keep the
petition where it is. Results are cached so a poll loop does not hammer GitHub.
"""
import json
import re
import shutil
import subprocess
import time
import urllib.request

FIND_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")
PR_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)/?$")
TTL = 300
FAIL_TTL = 60
_cache = {}
_lock_import = None


def find_url(text):
    match = FIND_RE.search(text or "")
    return match.group(0) if match else ""


def parts_of(url):
    match = PR_RE.match((url or "").strip())
    if not match:
        return None
    return match.group(0).rstrip("/"), match.group(1), match.group(2), match.group(3)


def classify_gh(data):
    if not isinstance(data, dict):
        return None
    state = str(data.get("state") or "").upper()
    if state == "MERGED" or data.get("mergedAt"):
        return "merged"
    if state == "CLOSED":
        return "closed"
    if state == "OPEN":
        return "open"
    return None


def classify_api(data):
    if not isinstance(data, dict):
        return None
    if data.get("merged_at") or data.get("merged") is True:
        return "merged"
    state = str(data.get("state") or "").lower()
    if state == "closed":
        return "closed"
    if state == "open":
        return "open"
    return None


def _remember(key, status, now):
    _cache[key] = (now, status, TTL if status else FAIL_TTL)
    if len(_cache) > 200:
        oldest = sorted(_cache, key=lambda k: _cache[k][0])[:50]
        for k in oldest:
            _cache.pop(k, None)


def via_gh(url, runner=None):
    if runner is None:
        if not shutil.which("gh"):
            return None

        def runner(argv):
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=8)
            return proc.returncode, proc.stdout

    code, out = runner(["gh", "pr", "view", url, "--json", "state,mergedAt,url"])
    if code != 0 or not out:
        return None
    return classify_gh(json.loads(out))


def via_api(url, opener=None):
    parsed = parts_of(url)
    if not parsed:
        return None
    _, owner, repo, number = parsed
    api = "https://api.github.com/repos/%s/%s/pulls/%s" % (owner, repo, number)
    if opener is None:
        req = urllib.request.Request(api, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "agent-office",
        })

        def opener(target):
            with urllib.request.urlopen(req if target == api else urllib.request.Request(target), timeout=6) as res:
                return res.read().decode("utf-8", "replace")

    return classify_api(json.loads(opener(api)))


def fetch(url, runner=None, opener=None, now=None, use_cache=True):
    """Return 'open', 'merged', 'closed', or None when GitHub cannot be read."""
    parsed = parts_of(url)
    if not parsed:
        return None
    key = parsed[0]
    now = time.time() if now is None else now
    if use_cache:
        hit = _cache.get(key)
        if hit and now - hit[0] < hit[2]:
            return hit[1]
    status = None
    try:
        status = via_gh(key, runner)
    except Exception:
        status = None
    if status is None:
        try:
            status = via_api(key, opener)
        except Exception:
            status = None
    if use_cache:
        _remember(key, status, now)
    return status


def clear_cache():
    _cache.clear()
