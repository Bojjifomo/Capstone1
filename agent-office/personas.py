"""A fantasy persona for every agent: name, kin and calling, age, a quirk, a home town, a bio and a motto.

Deterministic from the agent's seed, so the same session is always the same character.
"""
import hashlib
import re

FIRST = ["Patch", "Bramble", "Wren", "Fennel", "Tumble", "Pip", "Mossy", "Juniper", "Rook", "Clover", "Barnaby",
         "Sprocket", "Tansy", "Hob", "Marlow", "Nettle", "Odo", "Pemberly", "Quince", "Rosalind", "Sorrel", "Tib",
         "Ulla", "Bertram", "Dandelion", "Ember", "Flick", "Gilly", "Hazel", "Inkpot", "Jasper", "Kettle"]
LAST = ["Hoodwink", "Thistledown", "Copperkettle", "Inkwhistle", "Mapleton", "Brightbarrel", "Fernsby", "Pebblefoot",
        "Gearwhistle", "Candlewick", "Tallowmere", "Quickledger", "Mossbottom", "Featherstone", "Ashgrove", "Buttonwood",
        "Cobblepot", "Dewberry", "Elderflame", "Fiddlesworth", "Goldhollow", "Hearthstone", "Lanternby", "Saltmarsh"]
KIN = ["Halfling", "Elf", "Dwarf", "Gnome", "Hare-folk", "Fox-kin", "Owlfolk", "Badger-kin", "Cat-folk", "Human",
       "Bunny", "Otter-kin", "Mouse-folk", "Hedgehog"]
CALLINGS = [
    (("lp", "defi", "wallet", "pool", "fee", "rh", "trade", "trader", "finance", "price", "token"), ["Coin-Counter", "Ledger Witch", "Treasurer", "Barista of Books"]),
    (("web", "site", "ui", "css", "frontend", "widget", "design", "landing"), ["Glasswright", "Banner Painter", "Window Mage"]),
    (("test", "bug", "fix", "review", "security", "audit", "qa"), ["Bug Hunter", "Warden", "Lockpick"]),
    (("doc", "readme", "write", "guide", "report", "notes", "changelog"), ["Scribe", "Bard of Footnotes", "Inkkeeper"]),
    (("data", "sync", "index", "db", "sql", "pipeline", "import"), ["Cartographer", "Seer", "Archivist"]),
    (("deploy", "ops", "server", "infra", "launchd", "ci", "monitor"), ["Lamplighter", "Gatekeeper", "Forge-tender"]),
    (("plan", "idea", "product", "roadmap", "manager", "lead", "innovator"), ["Quartermaster", "Dreamer", "Strategist"]),
]
CALLING_DEFAULT = ["Tinkerer", "Wandering Artificer", "Alchemist", "Errand-runner", "Hedge Wizard"]
QUIRKS = ["Fluffy-ambiguous (and proud of it)", "Allergic to unfinished tests", "Hums while debugging",
          "Counts coins in their sleep", "Never trusts a green checkmark", "Keeps snacks in every pocket",
          "Talks to the RPC like it's a pet", "Owns eleven quills, uses one", "Can smell a stale cache",
          "Naps in exactly 20-minute shifts", "Collects broken stack traces", "Believes in tabs, quietly",
          "Writes commit messages as poems", "Afraid of merge conflicts, brave anyway"]
TOWNS = [("Fee Parsington-on-Spread", "a sleepy village where every latte comes with a receipt"),
         ("Lower Ledgerby", "where the river runs in double-entry"),
         ("Gaswick Hollow", "famous for its very expensive fog"),
         ("Port Liquidity", "where the tides go in and out of range"),
         ("Old Mempool", "a town that never sleeps, only waits"),
         ("Blockhaven", "where every house is built on the one before it"),
         ("Slippage-under-Hill", "where nothing costs quite what the sign says"),
         ("Candlestick Downs", "a market town of very green and very red days"),
         ("Little Rebase", "where history is rewritten every Sunday"),
         ("Bytewater", "a fishing village that only catches off-by-ones")]
PLACES = ["the espresso machine", "the ledger shelf", "the map table", "the window seat", "the forge", "the notice board",
          "the hearth", "the archive stacks", "the tavern counter", "the scrying pool"]
VERBS = ["squeaky clean", "honest", "humming", "in good order", "ticking over", "one step ahead"]
TROUBLES = ["the books don't balance", "a test turns red", "the RPC sulks", "a number looks odd", "the build grumbles",
            "the queue backs up"]
HABITS = ["twitches their whiskers", "polishes their spectacles", "brews a fresh cup", "rolls up their sleeves",
          "hums an old tavern song", "sharpens a quill"]
FIXES = ["tracks down the missing decimal", "finds the one line that broke", "sets it right before supper",
         "leaves a note for the next shift", "writes it down so it never happens again", "checks it twice, then once more"]
MOTTOS = ["Every fee has a home, and every cent gets counted.", "Measure twice, merge once.",
          "A green test is a promise, not a proof.", "Small steps, warm tea.", "If it isn't written down, it didn't happen.",
          "Trust the chain, check the math.", "Fortune favours the well-tested.", "Leave the hall tidier than you found it.",
          "Slow is smooth, smooth is shipped.", "No quest too small, no bug too sneaky."]


def _pick(seq, h, shift):
    return seq[(h >> shift) % len(seq)]


def calling_for(text, h):
    words = set(re.findall(r"[a-z]+", str(text).lower()))
    for keys, options in CALLINGS:
        if words & set(keys):
            return _pick(options, h, 3)
    return _pick(CALLING_DEFAULT, h, 3)


def persona(seed, context="", name=None, title=None, topic=None):
    """context: words that pick the calling (role, quest); topic: what the bio says they keep in order."""
    h = int(hashlib.sha256(str(seed).encode()).hexdigest()[:15], 16)
    first = name.split()[0] if name else _pick(FIRST, h, 0)
    full = name or "%s %s" % (first, _pick(LAST, h, 7))
    kin = _pick(KIN, h, 13)
    calling = calling_for(context, h)
    town, town_desc = _pick(TOWNS, h, 19)
    bio = "%s hops between %s and %s, keeping %s %s. When %s, %s just %s and %s." % (
        first, _pick(PLACES, h, 23), _pick(PLACES, h, 29), short_context(topic if topic is not None else context), _pick(VERBS, h, 31),
        _pick(TROUBLES, h, 37), first, _pick(HABITS, h, 41), _pick(FIXES, h, 43))
    return {
        "name": full,
        "title": title or "%s %s" % (kin, calling),
        "age": "%d yrs" % (2 + h % 9 if kin not in ("Elf", "Dwarf") else 90 + h % 300),
        "quirk": _pick(QUIRKS, h, 47),
        "home": "From %s, %s" % (town, town_desc),
        "bio": bio,
        "motto": _pick(MOTTOS, h, 53),
    }


def short_context(text):
    text = " ".join(str(text or "").split())
    text = re.sub(r"^(the|a|an)\s+", "", text, flags=re.I)
    if not text:
        return "the guild's quests"
    return "the " + (text if len(text) <= 48 else text[:45].rsplit(" ", 1)[0] + "…")
