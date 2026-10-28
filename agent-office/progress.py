"""XP, ranks, and badges for the adventurers.

Ranks replace the leading title word the town already shows:

    Apprentice (0) → Journeyman (40) → Adept (120) → Master (300)

Level is 1 + XP // 25, so it moves between ranks. Quest rarity comes from a
petition's impact and effort and scales the XP.

    Common 10 · Rare 25 · Epic 60 · Legendary 120

A completed quest grants that XP (Common when it was not a petition). Approving
a petition grants half, rounded, at least 5, to whoever filed it. A merged PR
grants the full amount again to whoever built it.
"""
import threading
import time

import store

RANKS = (("Apprentice", 0), ("Journeyman", 40), ("Adept", 120), ("Master", 300))
RANK_NAMES = tuple(name for name, _ in RANKS)
XP = {"Common": 10, "Rare": 25, "Epic": 60, "Legendary": 120}
BADGES = {
    "first-quest": "First Quest",
    "ten-quests": "10 Quests",
    "first-pr": "First PR Merged",
    "zero-cost": "Zero-Cost Day",
    "petition-sealed": "Petition Sealed",
    "legendary": "Legendary Quest",
}


def rarity_of(impact, effort):
    impact = str(impact or "").lower()
    effort = str(effort or "").upper()
    if impact == "high" and effort == "L":
        return "Legendary"
    if (impact == "high" and effort == "M") or (impact == "medium" and effort == "L"):
        return "Epic"
    if impact == "high" or effort == "M" or impact == "medium":
        return "Rare"
    return "Common"


def xp_for(rarity, kind="quest"):
    base = XP.get(rarity, XP["Common"])
    if kind == "approve":
        return max(5, base // 2)
    return base


def band(xp):
    """Rank, progress into the current band, band width, next rank, XP where the next rank starts."""
    xp = max(0, int(xp or 0))
    rank, floor = RANKS[0]
    nxt, nxt_at = (RANKS[1] if len(RANKS) > 1 else ("", xp))
    for i, (name, need) in enumerate(RANKS):
        if xp >= need:
            rank, floor = name, need
            if i + 1 < len(RANKS):
                nxt, nxt_at = RANKS[i + 1]
            else:
                nxt, nxt_at = "", need
    span = max(1, (nxt_at - floor) if nxt else 1)
    into = min(span, xp - floor) if nxt else span
    return rank, into, span, nxt, (nxt_at if nxt else None)


def titled(title, rank):
    """The rank drives the title already shown: 'Journeyman Artificer'."""
    title = " ".join(str(title or "").split())
    parts = title.split()
    if parts and parts[0] in RANK_NAMES:
        title = " ".join(parts[1:])
    return (str(rank or "Apprentice") + " " + title).strip()


def _speed(seconds):
    if seconds <= 60:
        return 100
    if seconds >= 1800:
        return 10
    return int(round(100 - 90 * (seconds - 60) / (1800 - 60)))


class Progress:
    def __init__(self, office=None):
        self.office = office
        saved = store.load("progress.json", {})
        if not isinstance(saved, dict):
            saved = {}
        self.actors = saved.get("actors") if isinstance(saved.get("actors"), dict) else {}
        self.recent = saved.get("recent") if isinstance(saved.get("recent"), list) else []
        self.lock = threading.Lock()

    def save(self):
        store.save("progress.json", {"actors": self.actors, "recent": self.recent[-30:]})

    def actor_for_cwd(self, cwd, sid):
        member = None
        if cwd and self.office is not None and getattr(self.office, "team", None):
            member = self.office.team.member_for_cwd(cwd)
        if member:
            return member["id"], member["name"]
        name = ""
        if self.office is not None:
            name = (self.office.names or {}).get(sid) or ""
            if not name and getattr(self.office, "scanner", None):
                t = self.office.scanner.transcripts.get(sid)
                name = (t.title if t else "") or ""
        return sid or "unknown", name or "Adventurer"

    def actor_named(self, name):
        name = " ".join(str(name or "").split())[:40] or "someone"
        member = None
        if self.office is not None and getattr(self.office, "team", None):
            member = self.office.team.find(name)
        if member:
            return member["id"], member["name"]
        return "by:" + name.casefold(), name

    def on_quest(self, run, petition=None):
        key, name = self.actor_for_cwd(getattr(run, "cwd", ""), getattr(run, "sid", ""))
        rarity = rarity_of(petition.get("impact"), petition.get("effort")) if petition else "Common"
        title = (petition or {}).get("title") or (getattr(run, "text", "") or "Quest")
        seconds = 0
        if getattr(run, "ended", None) and getattr(run, "started", None):
            seconds = max(0, run.ended - run.started)
        cost = getattr(run, "cost", None)
        return self.grant(key, name, xp_for(rarity) if run.ok else 0, "quest", title, rarity,
                          ok=bool(run.ok), cost=cost, seconds=seconds)

    def on_decision(self, item, decision):
        if decision != "approve" or not item:
            return []
        key, name = self.actor_named(item.get("by"))
        rarity = rarity_of(item.get("impact"), item.get("effort"))
        return self.grant(key, name, xp_for(rarity, "approve"), "approve", item.get("title") or "Petition", rarity, ok=True)

    def on_merged(self, item):
        if not item:
            return []
        sid = item.get("sid") or ""
        cwd = ""
        if sid and self.office is not None and getattr(self.office, "scanner", None):
            t = self.office.scanner.transcripts.get(sid)
            cwd = t.cwd if t else ""
        key, name = self.actor_for_cwd(cwd, sid)
        rarity = rarity_of(item.get("impact"), item.get("effort"))
        return self.grant(key, name, xp_for(rarity), "merge", item.get("title") or "Pull request", rarity, ok=True)

    def on_brief(self, state):
        if not state or not state.get("ok"):
            return []
        import briefing
        seconds = 0
        if state.get("ended") and state.get("started"):
            seconds = max(0, state["ended"] - state["started"])
        return self.grant(briefing.ORCH_ID, state.get("name") or "Quill", XP["Common"], "quest",
                          "Today's brief", "Common", ok=True, cost=None, seconds=seconds)

    def grant(self, key, name, xp, kind, title, rarity="Common", ok=True, cost=None, seconds=0):
        badges, rank_up = [], None
        with self.lock:
            actor = self.actors.setdefault(key, {"name": name, "xp": 0, "history": [], "badges": [], "unlocked": {}})
            if name:
                actor["name"] = name
            before = int(actor.get("xp") or 0)
            gained = int(xp or 0) if ok else 0
            actor["xp"] = before + gained
            actor.setdefault("history", []).append({
                "ts": time.time(), "ok": bool(ok), "seconds": round(float(seconds or 0), 1),
                "cost": None if cost is None else round(float(cost), 4), "xp": gained,
                "title": " ".join(str(title or kind).split())[:80], "rarity": rarity, "kind": kind,
            })
            actor["history"] = actor["history"][-40:]
            badges = self._badges(actor, kind, rarity, ok, cost)
            new_rank = band(actor["xp"])[0]
            if gained and new_rank != band(before)[0]:
                rank_up = new_rank
            if badges:
                now = time.time()
                for badge in badges:
                    self.recent.append({"key": key, "name": actor["name"], "badge": badge,
                                        "label": BADGES[badge], "ts": now})
                self.recent = self.recent[-30:]
            self.save()
        self._announce(name, badges, rank_up)
        return badges

    def _badges(self, actor, kind, rarity, ok, cost):
        have = set(actor.get("badges") or [])
        earned = []

        def give(bid):
            if bid in have or bid not in BADGES:
                return
            have.add(bid)
            earned.append(bid)
            actor.setdefault("unlocked", {})[bid] = time.time()

        quests = [h for h in actor.get("history") or [] if h.get("kind") == "quest" and h.get("ok")]
        if ok and kind == "quest" and len(quests) >= 1:
            give("first-quest")
        if ok and kind == "quest" and len(quests) >= 10:
            give("ten-quests")
        if ok and kind == "merge":
            give("first-pr")
        if ok and kind == "approve":
            give("petition-sealed")
        if ok and kind == "quest" and rarity == "Legendary":
            give("legendary")
        if ok and kind == "quest" and cost is not None and float(cost) <= 0:
            give("zero-cost")
        actor["badges"] = [bid for bid in BADGES if bid in have]
        return earned

    def _announce(self, name, badges, rank_up):
        ledger = getattr(self.office, "ledger", None) if self.office is not None else None
        if not ledger:
            return
        if rank_up:
            ledger.add("guild", "%s reached %s" % (name, rank_up), by=name)
        for badge in badges:
            ledger.add("guild", "%s unlocked %s" % (name, BADGES[badge]), by=name)

    def public(self, key):
        actor = self.actors.get(key) or {}
        xp = int(actor.get("xp") or 0)
        rank, into, span, nxt, nxt_at = band(xp)
        hist = [h for h in (actor.get("history") or []) if h.get("kind") == "quest"]
        oks = [h for h in hist if h.get("ok")]
        success = round(100 * len(oks) / len(hist)) if hist else None
        costs = [h.get("cost") for h in oks if h.get("cost") is not None]
        efficiency = None
        if costs:
            avg = sum(costs) / len(costs)
            efficiency = 100 if avg <= 0 else max(5, min(100, int(round(100 * (0.02 / avg)))))
        speeds = [h.get("seconds") or 0 for h in oks if h.get("seconds")]
        speed = _speed(sum(speeds) / len(speeds)) if speeds else None
        return {
            "level": 1 + xp // 25, "rank": rank, "xp": xp, "into": into, "span": span,
            "next_rank": nxt, "next_at": nxt_at, "success": success, "efficiency": efficiency, "speed": speed,
            "badges": [{"id": bid, "label": BADGES[bid]} for bid in (actor.get("badges") or []) if bid in BADGES],
            "history": list(reversed((actor.get("history") or [])[-6:])),
        }

    def unlocks(self, within=600):
        now = time.time()
        return [dict(u) for u in self.recent if now - (u.get("ts") or 0) < within]

    def board(self, book):
        rows = []
        for item in book.listing():
            if item.get("status") not in ("pending", "approved", "building"):
                continue
            rarity = rarity_of(item.get("impact"), item.get("effort"))
            rows.append({
                "id": item["id"], "title": item.get("title") or "", "by": item.get("by") or "",
                "status": item["status"], "rarity": rarity, "xp": xp_for(rarity),
                "app": item.get("app") or "", "impact": item.get("impact") or "", "effort": item.get("effort") or "",
            })
        return rows

    def decorate(self, item):
        row = dict(item)
        row["rarity"] = rarity_of(item.get("impact"), item.get("effort"))
        row["xp"] = xp_for(row["rarity"])
        return row
