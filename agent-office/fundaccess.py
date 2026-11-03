"""Which guild agents may work on the Fund, and what they may do there.

Put an agent in FUND_DIVISION ("The Counting House") and it gets:
  - a Fund section in its CLAUDE.md: the commands, and how to think critically about money
  - .claude/settings.json rules that let its headless runs call exactly those commands:
    read the report, read LP flow, list recommendations, propose one, argue on one.
Balances, expenses, budgets and the rate stay yours: no rule lets an agent change them.
Move the agent out of the division and the rules are taken out again (your own rules stay).
"""
import json
from pathlib import Path

FUND_DIVISION = "The Counting House"
READ_AND_ARGUE = ("report", "lp", "rec list", "rec add", "rec note")


def fund_cli(cfg):
    raw = str((cfg or {}).get("fund_dir") or "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / "Projects" / "fund"
    return base / "fund.py"


def rules(cfg):
    cli = fund_cli(cfg)
    return ["Bash(python3 %s %s:*)" % (cli, sub) for sub in READ_AND_ARGUE]


def brief_section(cfg, name):
    cli = fund_cli(cfg)
    return (
        "\n## The Fund (bojjifomo's money)\n\n"
        "You sit in %s, so you work on the Fund app: net worth, expenses, and recommendations with their "
        "real results. Use only these commands (the others are refused):\n\n"
        "- `python3 %s report`: everything (net worth, sleeves, accounts and stale ones, history, this month's "
        "spend vs budget, runway, saving rate, LP value and flow, open recommendations with every note on them).\n"
        "- `python3 %s lp --days 30`: LP value and money flow (read-only, no RPC).\n"
        "- `python3 %s rec list`: all recommendations and notes.\n"
        "- `python3 %s rec add \"title\" --body \"…\" --sleeve N --expected <IDR/month> --by \"%s\"`: propose one.\n"
        "- `python3 %s rec note <id> \"…\" --stance support|challenge|question --by \"%s\"`: argue on one.\n\n"
        "### Think critically\n\n"
        "1. Start from the numbers in `report`, not from memory. Quote the figure and its date.\n"
        "2. Before you propose, try to kill your own idea: what has to be true, what would make it lose money, "
        "what it costs (fees, tax, time, liquidity), and what the base rate says.\n"
        "3. Read every open recommendation from other agents. Leave a `challenge` note on any with a weak number, "
        "a hidden risk, a missing cost or a better alternative; a `support` note only with a reason they missed.\n"
        "4. Expected impact in IDR/month must be conservative and show the arithmetic in the body.\n"
        "5. Check what came of past `done` ones (target vs actual) and learn from the misses.\n"
        "6. Never move money, never ask for keys, never change balances or expenses; bojjifomo decides and executes "
        "in the Saran tab.\n"
    ) % (FUND_DIVISION, cli, cli, cli, cli, name, cli, name)


def in_fund(member):
    return (member.get("division") or "") == FUND_DIVISION and member.get("model") != "grok"


def sync_settings(member, cfg):
    """Add or remove the Fund rules in <folder>/.claude/settings.json, keeping anything else in it."""
    path = Path(member["folder"]) / ".claude" / "settings.json"
    ours = rules(cfg)
    try:
        data = json.loads(path.read_text()) if path.is_file() else {}
    except ValueError:
        return  # someone's hand-edited file we can't read: leave it alone
    if not isinstance(data, dict):
        return
    perms = data.setdefault("permissions", {})
    allow = [r for r in perms.get("allow", []) if not (isinstance(r, str) and "fund.py" in r and r.startswith("Bash("))]
    if in_fund(member):
        allow += ours
    if allow:
        perms["allow"] = allow
    else:
        perms.pop("allow", None)
    if not perms:
        data.pop("permissions", None)
    if not data and not path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
