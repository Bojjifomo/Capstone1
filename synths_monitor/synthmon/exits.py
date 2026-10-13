"""Exit type of a sell (take profit / stop loss / timer / AI), read from the bot's posts."""

from __future__ import annotations

import re
from typing import Any

from .models import Post, Trade

EXIT_TYPES = ("take_profit", "stop_loss", "timer", "ai")

# Checked in EXIT_TYPES order; the first type with a matching pattern wins.
DEFAULT_PATTERNS: dict[str, list[str]] = {
    "take_profit": [
        r"take[\s_-]?profits?", r"took\s+(some\s+)?profits?", r"taking\s+(some\s+)?profits?", r"\btp\b",
        r"profit\s+target", r"target\s+(hit|reached)", r"(locked|locking|secured?|securing)\s+(in\s+)?(the\s+)?(profits?|gains?)",
    ],
    "stop_loss": [
        r"stop[\s_-]?loss", r"stopped\s+out", r"\bsl\b", r"cut(ting)?\s+(my\s+|the\s+)?loss(es)?", r"loss\s+limit",
        r"stop\s+(hit|triggered)",
    ],
    "timer": [
        r"\btimer\b", r"time[\s_-]?(limit|out|stop|based|exit)", r"timed\s+out", r"max(imum)?\s+hold",
        r"hold\s+(time|period|window|limit)", r"\bexpired?\b", r"time'?s\s+up", r"held\s+(too\s+)?long",
    ],
    "ai": [
        r"\bai\b", r"\bllm\b", r"\bmodel\b", r"my\s+(analysis|read|judg(e)?ment|thesis)", r"\bdiscretion",
        r"sentiment", r"\bvibes?\b", r"decided\s+to\s+(sell|exit|dump|close)", r"\bconviction\b", r"\bthesis\b",
    ],
}

SELL_WORDS = re.compile(r"\b(sell|sold|selling|exit(ed|ing)?|closed?|closing|dump(ed)?|out of)\b", re.I)


class ExitClassifier:
    def __init__(self, extra: dict[str, list[str]] | None = None):
        extra = extra or {}
        self.patterns = {
            t: [re.compile(p, re.I) for p in list(extra.get(t) or []) + DEFAULT_PATTERNS[t]] for t in EXIT_TYPES
        }

    def classify(self, text: str | None) -> str | None:
        if not text:
            return None
        normalized = text.replace("_", " ")
        for exit_type in EXIT_TYPES:
            if any(p.search(text) or p.search(normalized) for p in self.patterns[exit_type]):
                return exit_type
        return None


def _mentions(post: Post, trade: Trade) -> bool:
    if post.tx_hash and trade.tx_hash:
        return post.tx_hash == trade.tx_hash
    if post.coin:
        return post.coin == trade.coin
    text = post.text.lower()
    if trade.coin in text:
        return True
    if trade.symbol:
        sym = re.escape(trade.symbol.lower().lstrip("$"))
        return bool(re.search(rf"(\${sym}\b|\b{sym}\b)", text))
    return False


def assign_exit_types(
    trades: list[Trade], posts: list[Post], classifier: ExitClassifier, before_s: int = 900, after_s: int = 1800
) -> dict[str, tuple[str, str | None]]:
    """Map sell trade id -> (exit_type, post_id).

    A reason field on the trade itself wins. Otherwise the post closest in
    time that names the same coin (or tx) and reads as an exit is used.
    Sells with nothing to go on are "unknown".
    """
    out: dict[str, tuple[str, str | None]] = {}
    exit_posts = [(p, classifier.classify(p.text)) for p in posts]
    exit_posts = [(p, t) for p, t in exit_posts if t is not None]
    used: set[str] = set()
    for trade in sorted((t for t in trades if t.side == "sell"), key=lambda t: t.ts):
        reason_type = classifier.classify(trade.reason)
        if reason_type:
            out[trade.id] = (reason_type, None)
            continue
        best: tuple[float, Post, str] | None = None
        for post, exit_type in exit_posts:
            if post.id in used or not (trade.ts - before_s <= post.ts <= trade.ts + after_s):
                continue
            if not _mentions(post, trade):
                continue
            exact = bool(post.tx_hash and post.tx_hash == trade.tx_hash)
            if not exact and not SELL_WORDS.search(post.text) and exit_type == "ai":
                continue  # "AI" words alone are too common in an agent's posts to imply an exit
            score = -1.0 if exact else abs(post.ts - trade.ts)
            if best is None or score < best[0]:
                best = (score, post, exit_type)
        if best:
            used.add(best[1].id)
            out[trade.id] = (best[2], best[1].id)
        else:
            out[trade.id] = ("unknown", None)
    return out


def classifier_from_config(cfg: dict[str, Any]) -> ExitClassifier:
    return ExitClassifier({t: cfg["exits"].get(t) or [] for t in EXIT_TYPES})
