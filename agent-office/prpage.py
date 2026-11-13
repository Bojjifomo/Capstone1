"""The PR page: open pull requests across every Bojjifomo repo, merged only by bojjifomo from the Guild Hall.

Every `gh` call lives in a small function here so tests can replace it. Nothing in this module runs a paid
review: a verdict is only read from what the Chain already stored.
"""
import json
import re
import subprocess
from datetime import datetime

OWNER = "Bojjifomo"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
GREEN_BUCKETS = ("pass", "skipping")
LP_BOT = ("lp-call-bot", "lp_call_bot")
DIFF_LIMIT = 200_000


def _gh(args, timeout=30):
    proc = subprocess.run(["gh"] + list(args), capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def parse_target(repo, number):
    """('Bojjifomo/name', int) for a request body, or raises ValueError. Only our own repos."""
    full = str(repo or "").strip()
    owner, _, name = full.partition("/")
    if owner.lower() != OWNER.lower() or not REPO_RE.match(name) or name in (".", ".."):
        raise ValueError("only %s repositories" % OWNER)
    try:
        n = int(number)
    except (TypeError, ValueError):
        raise ValueError("bad pull request number")
    if n < 1:
        raise ValueError("bad pull request number")
    return "%s/%s" % (OWNER, name), n


def pr_url(repo, number):
    return "https://github.com/%s/pull/%d" % (repo, number)


def is_lp_bot(repo):
    return str(repo).rsplit("/", 1)[-1].lower() in LP_BOT


# ---- gh wrappers (mock these in tests) --------------------------------------

def gh_list_open():
    code, out, err = _gh(["search", "prs", "--owner", OWNER, "--state", "open", "--limit", "100",
                          "--json", "number,title,url,repository,author,createdAt,isDraft"])
    if code != 0:
        raise RuntimeError("gh could not list pull requests: " + (err or "").strip()[:160])
    return json.loads(out or "[]")


def gh_diff(repo, number):
    code, out, err = _gh(["pr", "diff", str(number), "-R", repo])
    if code != 0:
        raise RuntimeError("gh could not read the diff: " + (err or "").strip()[:160])
    return out


def gh_checks(repo, number):
    """Raw check rows. `gh pr checks` exits non-zero while checks fail or wait, so only unreadable output is an error."""
    code, out, err = _gh(["pr", "checks", str(number), "-R", repo, "--json", "name,bucket,state"])
    try:
        return json.loads(out or "[]")
    except ValueError:
        if "no checks reported" in (err or "").lower():
            return []
        raise RuntimeError("gh could not read the checks: " + (err or "").strip()[:160])


def gh_pr_info(repo, number):
    """isDraft, head commit and the newest commit time (epoch seconds, 0 when unknown) from one `gh pr view`."""
    code, out, err = _gh(["pr", "view", str(number), "-R", repo, "--json", "isDraft,headRefOid,commits"])
    if code != 0:
        raise RuntimeError("gh could not read the pull request: " + (err or "").strip()[:160])
    data = json.loads(out or "{}")
    newest = 0
    for c in data.get("commits") or []:
        try:
            newest = max(newest, datetime.fromisoformat(str(c.get("committedDate")).replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return {"draft": bool(data.get("isDraft")), "head": data.get("headRefOid") or "", "newest_commit": newest}


def gh_merge(repo, number):
    code, out, err = _gh(["pr", "merge", str(number), "-R", repo, "--squash", "--delete-branch"], timeout=60)
    if code != 0:
        raise RuntimeError("gh could not merge: " + ((err or out) or "").strip()[:200])
    return (out or "").strip()


def gh_comment(repo, number, text):
    code, out, err = _gh(["pr", "comment", str(number), "-R", repo, "--body", text])
    if code != 0:
        raise RuntimeError("gh could not comment: " + (err or "").strip()[:160])


# ---- state -------------------------------------------------------------------

def ci_state(rows):
    """'green' (all passed or skipped), 'red' (any failed or cancelled), 'pending', or 'none' (no checks at all)."""
    if not rows:
        return "none"
    buckets = [str((r or {}).get("bucket") or "").lower() for r in rows]
    if any(b in ("fail", "cancel") for b in buckets):
        return "red"
    if all(b in GREEN_BUCKETS for b in buckets):
        return "green"
    return "pending"


def verdict_for(chain, url):
    """Thorne's stored verdict for this pull request, plus the review record.

    Verdict is 'approve', 'changes', 'needs-you', 'failed', 'pending' or 'none'.
    Reads chain.reviews only; it never starts a review."""
    if chain is None:
        return "none", None
    with chain.lock:
        mine = [r for r in chain.reviews if str(r.get("url", "")).lower() == url.lower()]
    if not mine:
        return "none", None
    r = max(mine, key=lambda x: x.get("started") or 0)
    if not r.get("ended"):
        return "pending", r
    return {"done": "approve", "blocked": "changes", "needs-you": "needs-you"}.get(r.get("outcome"), "failed"), r


def is_stale(review, info):
    """True when a commit landed after the review began, so the approval no longer covers the head."""
    if not review or not info:
        return False
    return (info.get("newest_commit") or 0) > (review.get("started") or 0)


def can_merge(ci, verdict):
    return ci == "green" and verdict == "approve"


def list_open(chain):
    rows = []
    for pr in gh_list_open():
        repo = (pr.get("repository") or {}).get("nameWithOwner") or ""
        number = pr.get("number")
        if not repo or not number:
            continue
        try:
            ci = ci_state(gh_checks(repo, number))
        except Exception:
            ci = "unknown"
        url = pr.get("url") or pr_url(repo, number)
        verdict, review = verdict_for(chain, url)
        if verdict == "approve":
            try:
                if is_stale(review, gh_pr_info(repo, number)):
                    verdict = "stale"
            except Exception:
                verdict = "stale"  # cannot prove the approval covers the head, so do not offer merge
        rows.append({"repo": repo, "number": number, "title": pr.get("title") or "", "url": url,
                     "author": (pr.get("author") or {}).get("login") or "", "created": pr.get("createdAt") or "",
                     "draft": bool(pr.get("isDraft")), "ci": ci, "verdict": verdict,
                     "verdict_note": (review or {}).get("note") or "", "reviewer": (review or {}).get("reviewer") or "",
                     "can_merge": can_merge(ci, verdict) and not pr.get("isDraft"), "lp_bot": is_lp_bot(repo)})
    return rows


def diff_of(repo, number):
    text = gh_diff(repo, number)
    return text[:DIFF_LIMIT] + ("\n... diff cut here ..." if len(text) > DIFF_LIMIT else "")


def merge_checked(chain, repo, number):
    """Re-check CI and Thorne's verdict right now, then merge. Raises PermissionError when either is not good."""
    ci = ci_state(gh_checks(repo, number))
    if ci != "green":
        raise PermissionError("CI is %s; merge needs every check green" % ci)
    verdict, review = verdict_for(chain, pr_url(repo, number))
    if verdict != "approve":
        raise PermissionError("Thorne's verdict is %s; merge needs approve" % verdict)
    info = gh_pr_info(repo, number)
    if info["draft"]:
        raise PermissionError("this pull request is a draft")
    if is_stale(review, info):
        raise PermissionError("new commits landed after Thorne's review; ask for a fresh review")
    return gh_merge(repo, number)


def author_of(chain, repo, number):
    """Name of the guild member who opened the PR, from the Chain's review record, or ''."""
    _, review = verdict_for(chain, pr_url(repo, number))
    return (review or {}).get("by") or ""
