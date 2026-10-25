"""One Claude agent over every tool in tools.py.

    python3 -m orchestrator                    # chat (Indonesian or English)
    python3 -m orchestrator ask "top 5 multiday yang aktif minggu ini?"
    python3 -m orchestrator brief              # status + top wallets + Smart LP + web, one report
    python3 -m orchestrator tools              # list the tools and their risk

Tools marked write ask "y/N" before they run; --yes answers yes for you.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from .tools import ROOT, ToolError, all_tools

MODEL = os.environ.get("ORCHESTRATOR_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("ORCHESTRATOR_EFFORT", "medium")
MAX_STEPS = 40

SYSTEM = """You run the LP analysis tools of one trader, on their own machine:

- RH Wallet Finder: indexes every Uniswap V4 LP position on Robinhood Chain from the chain itself, prices it, and
  ranks wallets in two classes. Multiday = median hold >= 2 h (ranked on total PnL with open positions), Degen =
  faster (ranked on realized PnL + profit factor); bots are hidden. Figures cover the last 30 days, wallets idle
  for 14 days are left out. "Real PnL" subtracts what selling the returned tokens would cost.
- RH Smart LP Finder: a separate app that ranks the LPs of one pool on demand. The Wallet Finder imports its saved
  wallets and pool rankings; slf_* tools read that.
- Web showcase: a read-only Vercel page fed by snapshots the Wallet Finder uploads (web_* tools).
- Pool finder (rh_pools): which pools on Robinhood Chain pay LPs the most fee per $ of liquidity, over the last
  hour and 24 hours, with a market cap and TVL floor. Fee tiers are read on chain, so 0% wash pools drop out.

Reading the numbers:
- `dd` is already a percent: -21 means a -21% max drawdown, not -0.21%.
- `rpc_cap` 0 means no daily limit (RPC_CALLS_PER_DAY=0). Only when `rpc_today` >= a nonzero cap does pricing pause.
- `queue` = positions still waiting to be priced; the worker works through it on its own, newest wallets first.
- `copy` = followable by hand (✓ / ✗ / bot); `copiers` = how many other wallets already copy that wallet.
- rh_pool_results: `fee_apr` = fees ÷ capital-days × 365; `net_apr` = the same with PnL, so a high fee_apr with a low or
  negative net_apr means impermanent loss ate the fees. `top_share` near 100 = one wallet's result, not the pool's.
- `net` = realized PnL over the last 30 days in USD; `worth` = net worth now.

How to work:
- Answer in the user's language; they usually write casual Indonesian, so answer the same way.
- Look before you answer: numbers come from tool results, never from memory. Say when data is stale (compare
  synced_at / scored_at / generated with the current time you get from rh_status "now").
- When picking wallets to follow, weigh more than PnL: trades closed, win, drawdown, 7D vs 30D, net worth, hold
  time, whether the style is followable, and agreement between our score, Smart LP Finder and LP Agent. Say why.
- When picking pools to LP, weigh $/1k 1h and 24h together (a high 1h with a low 24h is a spike that may not
  last), pace, price change (IL risk), traders and flags; TVL counts every LP, so a tight in-range position
  earns more and an out-of-range one nothing. Give the GeckoTerminal link.
- Read tools are free to use. Tools marked write spend RPC quota, change the database or upload: use one only
  when the user's request needs it, and say what it will do first. The user may decline; then carry on without.
- Keep answers short: a table or a few bullets, wallet addresses shortened as 0x1234…abcd unless asked in full.
- If something is broken or missing (no RPC, empty DB, web not published), say exactly what to fix and where
  (Settings tab, .env, Vercel) using read_docs when needed."""

BRIEF = """Give me today's brief:
1. Wallet Finder health (sync / rank age, price queue, RPC calls today vs cap).
2. Top 5 Multiday and top 5 Degen (Real PnL), each with one line on why they stand out or what's risky.
3. Smart LP Finder: wallets that also place top 10 in a pool there, and how our score rates them.
4. Web showcase: is the live snapshot fresh, does it match the last publish?
5. Top 5 pools by fee per $ of liquidity (blend of 1h and 24h, default filters), one line each, and for those
   pools what LPs actually kept over 7 days (rh_pool_results: net APR vs fee APR) where we have the data.
6. Anything I should act on."""


def load_env():
    """KEY=value lines from .env files the user already keeps, without overriding the environment."""
    for path in (ROOT / ".env", ROOT / "orchestrator" / ".env"):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


class Agent:
    def __init__(self, auto_yes=False, verbose=False, out=print):
        import anthropic
        self.client = anthropic.Anthropic()
        self.tools = {t.name: t for t in all_tools()}
        self.auto_yes = auto_yes
        self.verbose = verbose
        self.out = out
        self.messages = []

    def confirm(self, tool, args):
        if self.auto_yes:
            return True
        if not sys.stdin.isatty():
            return False
        a = input(f"\n  ⚠ {tool.name} ({tool.risk}) {json.dumps(args, ensure_ascii=False)}  run it? [y/N] ")
        return a.strip().lower() in ("y", "yes", "ya")

    def call_tool(self, block):
        tool = self.tools.get(block.name)
        if not tool:
            return f"unknown tool {block.name}", True
        args = block.input if isinstance(block.input, dict) else {}
        if tool.risk != "read" and not self.confirm(tool, args):
            return "The user declined this action. Don't retry it; carry on without it or ask them.", True
        self.out(f"  · {block.name} {json.dumps(args, ensure_ascii=False)[:120]}")
        t = time.time()
        try:
            result, err = tool.run(args), False
        except ToolError as e:
            result, err = str(e), True
        except Exception as e:  # noqa: BLE001 - a crashed tool is reported to Claude, not fatal
            result, err = f"{type(e).__name__}: {e}", True
        if self.verbose:
            self.out(f"    ({time.time() - t:.1f}s) {result[:400]}")
        return result, err

    def ask(self, text):
        import anthropic
        self.messages.append({"role": "user", "content": text})
        for _ in range(MAX_STEPS):
            try:
                r = self.client.beta.messages.create(
                    model=MODEL, max_tokens=16000, system=SYSTEM,
                    tools=[t.schema() for t in self.tools.values()],
                    messages=self.messages, cache_control={"type": "ephemeral"},
                    output_config={"effort": EFFORT},
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except anthropic.AuthenticationError:
                return "Claude API key not accepted: set ANTHROPIC_API_KEY (in your shell or Capstone1/.env)."
            except anthropic.APIConnectionError as e:
                return f"Can't reach the Claude API: {e}"
            except anthropic.APIStatusError as e:
                return f"Claude API error {e.status_code}: {e.message}"
            self.messages.append({"role": "assistant", "content": r.content})
            if r.stop_reason == "refusal":
                return "(Claude declined this request.)"
            if r.stop_reason == "pause_turn":
                continue
            uses = [b for b in r.content if b.type == "tool_use"]
            if r.stop_reason != "tool_use" or not uses:
                text = "\n".join(b.text for b in r.content if b.type == "text").strip()
                return text + ("\n\n(answer cut off: max_tokens)" if r.stop_reason == "max_tokens" else "")
            results = []
            for b in uses:
                content, err = self.call_tool(b)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": content, "is_error": err})
            self.messages.append({"role": "user", "content": results})
        return f"(stopped after {MAX_STEPS} steps)"


def chat(agent):
    print(f"LP orchestrator · {MODEL} · {len(agent.tools)} tools · ketik 'exit' buat keluar")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if q.lower() in ("exit", "quit", "keluar"):
            return
        if q:
            print("\n" + agent.ask(q))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m orchestrator", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="run write tools without asking")
    ap.add_argument("-v", "--verbose", action="store_true", help="print each tool's output too")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("ask")
    s.add_argument("question", nargs="+")
    s = sub.add_parser("brief")
    s.add_argument("--out", help="also save the brief to this file")
    sub.add_parser("tools")
    sub.add_parser("chat")
    args = ap.parse_args(argv)

    if args.cmd == "tools":
        for t in all_tools():
            print(f"{t.name:<14} {t.risk:<6} {t.description.split('. ')[0]}")
        return
    load_env()
    try:
        agent = Agent(auto_yes=args.yes, verbose=args.verbose)
    except ImportError:
        sys.exit("Install the Claude SDK first:  python3 -m pip install -r orchestrator/requirements.txt")
    if args.cmd == "ask":
        print(agent.ask(" ".join(args.question)))
    elif args.cmd == "brief":
        text = agent.ask(BRIEF)
        print(text)
        if args.out:
            Path(args.out).write_text(text + "\n")
    else:
        chat(agent)
