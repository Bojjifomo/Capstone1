"""Guild gold. A daily Claude budget maps onto a purse; spend from the transcripts drains it.

`daily_budget_usd` 0 (the default) leaves the purse full and does not pause anyone,
so a hall with no budget set behaves as before. Set a budget and gold runs out
when today's estimated cost reaches it. New quests then wait in gold.json until
the next day, until you let one through, or until you tick "open the strongbox"
on the Mac. A token caller cannot force that.
"""
import secrets
import threading
import time

import panels
import store


def figures(spent, budget, purse):
    """Return gold left and whether new quests should wait."""
    purse = max(0.0, float(purse or 0))
    budget = float(budget or 0)
    spent = max(0.0, float(spent or 0))
    if budget <= 0:
        return purse, False, False
    gold = round(purse * max(0.0, 1 - spent / budget), 1)
    return gold, gold <= 0, True


class Economy:
    def __init__(self, office=None):
        self.office = office
        saved = store.load("gold.json", {})
        if not isinstance(saved, dict):
            saved = {}
        self.queue = saved.get("queue") if isinstance(saved.get("queue"), list) else []
        self.lock = threading.Lock()

    def save(self):
        store.save("gold.json", {"queue": self.queue[-40:]})

    def spent_today(self):
        if self.office is None or not getattr(self.office, "scanner", None):
            return 0.0
        return panels.cost_today(self.office)

    def _budget(self):
        cfg = getattr(self.office, "cfg", None) or {}
        try:
            budget = float(cfg.get("daily_budget_usd") or 0)
        except (TypeError, ValueError):
            budget = 0.0
        try:
            purse = float(cfg.get("daily_gold") or 100)
        except (TypeError, ValueError):
            purse = 100.0
        return budget, purse

    def snapshot(self):
        budget, purse = self._budget()
        spent = self.spent_today()
        gold, paused, metered = figures(spent, budget, purse)
        with self.lock:
            queue = [{"id": i.get("id"), "kind": i.get("kind"), "label": i.get("label"), "ts": i.get("ts")}
                     for i in self.queue]
        return {"gold": gold, "max": purse, "spent": round(spent, 4), "budget": budget,
                "metered": metered, "paused": paused, "queue": queue}

    def hold(self, kind, label, payload):
        item = {"id": secrets.token_hex(4), "kind": kind, "label": " ".join(str(label).split())[:120],
                "payload": payload, "ts": time.time()}
        with self.lock:
            self.queue.append(item)
            self.queue = self.queue[-40:]
            self.save()
        return item

    def consider(self, external, override, kind, label, payload):
        """None means go ahead. blocked means a token caller must stop. Otherwise the quest waits."""
        if not self.snapshot()["paused"]:
            return None
        if override and not external:
            ledger = getattr(self.office, "ledger", None) if self.office is not None else None
            if ledger:
                ledger.add("guild", "You opened the strongbox: " + " ".join(str(label).split())[:120])
            return None
        if external:
            return {"blocked": True}
        item = self.hold(kind, label, payload)
        return {"queued_for_gold": True, "id": item["id"],
                "reason": "The treasury is empty. This quest waits until gold returns, or until you let it through."}

    def take(self, qid):
        with self.lock:
            for i, item in enumerate(self.queue):
                if item.get("id") == qid:
                    self.queue.pop(i)
                    self.save()
                    return item
        return None

    def release(self, qid):
        item = self.take(qid)
        if not item:
            raise LookupError("that quest is no longer waiting")
        try:
            return self.office.start_queued(item)
        except Exception:
            with self.lock:
                self.queue.insert(0, item)
                self.save()
            raise

    def pump(self):
        """When gold is back, start the oldest waiting quest. One per pass."""
        if self.office is None or self.snapshot()["paused"]:
            return None
        with self.lock:
            if not self.queue:
                return None
            item = self.queue.pop(0)
            self.save()
        try:
            return self.office.start_queued(item)
        except Exception:
            with self.lock:
                self.queue.insert(0, item)
                self.save()
            raise
