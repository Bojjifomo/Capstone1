"""RH LP Analyzer: paste a Robinhood Chain wallet, get its Uniswap V4 LP game
dissected for copy-trading (PnL, capital ROI, risk, profit quality, how it LPs).

Run:  streamlit run streamlit_app.py      (from this folder)
Needs an RPC (RPC_URL, optional CALL_RPC_URL) in .env, the Settings panel, or
Streamlit secrets. Wallets are read from RH Wallet Finder's database when it is
next door, else the explorer / chain, and cached in data/cache.db.
"""
import base64
import inspect
import json
import os
import re
import sys
import time
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import analysis as A  # noqa: E402
import chain as C  # noqa: E402
import config  # noqa: E402
import fetch as F  # noqa: E402
import finder_db  # noqa: E402

LOGO = HERE / "assets" / "logo.svg"
st.set_page_config(page_title="RH LP Analyzer", page_icon=str(LOGO), layout="centered",
                   initial_sidebar_state="collapsed")

ST_VER = tuple(int(x) for x in re.findall(r"\d+", st.__version__)[:2])
if ST_VER < (1, 37):
    st.error(f"Streamlit {st.__version__} is too old for this app. In Terminal run:\n\n"
             "`python3 -m pip install -U -r requirements.txt`\n\nthen start it again.")
    st.stop()

# st.secrets (Streamlit Cloud, or .streamlit/secrets.toml) feed the same settings as .env
if any(p.exists() for p in (Path.home() / ".streamlit" / "secrets.toml", HERE / ".streamlit" / "secrets.toml")) \
        or Path("/mount/src").exists():
    try:
        for _k in config.DEFAULTS:
            if _k in st.secrets and not os.environ.get(_k):
                os.environ[_k] = str(st.secrets[_k])
    except Exception:  # noqa: BLE001 - no or broken secrets: .env still works
        pass

GREEN, RED, CYAN, YELLOW, ORANGE, PURPLE = "#00e08a", "#ff4d6d", "#22d3ee", "#f5b301", "#ff8a3d", "#8b5cf6"
TIER_COLOR = {"ELITE": GREEN, "SOLID": "#3b82f6", "MIXED": YELLOW, "WEAK": ORANGE, "REJECT": RED}
KIND_COLOR = {"multiday": CYAN, "hybrid": PURPLE, "degen": YELLOW}
KIND_LABEL = {"multiday": "Multiday", "hybrid": "Hybrid", "degen": "Degen"}
FOLLOW = {"✓": "✓ yes", "✗": "✗ bot", "?": "? maybe"}
MODES = ("Single wallet", "Compare wallets")
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")
SIDES = (("bid", "ETH/USDG Only"), ("both", "Dual Sided"), ("ask", "Token Only"))

# small line icons (24px viewBox), drawn in the section headers and cards
ICONS = {
    "wallet": '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h14v4m0 4v4H5a2 2 0 0 1-2-2V5"/><path d="M16 12h5v4h-5a2 2 0 0 1 0-4z"/>',
    "trend": '<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "bars": '<path d="M5 20V10M12 20V4M19 20v-7"/>',
    "pulse": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "star": '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9z"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/>'
               '<circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "pie": '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
    "compass": '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h1M4 12h1M4 18h1"/>',
    "pool": '<path d="M3 15c2 0 2-2 4.5-2S10 15 12 15s2.5-2 4.5-2S19 15 21 15"/><path d="M3 19c2 0 2-2 4.5-2S10 19 12 '
            '19s2.5-2 4.5-2S19 19 21 19"/><path d="M12 3l4 6H8z"/>',
    "split": '<path d="M12 3v7M12 10l-6 6M12 10l6 6"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
}
TONE_ICON = {"good": ("trend", GREEN), "info": ("bars", CYAN), "bad": ("alert", YELLOW)}
ICONS["alert"] = '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h0"/>'


def icon(name, color="currentColor", size=18):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')


# ------------------------------------------------------------- versions ---
def _accepts(fn, name):
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def stretch(fn):
    """Full width on this Streamlit: width="stretch" on new versions, use_container_width on older."""
    if ST_VER >= (1, 50) and _accepts(fn, "width"):
        return {"width": "stretch"}
    if _accepts(fn, "use_container_width"):
        return {"use_container_width": True}
    return {}


def auto_height(n_rows, cap):
    if n_rows > 16:
        return cap
    return "auto" if ST_VER >= (1, 50) else 38 + 35 * max(1, n_rows)


# ---------------------------------------------------------------- look ----
def css():
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');
:root {{ --g:{GREEN}; --r:{RED}; --c:{CYAN}; --y:{YELLOW}; --p:{PURPLE}; --ink:#e6edf3; --ink2:#b6c2d0; --muted:#8392a6;
        --panel:#111821; --panel2:#151e29; --line:#1f2a37; --line2:#2b394a; }}
.stApp {{ background:
  linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px) 0 0/40px 40px,
  linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px) 0 0/40px 40px,
  radial-gradient(900px 420px at 0% -5%, rgba(0,224,138,.08), transparent 60%), #0b1016; font-family: Inter, sans-serif; }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ max-width: 1000px; padding-top: 1.6rem; padding-bottom: 4rem; }}
.brand {{ display:flex; align-items:center; gap:14px; }}
.brand img {{ width:52px; height:52px; filter: drop-shadow(0 0 14px rgba(0,224,138,.25)); }}
.word {{ font: 800 28px/1 Inter, sans-serif; letter-spacing: .04em; color: var(--ink); }}
.word b {{ color: var(--g); }}
.tag {{ font: 600 10.5px "JetBrains Mono", monospace; color: var(--muted); letter-spacing: .3em; margin-top: 7px; text-transform: uppercase; }}
.lead {{ color: var(--muted); font-size: 14px; margin: 10px 0 6px; }}
.setup {{ display:flex; gap:8px; margin: 2px 0 2px; flex-wrap: wrap; }}
.setup span {{ font: 700 10.5px "JetBrains Mono", monospace; letter-spacing:.14em; padding: 5px 10px; border-radius: 4px;
       background: rgba(255,255,255,.04); color: var(--muted); text-transform: uppercase; }}
.setup span.on {{ background: rgba(0,224,138,.12); color: var(--g); }}
.empty {{ text-align:center; padding: 46px 30px; margin-top: 18px; border: 1px solid rgba(0,224,138,.35); border-radius: 12px;
       background: linear-gradient(160deg, var(--panel2), var(--panel)); box-shadow: 0 0 40px rgba(0,224,138,.08); }}
.empty img {{ width: 64px; margin-bottom: 10px; }}
.empty b {{ display:block; font: 700 21px Inter, sans-serif; color: var(--ink); margin-bottom: 8px; }}
.empty span {{ font-size: 14px; color: var(--muted); line-height: 1.6; }} .empty i {{ color: var(--g); font-style: normal; font-weight: 600; }}
.card {{ border: 1px solid var(--line); border-top: 2px solid var(--g); background: linear-gradient(160deg, var(--panel2), var(--panel));
       padding: 18px 22px 16px; margin: 10px 0 10px; border-radius: 12px; }}
.title {{ display:flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap;
       font: 700 23px Inter, sans-serif; color: var(--ink); }}
.tags {{ display:flex; gap:6px; }}
.pill {{ font: 700 10.5px "JetBrains Mono", monospace; letter-spacing:.12em; padding: 4px 9px; border-radius: 4px; color: #06121a;
       text-transform: uppercase; display:inline-block; }}
.chips {{ display:flex; gap:6px; flex-wrap: wrap; margin-top: 12px; }}
.chips span, .chips a {{ border: 1px solid var(--line2); background: rgba(255,255,255,.03); padding: 3px 9px;
       font: 600 10.5px "JetBrains Mono", monospace; color: var(--muted); text-transform: uppercase; letter-spacing: .1em;
       border-radius: 4px; text-decoration: none; }}
.chips span.addr {{ color: var(--g); border-color: rgba(0,224,138,.4); background: rgba(0,224,138,.08); }}
.mcards {{ display:grid; gap:12px; margin: 10px 0 8px; }}
.m {{ position:relative; overflow:hidden; padding: 16px 16px 14px; background: linear-gradient(160deg, var(--panel2), var(--panel));
       border: 1px solid var(--line); border-top: 2px solid var(--line2); border-radius: 10px; min-height: 98px; }}
.m.pos {{ border-top-color: var(--g); }} .m.neg {{ border-top-color: var(--r); }} .m.neu {{ border-top-color: #cfd8e3; }}
.m .k {{ font: 600 11px Inter, sans-serif; letter-spacing:.14em; text-transform:uppercase; color: var(--ink2); }}
.m .v {{ font: 700 22px/1.25 "JetBrains Mono", monospace; color: var(--ink); margin-top: 8px; word-break: break-word; }}
.m.pos .v {{ color: var(--g); }} .m.neg .v {{ color: var(--r); }}
.m .s {{ font-size: 12px; color: var(--muted); margin-top: 4px; line-height: 1.4; }}
.m.big {{ min-height: 140px; padding: 20px 20px 18px; }}
.m.big .v {{ font-size: 30px; }}
.m.big::after {{ content:""; position:absolute; right:-36px; top:-36px; width:120px; height:120px; border-radius:50%;
       background: radial-gradient(circle, var(--glow, rgba(255,255,255,.08)), transparent 70%); }}
.m .ic {{ margin-bottom: 10px; }}
.qual {{ border: 1px solid var(--line); border-radius: 12px; background: linear-gradient(160deg, var(--panel2), var(--panel));
       padding: 16px 20px; margin: 12px 0 10px; }}
.qual small {{ font: 600 11px Inter, sans-serif; letter-spacing:.14em; text-transform:uppercase; color: var(--muted); margin-right: 8px; }}
.qual p {{ font-size: 14px; color: var(--ink2); margin: 8px 0 12px; }}
.qual hr {{ border: 0; border-top: 1px solid var(--line); margin: 12px 0; }}
.qual .why {{ font-size: 12.5px; color: var(--muted); margin: -6px 0 10px; }}
.flag {{ display:flex; gap:8px; align-items: baseline; font-size: 13.5px; color: var(--ink2); margin: 6px 0; }}
.flag svg {{ flex: none; position: relative; top: 2px; }}
.flag b {{ color: var(--ink); }}
.sec {{ display:flex; gap: 12px; margin: 34px 0 10px; }}
.sec .bar {{ width: 3px; border-radius: 2px; background: linear-gradient(var(--g), var(--p)); }}
.sec b {{ display:flex; align-items:center; gap: 10px; font: 700 20px Inter, sans-serif; color: var(--ink); }}
.sec span {{ display:block; font-size: 13px; color: var(--muted); margin-top: 3px; }}
.guide p {{ margin: 0 0 6px; font-size: 13px; color: var(--ink2); }}
.guide p b {{ color: var(--ink); }}
.guide h5 {{ font: 700 14px Inter, sans-serif; color: var(--ink); margin: 14px 0 8px; }}
.legend {{ font-size: 12px; color: var(--muted); line-height: 1.7; }}
.play {{ border: 1px solid var(--line); border-radius: 12px; background: var(--panel); padding: 16px 18px; margin: 6px 0 14px; }}
.play b {{ font: 700 18px Inter, sans-serif; color: var(--ink); }}
.play p {{ margin: 6px 0 0; color: var(--ink2); font-size: 14px; }}
.chart-t {{ text-align:center; font: 700 12px Inter, sans-serif; letter-spacing:.06em; color: var(--ink); margin: 14px 0 6px; }}
.warn {{ border-radius: 10px; padding: 12px 16px; background: rgba(245,179,1,.12); border: 1px solid rgba(245,179,1,.35);
       color: var(--ink); font-size: 14px; margin: 8px 0; }}
[data-testid="stExpander"] details {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }}
.rcard {{ border: 1px solid var(--line); border-radius: 12px; background: linear-gradient(160deg, var(--panel2), var(--panel));
       padding: 14px 16px; margin-bottom: 12px; }}
.rhead {{ display:flex; justify-content: space-between; align-items:center; gap: 8px; }}
.rhead b {{ font: 700 15px "JetBrains Mono", monospace; color: var(--ink); }}
.rhead span {{ display:flex; gap: 5px; }}
.rplay {{ font-size: 12.5px; color: var(--muted); margin: 3px 0 10px; }}
.rgrid {{ display:grid; grid-template-columns: 1fr 1fr; gap: 6px 14px; margin-bottom: 8px; }}
.rgrid small {{ display:block; font: 600 10px Inter, sans-serif; letter-spacing:.12em; text-transform: uppercase; color: var(--muted); }}
.rgrid span {{ font: 700 14px "JetBrains Mono", monospace; color: var(--ink); }}
.rgrid span.pos {{ color: var(--g); }} .rgrid span.neg {{ color: var(--r); }}
@media (max-width: 760px) {{ .mcards {{ grid-template-columns: 1fr 1fr !important; }} }}
</style>""", unsafe_allow_html=True)


# --------------------------------------------------------------- format ---
def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def compact(v, d=1):
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.{d}f}B"
    if a >= 1e6:
        return f"{v / 1e6:.{d}f}M"
    if a >= 1e4:
        return f"{v / 1e3:.{d}f}K"
    if a >= 100:
        return f"{v:,.0f}"
    return f"{v:.{1 if a >= 10 else 2}f}"


def usd(v):
    v = num(v)
    if v is None:
        return "–"
    return "$0" if abs(v) < 0.005 else ("-$" if v < 0 else "$") + compact(abs(v))


def money(v, sign=False):
    """$ with a + on gains (sign=True), – when unknown."""
    v = num(v)
    if v is None:
        return "–"
    return ("+" if sign and v > 0.005 else "") + usd(v)


def pct(v, d=0):
    v = num(v)
    if v is None:
        return "–"
    return f"{0 if abs(v) < 0.5 / 10 ** d else v:.{d}f}%"


def dur(s):
    s = num(s)
    if s is None:
        return "–"
    s = max(0, s)
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s / 60:.0f}m"
    if s < 86400:
        return f"{s / 3600:.0f}h"
    return f"{s / 86400:.{1 if s < 864000 else 0}f}d"


def range_fmt(v):
    v = num(v)
    if v is None:
        return "–"
    return "full" if v >= 1e6 else (compact(v, 0) + "%" if v >= 1e4 else f"{v:.0f}%")


def pf_fmt(v):
    v = num(v)
    return "–" if v is None else "∞" if v >= 99 else f"{v:.2f}" if v < 10 else f"{v:.0f}"


def short(a):
    return f"{a[:6]}…{a[-4:]}"


def esc(text):
    """Markdown text with $ kept as $ (two of them would start a formula)."""
    return text.replace("$", "\\$")


def parse_addrs(text):
    """Every 0x address in pasted text, lowercased, in order, once."""
    return list(dict.fromkeys(a.lower() for a in ADDR_RE.findall(text or "")))


def parse_usd(text):
    """'100K', '1.5m', '$250,000' -> number, or None."""
    t = (text or "").strip().lower().replace("$", "").replace(",", "")
    if not t:
        return None
    m = re.fullmatch(r"([\d.]+)\s*([kmb]?)", t)
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6, "b": 1e9}[m.group(2)]


def tone(v, good_from=0.0):
    v = num(v)
    return "" if v is None else "pos" if v > good_from else "neg" if v < good_from else ""


# --------------------------------------------------------------- pieces ---
def pill(text, color):
    return f'<span class="pill" style="background:{color}">{text}</span>'


def cards(items, cols, big=False):
    """Metric cards: (label, value, small print, tone 'pos'|'neg'|'neu'|'', icon name or None)."""
    html = "".join(
        f'<div class="m {t}{" big" if big else ""}" style="--glow:{"rgba(0,224,138,.18)" if t == "pos" else "rgba(255,77,109,.2)" if t == "neg" else "rgba(255,255,255,.07)"}">'
        + (f'<div class="ic">{icon(ic, GREEN if t == "pos" else RED if t == "neg" else CYAN, 20)}</div>' if ic else "")
        + f'<div class="k">{k}</div><div class="v">{v}</div>' + (f'<div class="s">{s}</div>' if s else "") + "</div>"
        for k, v, s, t, ic in items)
    st.markdown(f'<div class="mcards" style="grid-template-columns: repeat({cols}, 1fr)">{html}</div>',
                unsafe_allow_html=True)


def section(title, sub="", ic="bars"):
    st.markdown(f'<div class="sec"><div class="bar"></div><div><b>{icon(ic, GREEN, 19)}{title}</b>'
                + (f"<span>{sub}</span>" if sub else "") + "</div></div>", unsafe_allow_html=True)


def meanings(items):
    with st.expander("What these mean"):
        st.markdown('<div class="guide">' + "".join(f"<p><b>{k}</b> — {v}</p>" for k, v in items) + "</div>",
                    unsafe_allow_html=True)


AXIS = dict(gridColor="#1f2a37", labelColor="#8392a6", domainColor="#2b394a", labelFont="JetBrains Mono",
            titleColor="#8392a6")


def bars(rows, label, colors, fmt=".1f"):
    """Horizontal bars, one row per (name, share %), each in its own colour."""
    df = pd.DataFrame(rows, columns=[label, "share"])
    if df.empty:
        st.caption("Nothing to show.")
        return
    df["color"] = [colors[i % len(colors)] for i in range(len(df))]
    df["txt"] = df["share"].map(lambda v: f"{v:{fmt}}%")
    base = alt.Chart(df).encode(y=alt.Y(f"{label}:N", sort=list(df[label]), title=None,
                                        axis=alt.Axis(labelOverlap=False, labelLimit=170, ticks=False, minExtent=118,
                                                      labelFont="JetBrains Mono", labelFontSize=11,
                                                      labelColor="#cfd8e3", labelPadding=8)),
                                x=alt.X("share:Q", title=None, axis=alt.Axis(labels=False, ticks=False, grid=True),
                                        scale=alt.Scale(domain=[0, max(100, float(df["share"].max()) * 1.15)])))
    chart = base.mark_bar(cornerRadiusEnd=2).encode(color=alt.Color("color:N", scale=None)) + base.mark_text(
        align="left", dx=4, color="#8392a6", font="JetBrains Mono", fontSize=11).encode(text="txt:N")
    st.altair_chart(chart.properties(height=max(90, 40 * len(df))).configure(background="transparent")
                    .configure_view(strokeWidth=0).configure_axis(**AXIS), **stretch(st.altair_chart))


def pnl_chart(daily, height=280):
    """Daily realized PnL (bars) and the running total (line)."""
    df = pd.DataFrame(daily)
    df["when"] = pd.to_datetime(df["day"], unit="s")
    base = alt.Chart(df).encode(x=alt.X("when:T", title=None, axis=alt.Axis(format="%b %-d", tickCount=8)))
    tip = [alt.Tooltip("when:T", title="day"), alt.Tooltip("pnl:Q", title="that day", format="$,.2f"),
           alt.Tooltip("cum:Q", title="running total", format="$,.2f")]
    day = base.mark_bar(opacity=.9, size=14).encode(
        y=alt.Y("pnl:Q", title=None), tooltip=tip,
        color=alt.condition("datum.pnl >= 0", alt.value("#0f9f68"), alt.value("#a3263f")))
    cum = base.mark_line(color=GREEN, strokeWidth=2.4).encode(y="cum:Q", tooltip=tip)
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#8392a6", strokeDash=[6, 4], opacity=.6).encode(y="y:Q")
    st.altair_chart((zero + day + cum).properties(height=height).configure(background="transparent")
                    .configure_view(strokeWidth=0).configure_axis(**AXIS), **stretch(st.altair_chart))


# ----------------------------------------------------------------- data ---
def conn():
    return F.connect()


def rpcs():
    from rpc import make_rpcs
    cfg = config.load_env()
    return make_rpcs(cfg) if cfg["RPC_URL"] else (None, None)


def load(addrs, per):
    """Read and price these wallets for this period (once per session and period,
    or again on Analyze / Refresh), with a progress bar."""
    cfg = config.load_env()
    rpc, call_rpc = rpcs()
    if rpc is None:
        st.error("No RPC yet. Open **Settings** above and paste your Robinhood Chain RPC (SolidRPC link, or an "
                 "Alchemy API key).")
        return False
    start, _ = A.period(per, time.time())
    bar = st.progress(0.0, text="Connecting…")

    def progress(text, frac=None):
        bar.progress(min(1.0, frac) if frac is not None else 0.0, text=text)
    c = conn()
    t0 = time.time()
    try:
        res = F.Loader(c, rpc, call_rpc, cfg, progress).load(addrs, start)
        calls = rpc.calls + (call_rpc.calls if call_rpc is not rpc else 0)
        notes = []
        if F.get_meta(c, "explorer_off_until", 0) > t0 and "explorer" not in res.get("sources", []):
            notes.append(f"explorer unreachable ({F.get_meta(c, 'explorer_error')})")
        st.session_state["last_load"] = (f"Read in {time.time() - t0:.0f}s with {calls:,} RPC requests · via "
                                         + (", ".join(res.get("sources") or ["cache"]))
                                         + ("" if not notes else " · " + "; ".join(notes)))
        by = st.session_state.setdefault("gaps_by", {})  # per wallet, so compare mode keeps every wallet's
        for x in addrs:
            by[x] = {"gaps": [g for g in res.get("gaps") or [] if g["addr"] == x], "diag": res.get("diag") or {},
                     "unchecked": res.get("unchecked") or 0}
    except F.NoSource as e:
        bar.empty()
        st.error("Couldn't find this wallet's positions: every source failed for this period.")
        st.markdown(diagnose(e.diag), unsafe_allow_html=True)
        return False
    except Exception as e:  # noqa: BLE001 - show it, keep the page
        bar.empty()
        msg = str(e)
        if "quota" in msg.lower() or "plan limit" in msg.lower():
            msg += (". Your RPC's daily allowance is used up. Wait for it to reset, add another RPC in Settings, or "
                    "keep RH Wallet Finder's sync paused while you analyse (it shares the same quota).")
        elif "allows only" in msg:
            msg += (". That RPC can only read tiny block ranges (e.g. Alchemy's free tier). Use an RPC with a bigger "
                    "eth_getLogs range, or run RH Wallet Finder next to this app so its database can be read.")
        st.error(f"Couldn't read the chain: {msg}")
        return False
    finally:
        c.close()
    bar.empty()
    return True


HELP = ("**How to get it reading:** (1) run RH Wallet Finder next to this app (Capstone1/rh_wallet_finder) and let "
        "its sync catch up: then nothing here needs eth_getLogs; (2) wait for SolidRPC's daily quota to reset, or put "
        "an RPC with a bigger eth_getLogs range in Settings — Alchemy's free tier only reads 10 blocks per request; "
        "(3) open the Blockscout explorer in your browser once: if it loads, the app can usually use it too.")


def diagnose(diag):
    """What each source said, as a list, plus how to fix it."""
    rows = "".join(f"<p><b>{k}</b> — {v}</p>" for k, v in diag.items())
    return f'<div class="guide">{rows}</div>\n\n' + HELP


def gap_note(addrs):
    """Warn when part of the period couldn't be read (the report covers the rest)."""
    by = st.session_state.get("gaps_by") or {}
    g = next((by[x] for x in addrs if x in by), {})
    gaps = [x for a in addrs for x in (by.get(a) or {}).get("gaps", [])]
    if gaps:
        def when(t):
            return time.strftime("%d %b %H:%M", time.gmtime(t)) + " UTC" if t else "?"
        spans = "; ".join(f"{when(x.get('from_ts'))} → {when(x.get('to_ts'))}" for x in gaps)
        st.markdown(f'<div class="warn">⚠ Part of the period could not be read, so positions opened or closed in it '
                    f"are missing from this report: <b>{spans}</b>.<br><span style='color:#8392a6'>"
                    f"{esc(gaps[0]['why'])}</span></div>", unsafe_allow_html=True)
        with st.expander("Why, and how to fix it"):
            st.markdown(diagnose(g.get("diag", {})), unsafe_allow_html=True)
    elif g.get("unchecked"):
        st.caption(f"{g['unchecked']} position(s) may miss an add / remove the RPC couldn't look up "
                   f"({g['diag'].get('RPC pool log scan', '')}). Refresh later to complete them.")


def filters_now():
    f = st.session_state.get("flt") or {}
    return f.get("mcap_min"), f.get("mcap_max"), f.get("sides")


def report_data(addrs, per, real, filtered=True):
    cfg = config.load_env()
    now = time.time()
    start, end = A.period(per, now)
    c = conn()
    pos, skipped = F.positions(c, addrs, start, cfg)
    first = F.first_seen(c, addrs)
    c.close()
    if filtered:
        lo, hi, sides = filters_now()
        pos = A.filter_positions(pos, lo, hi, sides)
    a = A.analyze(pos, now, start, end, real, float(cfg.get("MULTIDAY_HOURS") or 2) * 3600)
    return {"analysis": a, "skipped": skipped, "positions": pos, "first_seen": first, "start": start, "end": end,
            "pnl": (lambda p: p["pnl"] - (p["cost"] or 0)) if real else (lambda p: p["pnl"])}


def saved_wallets():
    c = conn()
    rows = [dict(r) for r in c.execute("SELECT addr, label FROM saved ORDER BY added DESC")]
    c.close()
    return rows


def save_wallet(addr, label=None):
    c = conn()
    c.execute("INSERT INTO saved(addr, label, added) VALUES(?,?,?) ON CONFLICT(addr) DO UPDATE SET "
              "label=COALESCE(excluded.label, saved.label)", (addr, label, int(time.time())))
    c.commit()
    c.close()


def forget_wallet(addr):
    c = conn()
    c.execute("DELETE FROM saved WHERE addr=?", (addr,))
    c.commit()
    c.close()


def better_wallets(kind, addrs, n=6):
    """Wallets of the same class ranked above on RH Wallet Finder's board (read-only)."""
    p = finder_db.path(config.load_env())
    if not p:
        return None
    try:
        fc = finder_db.connect(p)
        rows = fc.execute("SELECT addr, score, data FROM wallets WHERE score IS NOT NULL ORDER BY score DESC "
                          "LIMIT 400").fetchall()
        fc.close()
    except Exception:  # noqa: BLE001
        return None
    out = []
    for r in rows:
        m = (json.loads(r["data"]) or {}).get("raw") or {}
        if r["addr"] in addrs or (kind != "hybrid" and m.get("kind") and m.get("kind") != kind):
            continue
        out.append({"addr": r["addr"], "score": r["score"], "net": m.get("net"), "win": m.get("win_pos"),
                    "kind": m.get("kind"), "style": m.get("style")})
        if len(out) >= n:
            break
    return out


# ----------------------------------------------------------------- page ---
def header():
    logo = base64.b64encode(LOGO.read_bytes()).decode()
    st.session_state["logo64"] = logo
    st.markdown(f"""
<div class="brand"><img src="data:image/svg+xml;base64,{logo}">
  <div><div class="word">RH <b>LP ANALYZER</b></div><div class="tag">Uniswap V4 liquidity intelligence</div></div>
</div>
<div class="lead">Paste a Robinhood Chain wallet to dissect its PnL, risk &amp; LP edge.</div>""", unsafe_allow_html=True)


def gate():
    """Optional password (APP_PASSWORD) before anything else."""
    pw = config.load_env().get("APP_PASSWORD")
    if not pw or st.session_state.get("authed"):
        return True
    with st.form("login"):
        typed = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            if typed == pw:
                st.session_state["authed"] = True
                st.rerun()
            st.error("Wrong password.")
    return False


def settings():
    cfg = config.load_env()
    fp = finder_db.path(cfg)
    with st.expander("⚙ Settings — RPC & data sources" + ("" if cfg["RPC_URL"] else "  (needed: paste your RPC)"),
                     expanded=not cfg["RPC_URL"]):
        st.caption(f"RPC 1: `{config.mask(cfg['RPC_URL']) or 'not set'}` · RPC 2: "
                   f"`{config.mask(cfg['CALL_RPC_URL']) or 'not set'}`. Saved to rh_lp_analyzer/.env on this computer "
                   "(on Streamlit Cloud, put them in the app's Secrets instead).")
        st.caption("Where wallets are read from, cheapest first: "
                   + ("**RH Wallet Finder's database** (found, read-only) → " if fp else
                      "RH Wallet Finder's database (not found next to this folder) → ")
                   + "the Blockscout explorer → Alchemy's Transfers API → RPC eth_getLogs. Prices always come "
                     "from the RPC (archive eth_call), and only once per event.")
        with st.form("rpc_form", border=False):
            c1, c2 = st.columns(2)
            r1 = c1.text_input("RPC 1 (eth_getLogs + calls)", type="password",
                               placeholder="https://rpc.solidrpc.io/ak_…/evm/4663")
            r2 = c2.text_input("RPC 2 (optional, an Alchemy API key works)", type="password",
                               placeholder="Alchemy key or RPC link")
            c1, c2 = st.columns(2)
            look = c1.number_input("Lookback days: positions opened this long before the period still count",
                                   min_value=0, max_value=120, value=int(float(cfg["LOOKBACK_DAYS"])))
            use_ex = c2.checkbox("Use the Blockscout explorer when it answers",
                                 value=str(cfg["EXPLORER_API"]).lower() not in ("0", "false", "no", "off"))
            use_fd = c2.checkbox("Read RH Wallet Finder's database when it's next door",
                                 value=str(cfg.get("WALLET_FINDER_DB", "")).lower() not in ("0", "false", "no", "off"))
            if st.form_submit_button("Save & test"):
                upd = {"LOOKBACK_DAYS": str(look), "EXPLORER_API": "1" if use_ex else "0",
                       "WALLET_FINDER_DB": "" if use_fd else "0"}
                if r1.strip():
                    upd["RPC_URL"] = config.rpc_link(r1)
                if r2.strip():
                    upd["CALL_RPC_URL"] = config.rpc_link(r2)
                config.save_env(upd)
                for k in upd:
                    os.environ.pop(k, None)
                from rpc import RPC
                for k in ("RPC_URL", "CALL_RPC_URL"):
                    url = config.load_env()[k]
                    if not url:
                        continue
                    try:
                        cid = int(RPC(url).call("eth_chainId", []), 16)
                        (st.success if cid == config.CHAIN_ID else st.warning)(
                            f"{k}: chain id {cid}" + ("" if cid == config.CHAIN_ID else
                                                      f" (Robinhood Chain is {config.CHAIN_ID})"))
                    except Exception as e:  # noqa: BLE001
                        st.error(f"{k}: {e}")


def prefill():
    """Fill the form from a shared link (?w=0x…&p=30 days&m=compare) once."""
    if st.session_state.get("prefilled"):
        return
    st.session_state["prefilled"] = True
    qp = st.query_params
    if qp.get("w"):
        st.session_state["an_text"] = qp["w"].replace(",", ", ")
        if qp.get("p") in A.PERIODS:
            st.session_state["an_period"] = qp["p"]
        st.session_state["an_mode"] = MODES[1] if qp.get("m") == "compare" else MODES[0]
        st.session_state["an_run"] = (tuple(parse_addrs(qp["w"])), st.session_state.get("an_period", "30 days"),
                                      st.session_state["an_mode"])


def fill(addrs):
    st.session_state["an_text"] = ", ".join(addrs)


def open_one(addr):
    st.session_state["an_text"] = addr
    st.session_state["an_mode"] = MODES[0]
    st.session_state["an_run"] = ((addr,), st.session_state.get("an_period", "30 days"), MODES[0])
    st.query_params.update({"w": addr, "m": "single"})


def main():
    css()
    header()
    if not gate():
        return
    settings()
    prefill()
    mode = st.radio("Mode", MODES, horizontal=True, key="an_mode", label_visibility="collapsed")
    n = len(parse_addrs(st.session_state.get("an_text")))
    st.markdown(f'<div class="setup"><span class="on">Setup</span><span>{"single" if mode == MODES[0] else "compare"} · '
                f'{n or 0} wallet{"" if n == 1 else "s"} · {st.session_state.get("an_period", "30 days")}</span></div>',
                unsafe_allow_html=True)
    with st.form("an_form", border=False):
        st.text_input("Wallet", key="an_text", label_visibility="collapsed",
                      placeholder="e.g. 0x3a5e…b9ea  (or paste several, comma/space, to combine)" if mode == MODES[0]
                      else "0x…, 0x…, 0x…  (up to 20 wallets: each analysed, compared, and your capital split)")
        c1, c2 = st.columns(2, vertical_alignment="bottom")
        c1.selectbox("Period", A.PERIODS, key="an_period",
                     help="Positions closed in this period count; open ones count when the period runs to now.",
                     **({} if "an_period" in st.session_state else {"index": A.PERIODS.index("30 days")}))
        go = c2.form_submit_button("Analyze", type="primary", **stretch(st.form_submit_button))
    saved = saved_wallets()
    with st.expander("☆  Saved wallets — quick load & compare"):
        if not saved:
            st.caption("Nothing saved yet: analyse a wallet and hit ★ Save wallet at the bottom.")
        else:
            names = {x["addr"]: f"{short(x['addr'])}  {x['label'] or ''}" for x in saved}
            pick = st.multiselect("Saved wallets", list(names), format_func=names.get, label_visibility="collapsed",
                                  placeholder="Pick one (or several to combine / compare)…")
            c1, c2 = st.columns(2)
            c1.button("Load into the box", disabled=not pick, on_click=fill, args=(pick,))
            if c2.button("Remove from saved", disabled=not pick):
                for a in pick:
                    forget_wallet(a)
                st.rerun()
    if go:
        addrs = parse_addrs(st.session_state.get("an_text"))
        if mode == MODES[1] and len(addrs) > 20:
            st.warning(f"{len(addrs)} wallets pasted: the first 20 are used.")
        if not addrs:
            st.warning("Paste a wallet address (0x + 40 hex).")
            return
        st.session_state["an_run"] = (tuple(addrs), st.session_state["an_period"], mode)
        done = st.session_state.setdefault("loaded_runs", set())  # Analyze reads new blocks again
        for key in [tuple(addrs)] + [(a,) for a in addrs]:
            done.discard((key, st.session_state["an_period"]))
        st.query_params.update({"w": ",".join(addrs), "p": st.session_state["an_period"],
                                "m": "compare" if mode == MODES[1] else "single"})
    run = st.session_state.get("an_run")
    if not run:
        st.markdown(f'<div class="empty"><img src="data:image/svg+xml;base64,{st.session_state["logo64"]}">'
                    '<b>Run a wallet through the terminal</b><span>Paste a wallet address above and hit '
                    '<i>Analyze</i> to break down its PnL, risk profile, capital deployment, and LP gameplay over the '
                    'selected period.</span></div>', unsafe_allow_html=True)
        return
    addrs, per, mode = run
    addrs = list(addrs)[:20]
    loaded = st.session_state.setdefault("loaded_runs", set())
    if mode == MODES[1] and len(addrs) > 1:
        note = st.empty()
        for i, a in enumerate(addrs, 1):
            if ((a,), per) not in loaded:
                note.caption(f"Reading wallet {i}/{len(addrs)}: {short(a)}…")
                if not load([a], per):
                    return
                loaded.add(((a,), per))
        note.empty()
        compare(addrs, per)
    else:
        if (tuple(addrs), per) not in loaded:
            if not load(addrs, per):
                return
            loaded.add((tuple(addrs), per))
        report(addrs, per)


# --------------------------------------------------------------- report ---
def filters_panel():
    """Filters — market cap at entry & position sided. Applies to the whole report."""
    lo, hi, sides = filters_now()
    on = lo or hi or (sides is not None and len(sides) < 3)
    with st.expander("Filters — market cap at entry & position sided" + ("  ·  ON" if on else ""), expanded=bool(on)):
        with st.form("flt_form", border=False):
            c1, c2 = st.columns(2)
            t_lo = c1.text_input("Min Market Cap at Entry", value=compact(lo, 0) if lo else "", placeholder="e.g. 100K",
                                 help="Token supply × pool price when the position opened.")
            t_hi = c2.text_input("Max Market Cap at Entry", value=compact(hi, 0) if hi else "", placeholder="e.g. 1M")
            st.markdown("**Position Sided**")
            st.caption("Which deposit types to include. **ETH/USDG Only** = deposited only ETH or USDG (buys the "
                       "token as price falls into the range) · **Dual Sided** = both · **Token Only** = deposited only "
                       "the token (sells it on the way up).")
            cs = st.columns(3)
            ticks = [cs[i].checkbox(lab, value=sides is None or key in sides, key=f"side_{key}")
                     for i, (key, lab) in enumerate(SIDES)]
            c1, c2 = st.columns(2)
            apply = c1.form_submit_button("Apply filters", type="primary")
            clear = c2.form_submit_button("Clear")
        if apply:
            chosen = [k for (k, _), t in zip(SIDES, ticks) if t]
            st.session_state["flt"] = {"mcap_min": parse_usd(t_lo), "mcap_max": parse_usd(t_hi),
                                       "sides": None if len(chosen) == 3 else chosen}
            st.rerun()
        if clear:
            st.session_state.pop("flt", None)
            for key, _ in SIDES:
                st.session_state.pop(f"side_{key}", None)
            st.rerun()
        st.caption("Token age at entry isn't available for Robinhood Chain tokens yet.")


def report(addrs, per):
    who = " + ".join(short(x) for x in addrs)
    res = report_data(addrs, per, st.session_state.get("real", False))
    a, sk = res["analysis"], res["skipped"]
    now = time.time()
    if res["first_seen"] is None:
        st.warning(f"{who}: no Uniswap V4 LP position (PositionManager NFT) found from "
                   f"{time.strftime('%d %b %Y', time.gmtime(max(config.CHAIN_LAUNCH_TS, res['start'] - 14 * 86400)))}"
                   " on. Check the address, or pick a longer period.")
        return
    with st.expander(f"✓ Analysis complete · {a['positions'] if a else 0:,} positions"):
        if st.session_state.get("last_load"):
            st.caption(st.session_state["last_load"])
        notes = []
        if sk["unpriced"]:
            notes.append(f"{sk['unpriced']:,} not priced yet (an RPC read failed): Refresh to try again")
        if sk["launch"]:
            notes.append(f"{sk['launch']:,} token launch(es) left out (seeded 2%+ of a token's supply)")
        if sk["unpriceable"]:
            notes.append(f"{sk['unpriceable']:,} in pools without ETH/USDG (can't be priced)")
        if sk["before"]:
            notes.append(f"{sk['before']:,} opened before the lookback (deposit unknown)")
        for x in notes:
            st.caption("• " + x)
        if st.button("↻ Refresh — read new blocks and re-value open positions"):
            st.session_state["loaded_runs"].discard((tuple(addrs), per))
            st.rerun()
    gap_note(addrs)
    filters_panel()
    better = better_wallets(a["kind"] if a else "hybrid", addrs) if a else None
    if better is not None:
        with st.popover("Better wallets — from RH Wallet Finder's board", **stretch(st.popover)):
            if not better:
                st.caption("Nothing on the board yet.")
            for b in better:
                c1, c2 = st.columns([4, 1], vertical_alignment="center")
                c1.markdown(esc(f"`{short(b['addr'])}` score **{b['score']:.1f}** · {KIND_LABEL.get(b['kind'], '')} · "
                                f"PnL {money(b['net'], True)} · win {pct(b['win'])}"))
                c2.button("Analyze", key=f"better_{b['addr']}", on_click=open_one, args=(b["addr"],))
    st.toggle("💧 Real PnL — charge the cost of selling the token back", key="real",
              help="Each position pays for selling back the token side it returns: the pool fee + "
                   f"{config.load_env()['SELL_SLIPPAGE_PCT']}% slippage.")
    if not a:
        st.info("Nothing closed or open in this period" + (" with these filters" if any(filters_now()) else "")
                + ". Pick a longer period or loosen the filters.")
        return

    tc, kc = TIER_COLOR[a["verdict"]], KIND_COLOR[a["kind"]]
    age = (now - res["first_seen"]) / 86400
    st.markdown(f"""
<div class="card">
  <div class="title">Wallet Analysis<div class="tags">{pill(KIND_LABEL[a['kind']], kc)}{pill(a['verdict'], tc)}</div></div>
  <div class="chips"><span class="addr">{who}</span><span>{per}</span><span>{a['positions']} positions</span>
    <span>V4 LP age · {age:.0f}d</span>""" + (f'<a href="{config.EXPLORER}/address/{addrs[0]}" target="_blank">explorer ↗</a>'
                                               if len(addrs) == 1 else "") + "</div></div>", unsafe_allow_html=True)
    share_link(addrs, per)
    cards([("Net PnL", money(a["net"], True), f"{a['wins']}W / {a['losses']}L", tone(a["net"]) or "neu", "wallet"),
           ("Capital ROI", pct(a["roi"], 1), f"over {a['active_days']:.1f}d active · ≈{pct(a['roi_30d'], 1)}/30d",
            tone(a["roi"]) or "neu", "trend"),
           ("Win rate", pct(a["win"], 1), f"{a['closed']} positions", "neu", "target")], 3, big=True)

    flags = "".join(f'<div class="flag">{icon(TONE_ICON[t][0], TONE_ICON[t][1], 15)}<div><b>{h}</b> — {x}</div></div>'
                    for t, h, x in a["flags"])
    why = f'<div class="why">Not {next_tier(a["verdict"])} because: {"; ".join(a["why"])}.</div>' if a["why"] else ""
    st.markdown(f"""
<div class="qual">
  <small>Quality</small>{pill(a['verdict'], tc)}<p>{A.TIER_TEXT[a['verdict']]}</p>{why}
  <small>Trader type</small>{pill(KIND_LABEL[a['kind']], kc)}<p>{A.KIND_TEXT[a['kind']]} ({a['kind_fit']}% of its trades
  fit.)</p>{'<hr>' + flags if flags else ''}
</div>""", unsafe_allow_html=True)
    with st.expander("How the classifications work"):
        st.markdown('<div class="guide"><h5>Quality — is this wallet good to copy?</h5>'
                    + "".join(f"<p>{pill(t + ' — ' + tag, TIER_COLOR[t])}&nbsp; {x}</p>" for t, tag, x in A.TIERS)
                    + f"<p style='color:#8392a6'>ELITE: profit factor ≥ {A.ELITE['pf']:g}, drawdown above "
                      f"{A.ELITE['dd']:.0f}%, top 2 days ≤ {A.ELITE['top2']:.0f}% of profit, win ≥ "
                      f"{A.ELITE['win']:.0f}%, {A.ELITE['closed']}+ closed. SOLID: profit factor ≥ "
                      f"{A.SOLID['pf']:g}, drawdown above {A.SOLID['dd']:.0f}%, top 2 days ≤ "
                      f"{A.SOLID['top2']:.0f}%, {A.SOLID['closed']}+ closed. MIXED: profit factor ≥ {A.MIXED_PF:g}. "
                      "Bots (hold under 5 min or 100+ trades a day) never pass SOLID.</p>"
                    + "<h5>Trader Type — by entry mcap, hold time and range width</h5>"
                    + "".join(f"<p>{pill(KIND_LABEL[k], KIND_COLOR[k])}&nbsp; {x}</p>" for k, x in A.KINDS)
                    + "<h5>Gameplay Archetype — the dominant cap × duration × range pattern</h5>"
                    + "".join(f"<p><b>{k}</b><br>{x}</p>" for k, x in A.ARCHETYPES) + "</div>",
                    unsafe_allow_html=True)

    section("Performance &amp; Returns", ic="bars")
    cards([("Total profit", money(a["profit"]), "", "pos", None),
           ("Total loss", money(a["loss"]), "", "neg", None),
           ("Profit / position", money(a["per_position"]), "", tone(a["per_position"]), None),
           ("Profit factor", pf_fmt(a["pf"]), "$ earned per $1 lost · >2 strong", tone(a["pf"], 1), None),
           ("Capital ROI", pct(a["roi"], 1), f"over {a['active_days']:.1f}d active · ≈{pct(a['roi_30d'], 1)}/30d",
            tone(a["roi"]), None),
           ("Median deployed capital", money(a["capital"]), "typical · median while active · gross", "neu", None)], 6)
    cards([("Peak deployed capital", money(a["peak"]), "max simultaneous · gross", "neu", None),
           ("Currently open", money(a["open_now"]), f"{a['open']} open now" if a["live"] else "period already over",
            "pos" if a["open"] else "neu", None),
           ("Floating PnL", money(a["float"], True), "open positions · incl unclaimed fees", tone(a["float"]), None),
           ("Net PnL incl. floating", money(a["total"], True),
            f"closed {money(a['net'], True)} · floating {money(a['float'], True)}", tone(a["total"]), None),
           ("Fees earned", money(a["fees"]), "total LP fees over the period", "pos" if a["fees"] else "neu", None),
           ("Token only", pct(a["token_only"], 1), f"{a['token_only_n']} positions", "neu", None)], 6)
    meanings([("Total profit / loss", "What winning positions made and losing ones lost (closed in the period)."),
              ("Profit factor", "Total profit ÷ total loss. Above 1 makes money; above 2 is strong."),
              ("Capital ROI", "Net PnL ÷ median deployed capital; ≈/30d scales it to 30 days of activity."),
              ("Median / peak deployed", "USD sitting in its positions: the time-weighted median while it had "
                                         "anything open, and the most at once. Gross: re-deposits count again."),
              ("Floating PnL", "Open positions: value now + unclaimed fees − deposits."),
              ("Token only", "Share of positions where only the token went in (selling it on the way up).")])

    section("Daily &amp; Cumulative PnL", "USD gained over " + per.lower(), ic="pulse")
    if len(a["daily"]) >= 2:
        pnl_chart(a["daily"])
    else:
        st.caption("Needs closes on at least two days.")

    rec = lambda v: "" if v is None or not a["avg_green"] else f"≈ {abs(v) / a['avg_green']:.2f} green days to recover"  # noqa: E731
    section("Risk Management", "how big and how often the losses get, and how long they take to win back",
            ic="shield")
    rk = a["risk"]
    cards([("Max PnL drawdown", pct(a["dd"], 1), dd_note(rk), tone(a["dd"]) or "neu", None),
           ("Longest under water", days_txt(rk["underwater_days"]), "below a previous high, longest stretch",
            "neg" if rk["underwater_days"] >= 7 else "neu", None),
           ("Back after a red day", days_txt(rk["recover_avg"], 1), f"on average · longest {days_txt(rk['recover_max'])}"
            + (f" · {rk['red_open']} not won back yet" if rk["red_open"] else ""), "neu", None),
           ("Current drawdown", pct(rk["current_dd"], 1),
            f"{days_txt(rk['days_since_peak'])} since its high" if rk["days_since_peak"] else "at its high",
            "neg" if rk["current_dd"] < -0.05 else "pos", None),
           ("Losing streak", f"{rk['loss_streak']} positions", f"longest run of red days: {rk['red_streak']}",
            "neg" if rk["loss_streak"] >= 5 else "neu", None)], 5)
    cards([("Worst single-day loss", money(a["worst_day"]), rec(a["worst_day"]), tone(a["worst_day"]) or "neu", None),
           ("Out-of-range rate", pct(a["oor_rate"], 1), f"{a['oor_n']} of {a['oor_of']} closed out-of-range at a loss",
            "neu", None),
           ("Worst OOR loss", money(a["oor_worst"]), rec(a["oor_worst"]), tone(a["oor_worst"]) or "neu", None),
           ("Median OOR loss", money(a["oor_median"]), rec(a["oor_median"]), tone(a["oor_median"]) or "neu", None)], 4)
    underwater_chart({short(addrs[0]) if len(addrs) == 1 else "combined": a})
    meanings([("Max PnL drawdown", "The deepest fall of its realized PnL curve from a high, as % of its median "
                                   "deployed capital, how long that fall took and how long it took to get back."),
              ("Longest under water", "The longest stretch its running PnL stayed below an earlier high: how long "
                                      "you'd have waited for a new high copying it."),
              ("Back after a red day", "After each losing day, how many days until its running PnL was back where "
                                       "it was before that day."),
              ("Current drawdown", "How far its running PnL sits below its high right now."),
              ("Worst single-day loss", "Its worst day of closes, and how many average green days it takes to win "
                                        "it back."),
              ("Out-of-range rate", "Closed positions whose last withdrawal came back all in one token (price ran "
                                    "out of the range) and lost money, out of all closed ones."),
              ("Followable", f"{FOLLOW[a['follow']]} — median hold {dur(a['hold'])}, {a['per_day']:.1f} trades a day. "
                             "Bots hold under 5 min or trade 100+ times a day.")])

    section("Profit Quality", "is the profit steady or carried by a few lucky days", ic="star")
    cards([("Avg daily profit", money(a["avg_green"]), f"across {a['green_days']} green days", "pos", None),
           ("Avg daily loss", money(a["avg_red"]), f"across {a['red_days']} red days", "neg", None),
           ("Profit concentration", pct(a["concentration"], 1), "from top 2 days",
            "neg" if (a["concentration"] or 0) >= 60 else "pos" if a["concentration"] is not None else "neu", None)], 3)
    rk = a["risk"]
    cards([("Consistency", f"{rk['consistency']:.2f}" if rk["consistency"] is not None else "–",
            "average day ÷ how much days swing · >0.3 steady", tone(rk["consistency"], 0.3) or "neu", None),
           ("Daily swing", pct(rk["daily_vol"], 2), "typical day's move, % of capital", "neu", None),
           ("Fees in profit", pct(rk["fee_share"]), "share of net PnL that came from LP fees", "neu", None),
           ("Last 7 days", money(rk["last7"], True), (f"{rk['momentum']:.1f}× its usual week" if rk["momentum"]
                                                      is not None else ""), tone(rk["last7"]) or "neu", None),
           ("Best / worst position", f"{money(rk['best'], True)} / {money(rk['worst'])}",
            f"expectancy {money(rk['expectancy'], True)} per position", "neu", None)], 5)
    meanings([("Avg daily profit / loss", "Average of its green days and of its red days (by close date)."),
              ("Consistency", "Average daily PnL ÷ the spread of daily PnL. Higher = steadier; under 0 = losing."),
              ("Fees in profit", "LP fees ÷ net PnL. Near or above 100% = the fees carry it and price moves cost it."),
              ("Last 7 days", "Realized PnL of the last week, and how it compares with its average week here."),
              ("Profit concentration", "Share of all green-day profit made on its two best days. High = it depends "
                                       "on a few big runs.")])

    section("Recommended Filter", "market cap & sided that maximised returns over this period", ic="sliders")
    st.caption("Grid-searches entry market cap ranges × deposit side × ETH/USDG pool for the filter that kept the "
               "most PnL. It runs only when you ask.")
    fkey = ("best", tuple(addrs), per, st.session_state.get("real", False))
    if st.button("Find the best filter", **stretch(st.button)):
        full = report_data(addrs, per, st.session_state.get("real", False), filtered=False)
        closed = [p for p in full["positions"] if p["close_ts"] and p["close_ts"] >= full["start"]
                  and (full["end"] is None or p["close_ts"] <= full["end"])]
        st.session_state[fkey] = A.recommend_filters(closed, full["pnl"])
    best = st.session_state.get(fkey)
    if best:
        if best["best"]:
            rows = [{"Filter": r["name"], "Trades": r["trades"], "PnL": r["pnl"], "ROI": r["roi"], "Win": r["win"],
                     "vs everything": r["pnl"] - best["all"]["pnl"]} for r in [best["all"]] + best["best"]]
            st.dataframe(pd.DataFrame(rows).style.format(
                {"PnL": lambda v: money(v, True), "ROI": lambda v: pct(v, 1), "Win": lambda v: pct(v),
                 "vs everything": lambda v: money(v, True)}, na_rep="–"), hide_index=True, **stretch(st.dataframe))
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            pick = c1.selectbox("Apply one as the report's filter", range(len(best["best"])),
                                format_func=lambda i: best["best"][i]["name"])
            if c2.button("Apply", **stretch(st.button)):
                r = best["best"][pick]
                st.session_state["flt"] = {"mcap_min": r["mcap_min"], "mcap_max": r["mcap_max"],
                                           "sides": None if len(r["sides"]) == 3 else r["sides"]}
                for key, _ in SIDES:
                    st.session_state.pop(f"side_{key}", None)
                st.rerun()
            st.caption(f"Combinations need {A.FILTER_MIN_TRADES}+ closed trades. Past fit, not a promise.")
        else:
            st.caption("No filter beats taking every trade.")

    sizing(a)

    section("Position Composition", "share of deposit types", ic="pie")
    bars(a["sides"], "type", [ORANGE, GREEN, CYAN])

    section("Gameplay Detail", ic="compass")
    st.markdown(f'<div class="play"><b>{a["play"]}</b><p>{a["play_text"]} ({a["play_share"]}% of its trades.)</p>'
                '</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="chart-t">RANGE DISTRIBUTION</div>', unsafe_allow_html=True)
        bars(a["range_dist"], "range", [GREEN, CYAN, PURPLE, YELLOW, ORANGE, RED, "#ec4899"])
        st.markdown(f'<div class="legend">{A.RANGE_LEGEND}</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="chart-t">DURATION DISTRIBUTION</div>', unsafe_allow_html=True)
        bars(list(reversed(a["hold_dist"])), "hold", [YELLOW, PURPLE, GREEN, CYAN, GREEN])
    st.markdown('<div class="chart-t">MARKET CAP AT ENTRY</div>', unsafe_allow_html=True)
    bars(list(reversed(a["mcap_dist"])), "mcap", [RED, "#ff6b4a", ORANGE, YELLOW, "#c7d93a", "#7bd94a", GREEN, CYAN])
    st.caption(f"Range = upper ÷ lower price − 1 of a trade (a ladder: lowest to highest). Entry market cap = token "
               f"price in the pool × on-chain supply when it opened; known for {a['mcap_known']} of {a['trades']} "
               "trades (the rest use today's).")

    section("Pools", "where the PnL came from", ic="pool")
    pools = pd.DataFrame(a["pools"])
    pools["Win"] = pools.apply(lambda r: r["wins"] / r["closed"] * 100 if r["closed"] else None, axis=1)
    st.dataframe(pools[["pair", "positions", "closed", "open", "pnl", "fees", "dep", "Win"]].style.format(
        {"pnl": lambda v: money(v, True), "fees": usd, "dep": usd, "Win": lambda v: pct(v)}, na_rep="–"),
        hide_index=True, **stretch(st.dataframe), height=auto_height(len(pools), 300),
        column_config={"pair": "Pool", "positions": "Positions", "closed": "Closed", "open": "Open", "pnl": "PnL",
                       "fees": "Fees", "dep": "Deposited"})

    section("Per-Position Detail", ic="list")
    with st.expander("Show positions"):
        lo, hi, sides = filters_now()
        st.caption("Showing sided types: " + ", ".join(lab for k, lab in SIDES if sides is None or k in sides)
                   + " (set via the Filters panel above).")
        pos = pd.DataFrame(res["positions"])
        pnl = pos["pnl"] - (pos["cost"].fillna(0) if st.session_state.get("real") else 0)
        rng = pos.apply(lambda r: A.M.range_pct(r), axis=1)
        view = pd.DataFrame({
            "Open": pd.to_datetime(pos["open_ts"], unit="s").dt.strftime("%Y-%m-%d"),
            "Closed": pd.to_datetime(pos["close_ts"], unit="s").dt.strftime("%Y-%m-%d").fillna("open"),
            "Pair": pos["pair"], "Market Cap": pos["mcap"].map(usd),
            "Sided": pos["side"].map(lambda s: A.SIDE_LABEL.get(s, "–")), "Range %": rng.round(2),
            "Range Flag": rng.map(lambda v: A.bucket(v, A.RANGE_BUCKETS)),
            "Duration (h)": ((pos["close_ts"].fillna(now) - pos["open_ts"]) / 3600).round(2),
            "Size ($)": pos["dep"].round(2), "PnL ($)": pnl.round(2), "Fees ($)": pos["fee"].round(2),
            "OOR": ["OOR" if p.get("oor") else "-" for p in res["positions"]],
            "_t": pos["open_ts"],
        }).sort_values("_t", ascending=False).drop(columns="_t")
        st.dataframe(view, hide_index=True, **stretch(st.dataframe), height=420)
        st.download_button("Download positions (CSV)", view.to_csv(index=False),
                           f"{'_'.join(x[:8] for x in addrs)}_{per.replace(' ', '')}.csv", "text/csv")

    if len(addrs) == 1:
        with st.popover("★ Save wallet"):
            lab = st.text_input("Label (optional)", key=f"lab_{addrs[0]}")
            if st.button("Save", key=f"save_{addrs[0]}"):
                save_wallet(addrs[0], lab.strip() or None)
                st.toast("Saved")


def days_txt(v, d=0):
    v = num(v)
    if v is None:
        return "–"
    return f"{v:.{d}f} day" + ("" if round(v, d) == 1 else "s")


def dd_note(rk):
    if not rk["max_dd_usd"]:
        return "no drawdown yet"
    fall = f"fell over {days_txt(rk['fall_days'])}"
    return f"{fall} · back in {days_txt(rk['recover_days'])}" if rk["recovered"] else f"{fall} · not back yet"


def underwater_chart(named, height=200):
    """Drawdown below the running high, % of capital, one line per wallet."""
    rows = []
    for name, a in named.items():
        peak, cap = 0.0, a["capital"] or 1.0
        for d in a["daily"]:
            peak = max(peak, d["cum"])
            rows.append({"wallet": name, "when": pd.to_datetime(d["day"], unit="s"), "dd": (d["cum"] - peak) / cap * 100})
    if not rows:
        return
    df = pd.DataFrame(rows)
    st.markdown('<div class="chart-t">UNDER WATER — % BELOW ITS HIGH</div>', unsafe_allow_html=True)
    enc = dict(x=alt.X("when:T", title=None, axis=alt.Axis(format="%b %-d")), y=alt.Y("dd:Q", title=None),
               tooltip=["wallet", alt.Tooltip("when:T"), alt.Tooltip("dd:Q", format=".1f", title="% below high")])
    if len(named) > 1:
        ch = alt.Chart(df).mark_line(strokeWidth=1.8).encode(color=alt.Color("wallet:N", title=None), **enc)
    else:
        ch = alt.Chart(df).mark_area(color=RED, opacity=.35, line={"color": RED}).encode(**enc)
    st.altair_chart(ch.properties(height=height).configure(background="transparent").configure_view(strokeWidth=0)
                    .configure_axis(**AXIS).configure_legend(labelColor="#b6c2d0", orient="bottom"),
                    **stretch(st.altair_chart))


def next_tier(tier):
    names = [t for t, _, _ in A.TIERS]
    i = names.index(tier)
    return names[i - 1] if i > 0 else tier


def share_link(addrs, per):
    q = f"?w={','.join(addrs)}&p={per.replace(' ', '%20')}"
    base = ""
    try:
        base = (getattr(st.context, "url", None) or "").split("?")[0]
    except Exception:  # noqa: BLE001
        pass
    with st.popover("🔗 Copy share link"):
        st.code((base or "http://localhost:8502/") + q, language=None)
        st.caption("Opens this wallet and period straight away.")


def sizing(a):
    section("Position Sizing", "size your positions to this wallet's deployment scale", ic="target")
    worth = None
    with st.expander("Use my wallet — fill capital from my net worth"):
        mine_addr = st.text_input("Your wallet", key="mine_addr", placeholder="0x… (reads ETH + USDG + its open "
                                  "positions we've read)").strip().lower()
        if ADDR_RE.fullmatch(mine_addr):
            worth = st.session_state.get(f"worth_{mine_addr}")
            if worth is None:
                try:
                    from pricing import multicall
                    _, call_rpc = rpcs()
                    eb, ub = multicall(call_rpc, [(config.MULTICALL3, C.cd_simple(C.SEL_ETH_BALANCE, mine_addr)),
                                                  (config.USDG, C.cd_simple(C.SEL_BALANCE_OF, mine_addr))], "latest")
                    c = conn()
                    eth_usd = F.get_meta(c, "eth_usd") or 0
                    held = c.execute("SELECT SUM(now_usd) FROM positions WHERE owner=? AND close_ts IS NULL AND "
                                     "valued=1", (mine_addr,)).fetchone()[0] or 0.0
                    c.close()
                    eth = int(eb, 16) / 1e18 if eb else 0.0
                    usdg = int(ub, 16) / 1e6 if ub else 0.0
                    worth = {"eth": eth, "usdg": usdg, "held": held, "worth": eth * eth_usd + usdg + held}
                    st.session_state[f"worth_{mine_addr}"] = worth
                except Exception as e:  # noqa: BLE001
                    st.caption(f"Couldn't read it: {e}")
            if worth:
                st.caption(esc(f"{worth['eth']:.4f} ETH + {usd(worth['usdg'])} USDG"
                               + (f" + {usd(worth['held'])} in positions" if worth["held"] else "")
                               + f" ≈ {usd(worth['worth'])}"))
    mine = st.number_input("Your wallet balance (USD)", min_value=0.0, step=100.0,
                           value=float(round(worth["worth"])) if worth else 0.0, key=f"mine_{bool(worth)}")
    target = st.number_input("Custom deployment target (USD)", min_value=0.0, step=100.0, key="mine_target",
                             help="How much you want working in positions at a time; empty = your whole balance.")
    st.markdown(esc(f"<span style='color:#8392a6;font-size:13px'>Target median deployed **{money(a['capital'])}** · "
                    f"peak **{money(a['peak'])}** · largest position **{money(a['largest'])}** · currently open "
                    f"**{money(a['open_now'])}**</span>"), unsafe_allow_html=True)
    sz = A.sizing(a, target or mine)
    if sz:
        cards([("Sizing ratio", f"{sz['ratio']:.3f}×", "your capital ÷ its median deployed", "neu", None),
               ("Per trade", money(sz["per_trade"]), "its median trade, scaled", "neu", None),
               ("Max per position", money(sz["max_position"]), "its largest position, scaled", "neu", None),
               ("Peak at once", money(sz["peak"]), "its peak deployed, scaled", "neu", None)], 4)
    else:
        st.caption("Enter your wallet balance above to get a sizing ratio and max USD per position.")


# -------------------------------------------------------------- compare ---
def compare(addrs, per):
    st.toggle("💧 Real PnL — charge the cost of selling the token back", key="real")
    results = {x: report_data([x], per, st.session_state.get("real", False), filtered=False)["analysis"]
               for x in addrs}
    rank = {t: i for i, (t, _, _) in enumerate(A.TIERS)}
    order = sorted(addrs, key=lambda x: (results[x] is None, rank.get((results[x] or {}).get("verdict"), 9),
                                         -((results[x] or {}).get("roi_30d") or -1e9)))
    with st.expander(f"✓ Compared {len(addrs)} wallets · {per} · each analysed on its own, best first"):
        if st.session_state.get("last_load"):
            st.caption(st.session_state["last_load"])
    for x in addrs:
        if ((st.session_state.get("gaps_by") or {}).get(x) or {}).get("gaps"):
            st.markdown(f"**{short(x)}**", unsafe_allow_html=True)
            gap_note([x])

    section("Leaderboard", "best first: quality tier, then ROI", ic="bars")
    recs = []
    for x in order:
        a = results[x]
        if not a:
            recs.append({"Wallet": short(x), "Quality": "no data"})
            continue
        rk = a["risk"]
        recs.append({"Wallet": short(x), "Quality": a["verdict"], "Type": KIND_LABEL[a["kind"]], "Gameplay": a["play"],
                     "Net PnL": a["net"], "Floating": a["float"], "ROI/30d": a["roi_30d"], "Win": a["win"],
                     "PF": min(a["pf"], 99) if a["pf"] is not None else None, "Max DD": a["dd"],
                     "Back in": (f"{rk['recover_days']}d" if rk["recovered"] else "not yet") if rk["max_dd_usd"] else "–",
                     "Under water": rk["underwater_days"], "Now below high": rk["current_dd"],
                     "Worst day": a["worst_day"], "Red day back": rk["recover_avg"], "Loss streak": rk["loss_streak"],
                     "Consistency": rk["consistency"], "Fees in profit": rk["fee_share"], "Last 7d": rk["last7"],
                     "OOR loss": a["oor_rate"], "Top-2 days": a["concentration"], "Deployed": a["capital"],
                     "Trade size": a["size"], "Hold": dur(a["hold"]), "Range": range_fmt(a["range"]),
                     "Trades/day": a["per_day"], "Closed": a["closed"], "Follow": FOLLOW[a["follow"]]})
    df = pd.DataFrame(recs)
    sty = df.style.format({"Net PnL": lambda v: money(v, True), "Floating": lambda v: money(v, True),
                           "ROI/30d": lambda v: pct(v, 1), "Win": lambda v: pct(v), "PF": pf_fmt,
                           "Max DD": lambda v: pct(v, 1), "Under water": lambda v: f"{v:.0f}d",
                           "Now below high": lambda v: pct(v, 1), "Worst day": money,
                           "Red day back": lambda v: "–" if v is None or v != v else f"{v:.1f}d",
                           "Loss streak": "{:.0f}", "Consistency": lambda v: "–" if v is None or v != v else f"{v:.2f}",
                           "Fees in profit": lambda v: pct(v), "Last 7d": lambda v: money(v, True),
                           "OOR loss": lambda v: pct(v), "Top-2 days": lambda v: pct(v), "Deployed": money,
                           "Trade size": money, "Trades/day": "{:.1f}"}, na_rep="–")
    sty = sty.map(lambda v: f"color: {TIER_COLOR[v]}; font-weight: 700" if v in TIER_COLOR else "", subset=["Quality"])
    st.dataframe(sty, hide_index=True, **stretch(st.dataframe), height=auto_height(len(df), 560),
                 column_config={"Wallet": st.column_config.TextColumn(pinned=True) if _pin_ok() else "Wallet"})
    with st.expander("What these columns mean"):
        st.markdown('<div class="guide">' + "".join(f"<p><b>{k}</b> — {v}</p>" for k, v in [
            ("Max DD", "Deepest fall of its realized PnL from a high, % of its median deployed capital."),
            ("Back in", "Days from the bottom of that drawdown back to the old high (not yet = still under)."),
            ("Under water", "Longest stretch below an earlier high: the longest you'd have waited for a new high."),
            ("Now below high", "How far it sits below its high today."),
            ("Red day back", "Average days to win back a losing day."),
            ("Loss streak", "Most losing positions in a row."),
            ("Consistency", "Average day ÷ how much days swing. Above 0.3 is steady; under 0 is losing."),
            ("Fees in profit", "LP fees ÷ net PnL: near 100%+ means fees carry it and price moves cost it."),
            ("OOR loss", "Closed out of range at a loss, % of closes."),
            ("Top-2 days", "Share of its profit made on its two best days.")]) + "</div>", unsafe_allow_html=True)

    section("Risk Cards", "each wallet's drawdown, recovery and streaks at a glance", ic="shield")
    good = [x for x in order if results[x]]
    for i in range(0, len(good), 2):
        cols = st.columns(2)
        for c, x in zip(cols, good[i:i + 2]):
            c.markdown(risk_card(x, results[x]), unsafe_allow_html=True)

    named = {short(x): results[x] for x in order if results[x]}
    curves = [{"wallet": short(x), "when": pd.to_datetime(d["day"], unit="s"), "cum": d["cum"]}
              for x in order if results[x] for d in results[x]["daily"]]
    if curves:
        section("Cumulative Realized PnL", ic="pulse")
        st.altair_chart(alt.Chart(pd.DataFrame(curves)).mark_line(strokeWidth=2).encode(
            x=alt.X("when:T", title=None), y=alt.Y("cum:Q", title=None), color=alt.Color("wallet:N", title=None),
            tooltip=["wallet", alt.Tooltip("when:T"), alt.Tooltip("cum:Q", format="$,.2f")])
            .properties(height=280).configure(background="transparent").configure_view(strokeWidth=0)
            .configure_axis(**AXIS).configure_legend(labelColor="#b6c2d0", orient="bottom"), **stretch(st.altair_chart))
        underwater_chart(named)
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    pick = c1.selectbox("Open one wallet's full analysis", [""] + order,
                        format_func=lambda x: "Pick a wallet…" if not x else short(x))
    c2.button("Open analysis", disabled=not pick, on_click=open_one, args=(pick,), **stretch(st.button))
    allocation({x: a for x, a in results.items() if a})


def _pin_ok():
    return _accepts(st.column_config.TextColumn, "pinned")


def risk_card(x, a):
    rk = a["risk"]
    row = lambda k, v, t="": f'<div><small>{k}</small><span class="{t}">{v}</span></div>'  # noqa: E731
    back = (f"back in {days_txt(rk['recover_days'])}" if rk["recovered"] else "not back yet") if rk["max_dd_usd"] \
        else "no drawdown"
    flags = "".join(f'<div class="flag">{icon(TONE_ICON[t][0], TONE_ICON[t][1], 13)}<div><b>{h}</b></div></div>'
                    for t, h, _ in a["flags"][:3])
    return (f'<div class="rcard"><div class="rhead"><b>{short(x)}</b><span>{pill(KIND_LABEL[a["kind"]], KIND_COLOR[a["kind"]])}'
            f'{pill(a["verdict"], TIER_COLOR[a["verdict"]])}</span></div><div class="rplay">{a["play"]}</div>'
            '<div class="rgrid">'
            + row("Net PnL", money(a["net"], True), tone(a["net"]))
            + row("ROI / 30d", pct(a["roi_30d"], 1), tone(a["roi_30d"]))
            + row("Win · PF", f"{pct(a['win'])} · {pf_fmt(a['pf'])}")
            + row("Max drawdown", pct(a["dd"], 1), tone(a["dd"]))
            + row("Recovery", back, "pos" if rk["recovered"] else "neg")
            + row("Longest under water", days_txt(rk["underwater_days"]))
            + row("Worst day", money(a["worst_day"]), tone(a["worst_day"]))
            + row("Red day won back", "avg " + days_txt(rk["recover_avg"], 1))
            + row("Loss streak", f"{rk['loss_streak']} in a row")
            + row("Now below high", pct(rk["current_dd"], 1), "neg" if rk["current_dd"] < -0.05 else "pos")
            + row("Consistency", f"{rk['consistency']:.2f}" if rk["consistency"] is not None else "–")
            + row("Last 7 days", money(rk["last7"], True), tone(rk["last7"]))
            + f"</div>{flags}</div>")


def allocation(results):
    section("Split Your Capital", "every wallet you follow gets a share; you choose how much of your port goes in",
            ic="split")
    c1, c2 = st.columns(2)
    capital = c1.number_input("Your capital (USD)", min_value=0.0, value=2500.0, step=100.0, key="alloc_capital")
    deploy = c2.slider("Put to work (% of capital)", 50, 100, int(A.DEPLOY_PCT), key="alloc_deploy",
                       help="The rest stays in your wallet as a buffer (gas, top-ups, a bad day).")
    with_rej = st.checkbox("Also give REJECT wallets a (small) share", key="alloc_rej",
                           help="Off: wallets that lost money over the period get nothing.")
    if not results or capital <= 0:
        return
    plans = {name: A.allocate(results, capital, name, deploy, with_rej) for name in A.PROFILES}

    def sim(r, k):
        return (r["sim"] or {}).get(k)
    comp = pd.DataFrame([{
        "Profile": name, "Wallets": len(r["rows"]), "Largest share": max((x["share"] for x in r["rows"]), default=None),
        "Into wallets": r["deployed"], "Cash": r["reserve"], "Replay PnL": sim(r, "total"),
        "Replay /30d": sim(r, "roi_30d"), "Max DD": sim(r, "max_dd"), "Back in": sim(r, "recover_days"),
        "Under water": sim(r, "underwater_days"), "Worst day": sim(r, "worst_day"),
        "Green days": sim(r, "green_share")} for name, r in plans.items()])
    st.markdown("**The three ways to split it, side by side** — replayed on these wallets' own results over the period")
    st.dataframe(comp.style.format({"Largest share": lambda v: pct(v, 1), "Into wallets": money, "Cash": money,
                                    "Replay PnL": lambda v: money(v, True), "Replay /30d": lambda v: pct(v, 1),
                                    "Max DD": lambda v: pct(v, 1),
                                    "Back in": lambda v: "not yet" if v is None or v != v else f"{v:.0f}d",
                                    "Under water": lambda v: "–" if v is None or v != v else f"{v:.0f}d",
                                    "Worst day": money, "Green days": lambda v: pct(v)}, na_rep="–"),
                 hide_index=True, **stretch(st.dataframe))
    tabs = st.tabs(list(A.PROFILES))
    for tab, name in zip(tabs, A.PROFILES):
        with tab:
            r = plans[name]
            st.caption(r["text"])
            if not r["rows"]:
                st.info("No wallet in this batch can take a share (all REJECT, or the tickets would be under $50). "
                        "Tick 'Also give REJECT wallets a share', add wallets, or raise the capital.")
                continue
            sm = r["sim"] or {}
            back = ("back in " + days_txt(sm.get("recover_days"))) if sm.get("recovered") else "not back yet"
            cards([("Into wallets", money(r["deployed"]), f"{deploy}% of {money(capital)} · {len(r['rows'])} wallets",
                    "pos", None),
                   ("Kept in cash", money(r["reserve"]), f"{100 - deploy}% buffer", "neu", None),
                   ("Replayed PnL", money(sm.get("total"), True),
                    f"{pct(sm.get('roi'), 1)} over the period · ≈{pct(sm.get('roi_30d'), 1)}/30d", tone(sm.get("total")),
                    None),
                   ("Portfolio max drawdown", pct(sm.get("max_dd"), 1),
                    f"{money(sm.get('max_dd_usd'))} · {back}", "neg" if (sm.get("max_dd") or 0) < -0.05 else "pos",
                    None)], 4)
            cards([("Longest under water", days_txt(sm.get("underwater_days")), "longest wait for a new high", "neu",
                    None),
                   ("Worst day", money(sm.get("worst_day")), f"best day {money(sm.get('best_day'), True)}",
                    tone(sm.get("worst_day")) or "neu", None),
                   ("Red day won back", days_txt(sm.get("recover_avg"), 1), f"on average · longest "
                    f"{days_txt(sm.get('recover_max'))}", "neu", None),
                   ("Green days", pct(sm.get("green_share")), "days the portfolio ended up", "neu", None),
                   ("Wallets move together", "–" if sm.get("corr") is None else f"{sm['corr']:.2f}",
                    "average daily correlation · lower = better spread", "neg" if (sm.get("corr") or 0) > 0.5 else "neu",
                    None)], 5)
            df = pd.DataFrame([{"Wallet": short(x["addr"]), "Quality": x["verdict"], "Share": x["share"],
                                "USD": x["usd"], "Per trade": x["per_trade"], "Max per position": x["max_position"],
                                "ROI/30d": x["roi_30d"], "Max DD": x["dd"],
                                "Note": "trades get tiny: copy only its bigger trades" if x["small"] else ""}
                               for x in r["rows"]])
            c1, c2 = st.columns([3, 2])
            with c1:
                st.dataframe(df.style.format({"Share": lambda v: pct(v, 1), "USD": money, "Per trade": money,
                                              "Max per position": money, "ROI/30d": lambda v: pct(v, 1),
                                              "Max DD": lambda v: pct(v, 1)}, na_rep="–")
                             .map(lambda v: f"color: {TIER_COLOR[v]}; font-weight: 700" if v in TIER_COLOR else "",
                                  subset=["Quality"]), hide_index=True, **stretch(st.dataframe))
            with c2:
                pie = pd.DataFrame([{"wallet": short(x["addr"]), "usd": x["usd"]} for x in r["rows"]]
                                   + [{"wallet": "cash", "usd": r["reserve"]}])
                st.altair_chart(alt.Chart(pie).mark_arc(innerRadius=55, stroke="#0b1016", strokeWidth=2).encode(
                    theta="usd:Q", color=alt.Color("wallet:N", title=None, sort=list(pie["wallet"])),
                    tooltip=["wallet", alt.Tooltip("usd:Q", format="$,.0f")]).properties(height=230)
                    .configure(background="transparent").configure_view(strokeWidth=0)
                    .configure_legend(labelColor="#b6c2d0"), **stretch(st.altair_chart))
            if sm.get("daily"):
                pdf = pd.DataFrame(sm["daily"])
                pdf["when"] = pd.to_datetime(pdf["day"], unit="s")
                peak = pdf["cum"].cummax().clip(lower=0)
                pdf["dd"] = (pdf["cum"] - peak) / (r["deployed"] or 1) * 100
                st.markdown('<div class="chart-t">YOUR PORTFOLIO, REPLAYED — RUNNING PNL</div>', unsafe_allow_html=True)
                base = alt.Chart(pdf).encode(x=alt.X("when:T", title=None, axis=alt.Axis(format="%b %-d")))
                st.altair_chart((base.mark_bar(opacity=.85, size=10).encode(
                    y=alt.Y("pnl:Q", title=None), color=alt.condition("datum.pnl >= 0", alt.value("#0f9f68"),
                                                                      alt.value("#a3263f")),
                    tooltip=[alt.Tooltip("when:T"), alt.Tooltip("pnl:Q", format="$,.2f", title="that day")])
                    + base.mark_line(color=GREEN, strokeWidth=2.4).encode(
                        y="cum:Q", tooltip=[alt.Tooltip("when:T"), alt.Tooltip("cum:Q", format="$,.2f", title="total")]))
                    .properties(height=240).configure(background="transparent").configure_view(strokeWidth=0)
                    .configure_axis(**AXIS), **stretch(st.altair_chart))
                st.altair_chart(base.mark_area(color=RED, opacity=.35, line={"color": RED}).encode(
                    y=alt.Y("dd:Q", title=None), tooltip=[alt.Tooltip("when:T"),
                                                         alt.Tooltip("dd:Q", format=".1f", title="% below high")])
                    .properties(height=130).configure(background="transparent").configure_view(strokeWidth=0)
                    .configure_axis(**AXIS), **stretch(st.altair_chart))
            hot = [(x, y, c) for x, y, c in sm.get("pairs") or [] if c >= 0.6]
            if hot:
                st.markdown("".join(f'<div class="flag">{icon("alert", YELLOW, 15)}<div><b>Move together</b> — '
                                    f"{short(x)} and {short(y)}: daily PnL correlation {c:.2f}. Their bad days tend "
                                    f"to land together.</div></div>" for x, y, c in hot), unsafe_allow_html=True)
            if r["dropped"]:
                st.caption("No share for " + ", ".join(short(x) for x in r["dropped"])
                           + f": their ticket would be under ${A.MIN_TICKET:.0f}.")
            if r["skipped"]:
                st.caption("Left out (REJECT): " + ", ".join(short(x) for x in r["skipped"]))
    ov = A.overlaps(results)
    if ov:
        st.markdown("".join(f'<div class="flag">{icon("alert", YELLOW, 15)}<div><b>Same pools</b> — {short(x)} and '
                            f'{short(y)} share {j:.0f}% of their pools: copying both doubles the same bets.</div></div>'
                            for x, y, j in ov), unsafe_allow_html=True)
    st.caption("Replay = each wallet's daily realized PnL over the period, scaled by your share ÷ its median deployed "
               "capital, added up. Per trade / max per position = its median trade and largest position on the same "
               "scale. Built from past results only: not financial advice, the next month can look nothing like the "
               "last.")

main()
