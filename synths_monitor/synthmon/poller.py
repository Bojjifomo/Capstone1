"""One poll = refresh every dataset, synths.lol API first and the chain as fallback."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import alerts, owncoin
from .config import rpc_url
from .db import DB
from .exits import ExitClassifier, assign_exit_types, classifier_from_config
from .http import Backoff, Http, RateLimited, SourceError
from .models import Bag, CoinStats, PollStatus
from .roundtrips import build_round_trips, store_round_trips
from .sources.chain import ExplorerChain, FallbackChain, RpcChain, trades_from_activity
from .sources.synths_api import SynthsClient
from .util import now

log = logging.getLogger("synthmon")


@dataclass
class Context:
    cfg: dict[str, Any]
    db: DB
    synths: SynthsClient
    chain: FallbackChain
    notifier: alerts.Notifier
    classifier: ExitClassifier
    backoffs: dict[str, Backoff] = field(default_factory=dict)

    def backoff(self, name: str) -> Backoff:
        if name not in self.backoffs:
            p = self.cfg["poll"]
            self.backoffs[name] = Backoff(base=p["interval_seconds"], max_delay=p["max_backoff_seconds"])
        return self.backoffs[name]


def build_context(cfg: dict[str, Any], db: DB) -> Context:
    http = Http(timeout=cfg["poll"]["timeout_seconds"])
    backends = []
    c = cfg["chain"]
    if c["explorer_api_url"]:
        backends.append(ExplorerChain(c["explorer_api_url"], http, c["explorer_api_key"], c["token_decimals"]))
    url = rpc_url(cfg)
    if url:
        backends.append(RpcChain(url, http, c["token_decimals"]))
    return Context(
        cfg=cfg, db=db, synths=SynthsClient(cfg, http, db.save_raw), chain=FallbackChain(backends),
        notifier=alerts.Notifier(cfg), classifier=classifier_from_config(cfg),
    )


def _attempt(ctx: Context, st: PollStatus, name: str, fn: Callable[[], Any]) -> tuple[bool, Any]:
    """Run one source call under its backoff. Returns (ok, result)."""
    b = ctx.backoff(name)
    if not b.ready():
        return False, None
    try:
        result = fn()
    except RateLimited as exc:
        wait = b.failed(exc.retry_after)
        st.error(f"{name}: rate limited, retry in {wait:.0f}s")
        return False, None
    except SourceError as exc:
        wait = b.failed()
        st.error(f"{name}: {exc} (retry in {wait:.0f}s)")
        return False, None
    b.succeeded()
    return True, result


def _synths(ctx: Context, st: PollStatus, kind: str, fn: Callable[[], Any]) -> tuple[bool, Any]:
    if not ctx.synths.configured(kind):
        return False, None
    return _attempt(ctx, st, f"synths:{kind}", fn)


def resolve_own_coin(ctx: Context) -> str | None:
    if ctx.cfg["bot"]["own_coin"]:
        coin = ctx.cfg["bot"]["own_coin"]
    else:
        row = ctx.db.query("SELECT coin FROM launched ORDER BY COALESCE(created_ts, 0) DESC LIMIT 1")
        coin = row[0]["coin"] if row else None
    if coin:
        ctx.db.set_state("own_coin", coin)
    return coin


def poll_once(ctx: Context) -> PollStatus:
    cfg, db, st = ctx.cfg, ctx.db, PollStatus()
    ts = now()
    wallet = cfg["bot"]["wallet"]
    latest_block: int | None = None

    def chain_block() -> int | None:
        nonlocal latest_block
        if latest_block is None and ctx.chain:
            ok, latest_block = _attempt(ctx, st, "chain:block", lambda: ctx.chain.call("latest_block"))
        return latest_block

    # Launched coins (tells us which coin is the bot's own when it isn't configured).
    ok, launched = _synths(ctx, st, "launched", ctx.synths.launched)
    if ok:
        db.upsert_launched(launched)
        st.note("launched", "synths")
    own = resolve_own_coin(ctx)

    # Trades.
    ok, trades = _synths(ctx, st, "trades", ctx.synths.trades)
    if ok:
        st.note("trades", "synths")
    elif ctx.chain and chain_block() is not None:
        start = db.get_state("wallet_block", cfg["chain"]["start_block"])

        def fetch():
            level = logging.INFO if latest_block - start > 10_000 else logging.DEBUG
            log.log(level, "reading wallet history on-chain, blocks %d..%d", start, latest_block)
            activity = ctx.chain.call("wallet_activity", wallet, start, latest_block, cfg["chain"]["weth"])
            return trades_from_activity(activity, cfg["chain"]["weth"], f"chain:{ctx.chain.last_source}")

        ok, trades = _attempt(ctx, st, "chain:trades", fetch) if start <= latest_block else (True, [])
        if ok:
            db.set_state("wallet_block", latest_block + 1)
            st.note("trades", ctx.chain.last_source or "chain")
    new_trades = sum(db.upsert_trade(t) for t in trades or [])
    got_trades = ok
    db.commit()

    # Posts (only synths.lol has them).
    ok, posts = _synths(ctx, st, "posts", ctx.synths.posts)
    if ok:
        for p in posts:
            db.upsert_post(p, ctx.classifier.classify(p.text))
        st.note("posts", "synths")

    # Bags.
    ok, bags = _synths(ctx, st, "bags", ctx.synths.bags)
    if ok:
        st.note("bags", "synths")
    elif ctx.chain:
        ok, balances = _attempt(ctx, st, "chain:bags", lambda: ctx.chain.call("token_balances", wallet))
        if ok:
            bags = []
            for coin, amount in balances.items():
                if coin in cfg["chain"]["weth"]:
                    bags.append(Bag(coin=coin, token_amount=amount, value_eth=amount, symbol="WETH", source="chain"))
                    continue
                price = db.last_price(coin)
                bags.append(Bag(coin=coin, token_amount=amount, value_eth=None if price is None else amount * price,
                                source="chain:last-trade-price"))
            st.note("bags", ctx.chain.last_source)
    if ok and bags is not None:
        db.add_bags(ts, bags)
    bags_value = sum(b.value_eth for b in bags if b.value_eth is not None) if ok and bags is not None else None

    # Wallet balance.
    balance = None
    ok, profile = _synths(ctx, st, "profile", ctx.synths.profile)
    if ok and profile.get("wallet") and profile["wallet"] != wallet:
        st.error(f"synths profile shows wallet {profile['wallet']} but bot.wallet is {wallet}; check the config")
    if ok and profile.get("eth_balance") is not None:
        balance = profile["eth_balance"]
        st.note("balance", "synths")
    elif ctx.chain:
        ok, balance = _attempt(ctx, st, "chain:balance", lambda: ctx.chain.call("eth_balance", wallet))
        if ok:
            st.note("balance", ctx.chain.last_source)
    if balance is not None:
        db.add_wallet_snapshot(ts, wallet, balance, bags_value, st.sources["balance"])

    # Own coin: holders, buys vs sells, progress to open.
    if own:
        stats: CoinStats | None = None
        ok, stats = _synths(ctx, st, "coin", lambda: ctx.synths.coin(own, cfg["coin"]["open_threshold_eth"]))
        if ok:
            st.note("own_coin", "synths")
        missing = stats is None or None in (stats.holders, stats.buys, stats.sells, stats.progress_pct)
        if missing and ctx.chain and chain_block() is not None:
            def chain_stats():
                if not owncoin.sync_transfers(db, ctx.chain, own, latest_block):
                    return None  # stats from a partial history would undercount
                return owncoin.onchain_stats(db, ctx.chain, cfg, own)

            ok, onchain = _attempt(ctx, st, "chain:own_coin", chain_stats)
            if ok and onchain is not None:
                if stats is None:
                    stats = onchain
                else:
                    for name in ("holders", "buys", "sells", "raised_eth", "progress_pct"):
                        if getattr(stats, name) is None:
                            setattr(stats, name, getattr(onchain, name))
                    stats.source += f"+{onchain.source}"
                st.note("own_coin", stats.source)
        if stats is not None:
            db.add_coin_snapshot(ts, stats)
        db.commit()

    # Leaderboard rank.
    ok, entry = _synths(ctx, st, "leaderboard", ctx.synths.leaderboard_entry)
    if ok and entry[1] is not None:
        db.add_leaderboard(ts, cfg["bot"]["handle"], entry[0], entry[1])
        st.note("leaderboard", "synths")

    # Round trips and exit types.
    ex = cfg["exits"]
    all_trades = db.trades()
    exits = assign_exit_types(all_trades, db.posts(), ctx.classifier,
                              int(ex["match_before_minutes"] * 60), int(ex["match_after_minutes"] * 60))
    store_round_trips(db, build_round_trips(all_trades, exits, cfg["roundtrips"]["dust_fraction"]))

    st.ok = got_trades and balance is not None
    db.add_poll(ts, st.ok, st.sources, st.errors)
    db.commit()

    sent = alerts.dispatch(db, ctx.notifier, alerts.evaluate(db, cfg, ts), ts)
    log.info("poll %s: %d new trades, sources=%s, alerts sent=%d%s", "ok" if st.ok else "DEGRADED", new_trades,
             st.sources, len(sent), f", errors={st.errors}" if st.errors else "")
    return st


def run_forever(ctx: Context, sleep: Callable[[float], None] = time.sleep, max_polls: int | None = None) -> None:
    p = ctx.cfg["poll"]
    loop = Backoff(base=p["interval_seconds"], max_delay=p["max_backoff_seconds"])
    polls = 0
    while max_polls is None or polls < max_polls:
        started = time.time()
        try:
            st = poll_once(ctx)
            failed = not st.ok
        except Exception:  # keep monitoring through bugs in a single poll; the traceback is logged
            log.exception("poll crashed")
            failed = True
        polls += 1
        if failed:
            wait = loop.failed()
            log.warning("backing off %.0fs (consecutive failed polls: %d)", wait, loop.failures)
        else:
            loop.succeeded()
            wait = max(0.0, p["interval_seconds"] - (time.time() - started))
        if max_polls is None or polls < max_polls:
            sleep(wait)
