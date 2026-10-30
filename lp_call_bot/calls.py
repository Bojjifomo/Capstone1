"""Parse pesan owner (format kayak di grup WA) dan bikin teks buat follower.

Format yang dikenali (huruf besar/kecil bebas):

    0x<40 hex> <nama koin> <fee>% [range]   -> open LP   (range kosong = DEFAULT_RANGE)
    close <koin> [koin ...] | close all     -> close LP
    reentry <koin> [koin ...]               -> masuk lagi, range & fee sama kayak open terakhir
                                               (alias: reenter, re-entry)
"""
import html
import re

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
FEE = re.compile(r"^(\d+(?:\.\d+)?)%$")
RANGE = re.compile(r"^-\d+(?:\.\d+)?%?$")
CLOSE_WORDS = {"close", "tutup"}
REENTRY_WORDS = {"reentry", "reenter", "re-entry", "re-enter"}


class ParseError(ValueError):
    pass


def _num(text):
    return text.rstrip("%").rstrip("0").rstrip(".") if "." in text else text.rstrip("%")


def parse(text, default_range="-65"):
    """Return dict {"action": "open"|"close"|"reentry", ...} atau None kalau bukan call."""
    tokens = text.replace(",", " ").replace("/", " ").split()
    if not tokens:
        return None
    head = tokens[0].lower()

    if ADDRESS.match(tokens[0]):
        names, fee, ranges = [], None, []
        for tok in tokens[1:]:
            if FEE.match(tok) and fee is None:
                fee = _num(tok)
            elif RANGE.match(tok):
                ranges.append(_num(tok))
            else:
                names.append(tok)
        if not names:
            raise ParseError("Nama koinnya mana? Contoh: 0x… hydx 2.5% -65")
        if fee is None:
            raise ParseError("Fee pool-nya berapa %? Contoh: 0x… hydx 2.5% -65")
        return {
            "action": "open",
            "ca": tokens[0],
            "coin": " ".join(names).lower(),
            "fee": fee,
            "range": ranges or [default_range.rstrip("%")],
        }

    if head in CLOSE_WORDS or head in REENTRY_WORDS:
        coins = [t.lower() for t in tokens[1:]]
        if not coins:
            raise ParseError(f"Koin apa? Contoh: {head} hydx")
        return {"action": "close" if head in CLOSE_WORDS else "reentry", "coins": coins}

    return None


def same_range(a, b, tolerance=1):
    """Range % dari dua open dianggap sama kalau selisihnya <= 1 (tick spacing bikin angka geser dikit)."""
    return len(a) == len(b) and all(abs(float(x) - float(y)) <= tolerance for x, y in zip(a, b))


def fmt_range(ranges):
    return " / ".join(f"{r}%" for r in ranges)


def _ca_line(ca):
    return f"CA: <code>{html.escape(ca)}</code>"


def render_open(pos):
    return "\n".join([
        f"🟢 <b>OPEN LP — {html.escape(pos['coin'].upper())}</b>",
        "",
        _ca_line(pos["ca"]),
        f"Pool: <b>{pos['fee']}%</b>",
        f"Range: <b>{fmt_range(pos['range'])}</b> (single sided USDG)",
    ])


def render_close(positions):
    if len(positions) == 1:
        pos = positions[0]
        return f"🔴 <b>CLOSE LP — {html.escape(pos['coin'].upper())}</b>\n\n{_ca_line(pos['ca'])}"
    lines = ["🔴 <b>CLOSE LP</b>", ""]
    for pos in positions:
        lines.append(f"• <b>{html.escape(pos['coin'].upper())}</b> — <code>{html.escape(pos['ca'])}</code>")
    return "\n".join(lines)


def render_reentry(positions):
    blocks = []
    for pos in positions:
        blocks.append("\n".join([
            f"🔁 <b>REENTRY — {html.escape(pos['coin'].upper())}</b> (range sama)",
            "",
            _ca_line(pos["ca"]),
            f"Pool: <b>{pos['fee']}%</b>",
            f"Range: <b>{fmt_range(pos['range'])}</b> (single sided USDG)",
        ]))
    return "\n\n".join(blocks)


def render_list(positions):
    opened = [p for p in positions if p["status"] == "open"]
    if not opened:
        return "Lagi nggak ada LP yang kebuka."
    lines = [f"📋 <b>LP kebuka ({len(opened)})</b>", ""]
    for pos in opened:
        lines.append(
            f"• <b>{html.escape(pos['coin'].upper())}</b> {pos['fee']}% {fmt_range(pos['range'])}\n"
            f"  <code>{html.escape(pos['ca'])}</code>"
        )
    return "\n".join(lines)
