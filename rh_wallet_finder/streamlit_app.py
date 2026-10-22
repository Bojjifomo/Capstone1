"""RH Wallet Finder, Streamlit app.

Run it on the Mac that keeps the database (sync runs in the background of
this process and the page refreshes itself):
  python3 finder.py                       (from this folder)
  streamlit run streamlit_app.py          (same thing)
Keys come from .env, the Settings tab, or st.secrets (RPC_URL, CALL_RPC_URL,
LPAGENT_API_KEY).
"""
import base64
import inspect
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

import config  # noqa: E402
import db  # noqa: E402
import lpagent  # noqa: E402
import queries as Q  # noqa: E402

LOGO = HERE / "assets" / "logo.svg"
st.set_page_config(page_title="RH Wallet Finder", page_icon=str(LOGO), layout="wide",
                   initial_sidebar_state="collapsed")

# Works on Streamlit 1.37+ (st.dialog / st.fragment); newer keyword arguments are
# only passed to versions that know them.
ST_VER = tuple(int(x) for x in re.findall(r"\d+", st.__version__)[:2])
if ST_VER < (1, 37):
    st.error(f"Streamlit {st.__version__} is too old for this app. In Terminal run:\n\n"
             "`python3 -m pip install -U -r requirements.txt`\n\nthen start it again.")
    st.stop()


def _accepts(fn, name):
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def stretch(fn):
    """Full-width keyword for fn on this Streamlit: width="stretch" on new
    versions, use_container_width=True on older ones (it's deprecated later)."""
    if ST_VER >= (1, 50) and _accepts(fn, "width"):
        return {"width": "stretch"}
    if _accepts(fn, "use_container_width"):
        return {"use_container_width": True}
    return {}


def col(kind, label, **kw):
    """st.column_config.<kind>(...) minus keywords this Streamlit doesn't have (pinned)."""
    fn = getattr(st.column_config, kind)
    return fn(label, **{k: v for k, v in kw.items() if _accepts(fn, k)})


def auto_height(n_rows, cap):
    """Table height that fits n_rows (old Streamlit only takes a number)."""
    if n_rows > 16:
        return cap
    return "auto" if ST_VER >= (1, 50) else 38 + 35 * max(1, n_rows)

# st.secrets (.streamlit/secrets.toml) feed the same settings as .env. Only
# touched when such a file exists: older Streamlit shows an error box otherwise.
if any(p.exists() for p in (Path.home() / ".streamlit" / "secrets.toml", Path.cwd() / ".streamlit" / "secrets.toml",
                            HERE / ".streamlit" / "secrets.toml")):
    try:
        for _k in Q.SETTING_KEYS:
            if _k in st.secrets and not os.environ.get(_k):
                os.environ[_k] = str(st.secrets[_k])
    except Exception:  # noqa: BLE001 - a broken secrets file shouldn't stop the app
        pass

LIVE_SECONDS = 15
BLUE, RED = "57,135,229", "230,103,103"  # better / worse
STEPS = (0.16, 0.30, 0.44)
VERDICT_BG = {"Elite": f"rgba({BLUE},0.46)", "Strong": f"rgba({BLUE},0.24)", "Decent": "rgba(255,255,255,0.07)",
              "Weak": f"rgba({RED},0.24)", "Avoid": f"rgba({RED},0.46)"}
FOLLOW = {"✓": "✓ yes", "✗": "✗ bot", "?": "? maybe"}


# ---------------------------------------------------------------- look ----
def css():
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root { --gold:#ffc533; --ink:#e8edf5; --ink2:#b4bfcd; --muted:#8592a6; --panel:#0f151f; --panel2:#131b27;
        --line:#1e2938; --line2:#2b3a4f; --pos:#5aa2ff; --neg:#ff7b7b; --warn:#fab219; --ok:#0ca30c; }
.stApp { background:
  radial-gradient(1200px 500px at 15% -10%, rgba(47,107,255,.10), transparent 60%),
  radial-gradient(900px 400px at 100% 0%, rgba(255,197,51,.05), transparent 60%),
  repeating-linear-gradient(0deg, rgba(255,255,255,.018) 0 1px, transparent 1px 4px), #090d14; }
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1960px; padding-top: 1.2rem; padding-bottom: 3rem; }
h1, h2, h3, h4 { font-family: "Chakra Petch", sans-serif !important; letter-spacing: .06em; }
.bar { display:flex; align-items:center; gap:18px; padding: 4px 0 14px; border-bottom: 1px solid var(--line);
       position: relative; margin-bottom: 18px; flex-wrap: wrap; }
.bar::after { content:""; position:absolute; left:0; right:0; bottom:-1px; height:1px;
       background: linear-gradient(90deg, var(--gold) 0, rgba(255,197,51,.25) 240px, transparent 520px); }
.brand { display:flex; align-items:center; gap:12px; }
.brand img { width:46px; height:46px; filter: drop-shadow(0 0 10px rgba(255,197,51,.18)); }
.word { font: 700 22px/1 "Chakra Petch", sans-serif; letter-spacing: .14em; color: var(--ink); }
.word b { color: var(--gold); }
.tag { font: 500 11px "JetBrains Mono", monospace; color: var(--muted); letter-spacing: .06em; margin-top: 5px; text-transform: uppercase; }
.rail { margin-left:auto; display:flex; border:1px solid var(--line2); background: var(--panel); flex-wrap: wrap; }
.rail span.c { display:flex; align-items:center; gap:7px; padding:6px 12px; font: 12px "JetBrains Mono", monospace;
       border-left:1px solid var(--line); white-space:nowrap; color: var(--ink); }
.rail span.c:first-child { border-left:0; }
.rail b { font: 600 10px "Chakra Petch", sans-serif; letter-spacing:.16em; color: var(--muted); }
.led { width:7px; height:7px; display:inline-block; background:#3a4558; transform: rotate(45deg); }
.led.ok { background: var(--ok); box-shadow: 0 0 6px var(--ok); } .led.warn { background: var(--warn); box-shadow: 0 0 6px var(--warn); }
.led.bad { background: var(--neg); } .led.busy { background: var(--pos); box-shadow: 0 0 6px var(--pos); animation: blink 1s infinite; }
@keyframes blink { 50% { opacity:.25; } }
.kpis { display:grid; grid-template-columns: repeat(4, 1fr); gap:14px; margin-bottom: 14px; }
.kpi { position:relative; padding:14px 18px 12px 22px; background: linear-gradient(160deg, var(--panel2), var(--panel));
       clip-path: polygon(14px 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%,0 14px); box-shadow: inset 0 0 0 1px var(--line); }
.kpi::before { content:""; position:absolute; left:0; top:14px; bottom:14px; width:3px; background: var(--gold); }
.kpi::after { content:""; position:absolute; left:0; top:0; width:20px; height:20px; background: linear-gradient(135deg, var(--gold) 0 50%, transparent 50%); }
.kpi .k { font: 600 12px "Chakra Petch", sans-serif; letter-spacing:.22em; text-transform:uppercase; color: var(--ink2); }
.kpi .v { font: 600 34px/1.15 "Chakra Petch", sans-serif; letter-spacing:.04em; color: var(--ink); }
.kpi .s { font-size: 12px; color: var(--muted); }
.notice { padding:10px 16px; border-left:3px solid var(--warn); background: rgba(250,178,25,.08); margin: 4px 0 12px; color: var(--ink); }
.mode { padding:10px 16px; background: rgba(255,197,51,.06); border:1px solid rgba(255,197,51,.25); border-left:3px solid var(--gold); margin: 0 0 10px; font-size: 13.5px; }
.mode b { color: var(--gold); }
button[role="tab"] { background: var(--panel) !important; padding: 8px 18px !important; margin-right: 4px;
       clip-path: polygon(8px 0,100% 0,calc(100% - 8px) 100%,0 100%); }
button[role="tab"] p { font: 600 13px "Chakra Petch", sans-serif !important; letter-spacing: .1em; text-transform: uppercase; }
button[role="tab"][aria-selected="true"] { background: #182232 !important; }
.stButton button, .stDownloadButton button, [data-testid="stFormSubmitButton"] button, [data-testid="stPopover"] button {
       font-family: "JetBrains Mono", monospace; font-size: 12.5px; }
[data-testid="stExpander"] details { background: var(--panel); border: 1px solid var(--line); }
.chips { display:flex; gap:6px; flex-wrap:wrap; margin: 6px 0 4px; }
.chip { border:1px solid var(--line2); background: var(--panel); padding: 3px 10px; font: 500 12px "JetBrains Mono", monospace; color: var(--ink2); }
.tagv { padding: 3px 10px; font: 600 11px "Chakra Petch", sans-serif; letter-spacing: .12em; text-transform: uppercase; color: var(--ink); }
.dk { display:grid; grid-template-columns: repeat(4, 1fr); gap:1px; background: var(--line); border:1px solid var(--line); margin: 8px 0 4px; }
.dk div { background: var(--panel); padding: 8px 11px; }
.dk small { display:block; font: 600 10px "Chakra Petch", sans-serif; letter-spacing:.16em; text-transform:uppercase; color: var(--muted); }
.dk span { font: 15px "JetBrains Mono", monospace; color: var(--ink); }
.kick { font: 600 10.5px "Chakra Petch", sans-serif; letter-spacing:.26em; text-transform:uppercase; color: var(--gold); }
.addr { font: 14px "JetBrains Mono", monospace; color: var(--ink); word-break: break-all; }
.legend { display:flex; align-items:center; gap:8px; flex-wrap:wrap; font: 12px "JetBrains Mono", monospace; color: var(--ink2); margin: 4px 0 12px; }
.legend .sw { width:24px; height:13px; display:inline-block; box-shadow: inset 0 0 0 1px var(--line2); }
.guide h4 { font-size: 11px !important; letter-spacing:.22em; color: var(--gold); text-transform: uppercase; margin: 10px 0 4px; }
.guide p { margin: 0 0 4px; font-size: 13px; color: var(--ink2); }
.guide p b { font-family: "JetBrains Mono", monospace; color: var(--ink); margin-right: 6px; }
.pills { display:flex; gap:6px; flex-wrap:wrap; margin: 2px 0 10px; }
.pills span { border:1px solid var(--line2); background: var(--panel); padding: 3px 10px; font: 12px "JetBrains Mono", monospace; color: var(--muted); }
.pills b { color: var(--ink); font-weight: 600; }
.showing { margin: -2px 0 8px; font: 12px "JetBrains Mono", monospace; color: var(--muted); }
.showing b { color: var(--gold); }
[class*="st-key-fbar"] { background: linear-gradient(160deg, var(--panel2), var(--panel)); border: 1px solid var(--line);
       border-left: 3px solid var(--gold); padding: 10px 14px 4px; margin-bottom: 10px; }
[class*="st-key-fbar"] [data-testid="stVerticalBlock"] { gap: .35rem; }
[class*="st-key-fbar"] [data-testid="stWidgetLabel"] p { font: 600 10px "Chakra Petch", sans-serif !important;
       letter-spacing: .14em; text-transform: uppercase; color: var(--muted) !important; }
[class*="st-key-fbar"] [data-baseweb="select"] > div, [class*="st-key-fbar"] input { min-height: 32px; font-size: 12.5px; }
[class*="st-key-fbar"] [data-testid="stCheckbox"] label p { font: 12px "JetBrains Mono", monospace !important;
       text-transform: none; letter-spacing: 0; color: var(--ink2) !important; }
@media (max-width: 900px) { .kpis { grid-template-columns: 1fr 1fr; } .rail { margin-left: 0; } .dk { grid-template-columns: 1fr 1fr; } }
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
        return f"{v:.0f}"
    return f"{v:.{1 if a >= 10 else 2}f}"


def usd(v):
    v = num(v)
    if v is None:
        return "–"
    return "$0" if abs(v) < 0.005 else ("-$" if v < 0 else "$") + compact(abs(v))


def pct(v, d=0):
    v = num(v)
    if v is None:
        return "–"
    return f"{0 if abs(v) < 0.5 / 10 ** d else v:.{d}f}%"


def f1(v):
    v = num(v)
    return "–" if v is None else ("0.0" if abs(v) < 0.05 else f"{v:.1f}")


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


def ago(ts, now):
    if not ts:
        return "never"
    s = now - ts
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60:.0f}m ago"
    return f"{s // 3600:.0f}h {(s % 3600) // 60:.0f}m ago"


def range_fmt(v):
    v = num(v)
    if v is None:
        return "–"
    return "full" if v >= 1e6 else (compact(v, 0) + "%" if v >= 1e4 else f"{v:.0f}%")


def short(a):
    return f"{a[:6]}…{a[-4:]}"


def pf_fmt(v):
    v = num(v)
    return "–" if v is None else "∞" if v >= 99 else f"{v:.2f}" if v < 10 else f"{v:.0f}"


KIND_LABEL = {"multiday": "Multiday", "degen": "Degen"}
KIND_BLURB = {
    "multiday": "Multiday players: median hold ≥ {h:g} h, scored against each other on total PnL (open positions "
                "count), ROI, green days and drawdown.",
    "degen": "Degen players: median hold under {h:g} h, scored against each other on realized PnL, ROI, profit "
             "factor and win rate. Bots stay hidden unless you tick Show bots.",
}


# ---------------------------------------------------------------- data ----
@st.cache_resource
def background_worker():
    """One sync + scoring loop per Streamlit server, shared by every tab."""
    if os.environ.get("RHWF_NO_WORKER"):
        return None
    from worker import Worker
    w = Worker()
    w.start()
    return w


def conn():
    return db.connect()


# (key, label, group, heat, help); {d} in a label is the window in days
COLS = [
    ("rank", "#", "Wallet", None, "Place on the board, by score."),
    ("score", "Score", "Wallet", "up", "0–100, against wallets of the same class only. Degen: realized PnL, ROI, profit factor, win rate, green days, max drawdown, fees, trade count. Multiday: total PnL (open positions count), ROI, green days, max drawdown, win rate, fees, recovery, trade count. Few trades pull it toward 50; a losing wallet is capped at 45."),
    ("wallet", "Wallet", "Wallet", None, "Select a row to open the wallet dossier: PnL curve, positions, label, LP Agent check. ◆ = a reference wallet of its class."),
    ("kind", "Class", "Wallet", None, "Multiday: its trades' median hold is at least the line in Settings (2 h by default); Degen: below it. A reference wallet keeps the class you gave it."),
    ("verdict", "Tier", "Wallet", "tier", "Elite ≥ 70, Strong ≥ 60, Decent ≥ 50, Weak ≥ 40, else Avoid."),
    ("worth", "Net worth", "Wallet", None, "ETH + USDG in the wallet + what its open positions are worth (other tokens it holds aren't counted)."),
    ("net", "PnL {d}D", "Performance", "signed", "Realized: positions closed in the window, everything taken out + fees − everything put in. A wallet whose older positions are still being priced is measured over part of the window (its dossier says how much)."),
    ("roi_nw", "ROI {d}D", "Performance", "signed", "PnL {d}D ÷ net worth now, the way RH Discovery reads ROI. A wallet that took its profit out reads high."),
    ("net_7d", "PnL 7D", "Performance", "signed", "Realized PnL of positions closed in the last 7 days."),
    ("momentum", "7D/{d}D", "Performance", None, "Share of the window's PnL made in the last 7 days (when that PnL is positive). About 0.23 is an even pace; higher = heating up, near 0 = gone quiet."),
    ("net_1d", "PnL 24h", "Performance", "signed", "Realized PnL of positions closed in the last 24 hours."),
    ("avg_month", "Avg / month", "History", "signed", "PnL ALL ÷ calendar months since its first LP (first LP on 14 Aug, now October = 3 months)."),
    ("pnl_3m", "PnL 3M", "History", "signed", "Realized PnL of positions closed in the last 90 days."),
    ("pnl_all", "PnL ALL", "History", "signed", "Realized PnL of every priced position. For a wallet already active when our data starts it runs from there (the header's History), not from its first LP: load more with Backfill days in Settings."),
    ("roi_all", "ROI ALL", "History", "signed", "PnL ALL ÷ everything those positions ever deposited."),
    ("float", "Open PnL", "Performance", "signed", "PnL of positions still open: current value + unclaimed fees − deposits."),
    ("total", "Total {d}D", "Performance", "signed", "PnL {d}D + open PnL."),
    ("win_pos", "Win", "Performance", "up", "Winning positions out of positions that won or lost, as RH Discovery counts it. A range that ended within ±0.05% of its deposit (one the price never reached) is flat and left out."),
    ("win", "Trade win", "Performance", "up", "The same per trade: what opened in one transaction counts once (a 5-range ladder is one trade), so a ladder with one losing leg can still be a winning trade. The score uses this one."),
    ("pf", "Profit factor", "Performance", "up", "What winning trades made ÷ what losing trades lost. Above 1 makes money; ∞ = no losing trade yet."),
    ("roi_win", "Capital ROI {d}D", "Performance", "signed", "PnL {d}D over the capital it kept deployed in that time (time-weighted, never below its median trade). Unlike ROI {d}D it doesn't change when the wallet moves money out."),
    ("roi", "ROI/mo", "Performance", "signed", "PnL per month over the capital it keeps deployed, measured over the days it has been active: a wallet active for 5 days isn't counted as idle for the other 25."),
    ("daily", "Green days", "Performance", "up", "Share of days with closes that ended in profit."),
    ("fees", "Fees {d}D", "Performance", "up", "LP fees earned (claimed + unclaimed), USD."),
    ("dd", "Max DD", "Risk", "up", "Deepest drop of the realized PnL curve, as % of deployed capital."),
    ("recover", "Recovery", "Risk", "down", "How long the deepest drawdown took to win back (or has lasted so far, if it hasn't)."),
    ("usdg", "USDG %", "Style", None, "Share of its deposits in USDG-paired pools (the rest ETH-paired)."),
    ("size", "Avg size", "Style", None, "Median USD deposited per trade."),
    ("duration", "Hold", "Style", None, "Median time a trade stays open."),
    ("trades", "Trades", "Style", None, "Trades in the window (closed in it, or still open). Ranges opened in one transaction count once."),
    ("style", "Style", "Style", None, "How its trades are built, and the share that fit: Ladder ×N (N ranges opened together) or Single range · bid (only USDG/ETH in: buys the token as price falls into it), ask (only the token in: sells it on the way up) or 2-sided."),
    ("sim", "Like ref", "Style", None, "Closest reference wallet of its class (set in a dossier; the app starts with one of each), and how close its LP style is. Without a reference: the closest starred / labelled wallet."),
    ("range", "Range", "Style", None, "Median range a trade covers (a ladder: lowest to highest price): upper / lower − 1."),
    ("mcap", "Entry mcap", "Style", None, "Median market cap of the token when it opened a trade (supply × pool price then)."),
    ("open", "Open", "Style", None, "Positions open right now."),
    ("concur", "Parallel", "Style", None, "Average number of trades open at the same time."),
    ("copy", "Followable", "Copy-trade", None, "Can a person follow it by hand? yes: median hold ≥ 30 min and ≤ 30 trades/day. bot: hold < 5 min or > 100 trades/day."),
    ("copiers", "Copiers", "Copy-trade", None, "Other wallets that opened the same pool with the exact same range within 60 s after this wallet, at least twice."),
    ("exposure", "Crowding", "Copy-trade", None, "Liquidity copiers add per open, as a share of the wallet's own. ⚠ from 50%: following it means sharing the range."),
    ("slf", "Smart LP", "Copy-trade", None, "Best place this wallet reached in an RH Smart LP Finder pool ranking you ran (rank/wallets · pool)."),
    ("open_now", "Deployed", "Holdings", None, "USD sitting in open positions now."),
    ("age", "Seen", "Holdings", None, "Since its first LP in our data (it may be older than our history)."),
    ("last", "Active", "Holdings", None, "Since its last LP action."),
    ("star", "★", "Links", None, "On your watchlist."),
    ("lp", "LP Agent", "Links", None, "The wallet on LP Agent."),
    ("scan", "Explorer", "Links", None, "The wallet on the Robinhood Chain explorer."),
]
SHORT_WINDOW_ONLY = ("net_7d", "momentum", "net_1d")  # meaningless on the 24 h board
NUM_KEYS = ["score", "net", "float", "total", "roi", "win", "win_pos", "pf", "daily", "fees", "dd", "recover", "copiers",
            "duration", "range", "size", "usdg", "mcap", "trades", "open", "concur", "open_now", "worth", "net_7d",
            "net_1d", "momentum", "roi_win", "roi_nw", "closed_trades", "pnl_all", "pnl_3m", "avg_month", "roi_all"]


def frame(rows, now, data_from):
    """wallet rows -> DataFrame indexed by address, one column per COLS key."""
    if not rows:
        return pd.DataFrame(columns=[c[0] for c in COLS])
    recs = []
    for r in rows:
        slf = r.get("slf")
        recs.append({
            **{k: r.get(k) for k in ("rank", "verdict", "copy", *NUM_KEYS)},
            "addr": r["addr"], "label": r.get("label"), "starred": r.get("starred"),
            "kind": KIND_LABEL.get(r.get("kind"), "–"),
            "wallet": ("◆ " if r.get("ref") else "") + short(r["addr"]) + (f"  {r['label']}" if r.get("label") else ""),
            "slf_rank": slf["rank"] if slf else None,
            "slf": (f"#{slf['rank']}/{slf['total']} · {slf.get('pair') or short(slf['pool'])}"
                    + (f" +{slf['pools'] - 1}" if slf.get("pools", 0) > 1 else "")) if slf else "–",
            "exposure": (r.get("exposure") or 0) * 100, "exposure_usd": r.get("exposure_usd"),
            "recover_open": r.get("recover_open"),
            "style": f"{r['style']} {r.get('style_conf')}%" if r.get("style") else "–",
            "sim": (f"{r['sim_label']} {r['sim_pct']}%" + ("" if r.get("sim_ref") else " ·")) if r.get("sim_label") else "–",
            "age": now - r["first_seen"] if r.get("first_seen") else None,
            "last": now - r["last_seen"] if r.get("last_seen") else None,
            "star": "★" if r.get("starred") else "",
            "lp": lpagent.link(r["addr"]), "scan": f"{config.EXPLORER}/address/{r['addr']}",
        })
    df = pd.DataFrame(recs).set_index("addr", drop=False)
    nums = NUM_KEYS + ["age", "last", "exposure"]
    df[nums] = df[nums].apply(pd.to_numeric, errors="coerce")  # a missing figure is NaN ("–"), never "None"
    return df


def heat(df):
    """CSS per cell: blue better, red worse, middle uncoloured (equal steps per arm)."""
    out = pd.DataFrame("", index=df.index, columns=df.columns)
    for k, _, _, mode, _ in COLS:
        if k not in df.columns or not mode:
            continue
        if mode == "tier":
            out[k] = df[k].map(lambda v: f"background-color: {VERDICT_BG[v]}" if v in VERDICT_BG else "")
            continue
        vals = pd.to_numeric(df[k], errors="coerce")
        if mode == "signed":
            pos = vals[vals > 0].sort_values().to_numpy()
            neg = (-vals[vals < 0]).sort_values().to_numpy()
            css = []
            for v in vals:
                if pd.isna(v) or abs(v) < 0.005:
                    css.append("")
                    continue
                ref = pos if v > 0 else neg
                q = (ref < abs(v)).sum() / (len(ref) - 1) if len(ref) > 1 else 1
                a = STEPS[0 if q < 0.34 else 1 if q < 0.67 else 2]
                css.append(f"background-color: rgba({BLUE if v > 0 else RED},{a})")
            out[k] = css
            continue
        valid = vals.dropna()
        if valid.nunique() < 2:
            continue
        p = (vals.rank(method="average") - 1) / (valid.size - 1)
        if mode == "down":
            p = 1 - p
        css = []
        for x in p:
            if pd.isna(x) or abs(x - 0.5) < 0.12:
                css.append("")
                continue
            d = abs(x - 0.5)
            a = STEPS[0 if d < 0.25 else 1 if d < 0.38 else 2]
            css.append(f"background-color: rgba({BLUE if x > 0.5 else RED},{a})")
        out[k] = css
    out["copy"] = df["copy"].map({"✓": "color: #5aa2ff", "✗": "color: #ff7b7b"}).fillna("")
    out["exposure"] = df["exposure"].map(lambda v: "color: #fab219" if num(v) and v >= 50 else "")
    return out


def formatters():
    return {
        "rank": lambda v: f"{v:.0f}", "score": f1, "net": usd, "float": usd, "total": usd, "net_7d": usd, "net_1d": usd,
        "roi_win": lambda v: pct(v, 1), "roi_nw": lambda v: pct(v, 1), "momentum": lambda v: f"{v:.2f}", "usdg": pct,
        "pnl_all": usd, "pnl_3m": usd, "avg_month": usd, "roi_all": lambda v: pct(v, 1),
        "roi": lambda v: pct(v, 1), "win": pct, "win_pos": pct, "pf": pf_fmt, "daily": pct, "fees": usd, "dd": lambda v: pct(v, 1),
        "recover": lambda v: dur(v) if num(v) else "–",
        "copy": lambda v: FOLLOW.get(v, "–"), "copiers": lambda v: f"{v:.0f}" if num(v) is not None else "–",
        "exposure": lambda v: f"⚠ {v:.0f}%" if num(v) and v >= 50 else f"{num(v) or 0:.0f}%",
        "duration": dur, "range": range_fmt, "size": usd, "mcap": usd,
        "trades": lambda v: f"{v:.0f}", "open": lambda v: f"{v:.0f}", "concur": lambda v: f"{v:.1f}×" if num(v) is not None else "–",
        "open_now": usd, "worth": usd, "age": dur, "last": lambda v: dur(v) + " ago" if num(v) is not None else "–",
    }


def column_config(keys, days):
    """Column titles + help; {d} in a title is the window in days."""
    cfg = {}
    for k, label, group, _, helptext in COLS:
        if k not in keys:
            continue
        label = label.format(d=f"{days:g}")
        if k in ("lp", "scan"):
            cfg[k] = col("LinkColumn", label, help=helptext, display_text="open ↗", width="small")
        else:
            width = "small" if k in ("rank", "star", "copiers", "open", "trades", "momentum", "usdg") else \
                "medium" if k in ("slf", "wallet", "style", "sim") else None
            cfg[k] = col("Column", label, help=f"{group} · {helptext}", pinned=k in ("rank", "score", "wallet"),
                         width=width)
    return cfg


def styled(df, css_full):
    sty = df.style.apply(lambda _: css_full.loc[df.index, df.columns], axis=None)
    return sty.format({k: v for k, v in formatters().items() if k in df.columns}, na_rep="–")


# ------------------------------------------------------------- filters ----
# Always-visible filter bar, one per board (session keys carry the board's key).
# Each select: (key, label, options, how an option reads, help); ANY = no limit.
ANY = "any"


def _money(v):
    return f"${v / 1000:g}k" if v >= 1000 else f"${v:g}"


SELECTS = [
    ("f_closed", "Min closed", [ANY, 3, 10, 30, 100], lambda v: f"{v} trades",
     "Closed trades in the window (ranges opened together count once)."),
    ("f_active", "Active within", [ANY, 1, 3, 7], lambda v: "24h" if v == 1 else f"{v}d",
     "Last LP action (open, add, remove, close) this recent. Wallets idle past Settings' limit are never ranked."),
    ("f_size_min", "Min avg size", [ANY, 100, 300, 1000, 5000], _money, "Median USD per trade at least this."),
    ("f_size_max", "Max avg size", [ANY, 1000, 5000, 20000, 100000], _money, "Median USD per trade at most this."),
    ("f_worth", "Min net worth", [ANY, 1000, 10000, 100000], _money, "ETH + USDG in the wallet + open positions."),
    ("f_roi", "Min ROI {d}D", [ANY, 0, 10, 25, 50, 100], lambda v: f"{v}%", "PnL over the window ÷ net worth now."),
    ("f_mom", "7D/{d}D at least", [ANY, 0.1, 0.25, 0.5], lambda v: f"{v:g}",
     "Share of the window's PnL made in the last 7 days: still making money now, not just weeks ago."),
    ("f_usdg", "Min USDG %", [ANY, 50, 90, 100], lambda v: f"{v}%", "Share of deposits in USDG-paired pools."),
    ("f_win", "Min win", [ANY, 50, 60, 70, 80, 90], lambda v: f"{v}%", "Winning positions out of decided ones."),
    ("f_float", "Max open loss", [ANY, 100, 1000, 10000], lambda v: "-" + _money(v), "Open PnL no worse than this."),
    ("f_dd", "Max drawdown", [ANY, 5, 10, 25, 50], lambda v: f"-{v}%", "Deepest drop no worse than this."),
    ("f_tier", "Min tier", [ANY, "Elite", "Strong", "Decent"], lambda v: f"{v}+" if v != "Elite" else v, ""),
    ("f_follow", "Followable", [ANY, "yes", "yes or maybe"], str, "Hand-followable pace (see the Followable column)."),
    ("f_style", "Style", [ANY, "ladder", "single", "bid", "ask", "2-sided"], str, "Words in the Style column."),
]
CHECKS = [
    ("f_p30", "{d}D profit only", "PnL over the window above zero."),
    ("f_p7", "7D profit only", "PnL of the last 7 days above zero."),
    ("f_usdg_only", "USDG pairs only", "Every deposit in USDG-paired pools."),
    ("f_watch", "Watchlist only", "Starred wallets."),
    ("f_slf", "Smart LP top 10", "Placed top 10 in an RH Smart LP Finder pool ranking."),
    ("f_bots", "Show bots", "Hold < 5 min or > 100 trades a day: nobody can follow those."),
]
FILTER_DEFAULTS = {**{k: ANY for k, *_ in SELECTS}, **{k: False for k, *_ in CHECKS}}
TIERS = ["Elite", "Strong", "Decent", "Weak", "Avoid"]
BUILTIN = {
    "All wallets": {},
    "Proven earners": {"f_closed": 30, "f_active": 7, "f_size_min": 300, "f_worth": 1000, "f_p30": True,
                       "f_usdg_only": True},
    "Hot this week": {"f_active": 3, "f_p7": True, "f_mom": 0.25},
    "Followable winners": {"f_follow": "yes", "f_p30": True, "f_win": 60},
    "Low drawdown": {"f_p30": True, "f_dd": 10},
    "Size $1k+": {"f_size_min": 1000},
    "Ladders": {"f_style": "ladder"},
    "Smart LP top 10": {"f_slf": True},
    "Watchlist only": {"f_watch": True},
}


def fk(board, name):
    return f"{board}_{name}"


def apply_preset(board):
    name = st.session_state.get(fk(board, "preset"))
    con = conn()
    saved = {p["name"]: p["filters"] for p in Q.presets(con)}
    con.close()
    values = BUILTIN.get(name, saved.get(name, {}))
    for k, v in FILTER_DEFAULTS.items():
        got = values.get(k, v)
        options = next((o for key, _, o, *_ in SELECTS if key == k), None)
        st.session_state[fk(board, k)] = got if options is None or got in options else v


def clear_filters(board):
    for k, v in FILTER_DEFAULTS.items():
        st.session_state[fk(board, k)] = v
    st.session_state[fk(board, "quick")] = ""
    st.session_state[fk(board, "preset")] = "All wallets"


def filter_controls(board, saved_names, days):
    """The filter bar; returns the slot for the 'showing x of y' line."""
    k = lambda name: fk(board, name)  # noqa: E731
    for name, v in FILTER_DEFAULTS.items():
        st.session_state.setdefault(k(name), v)
    box = st.container(**({"key": f"fbar_{board}"} if _accepts(st.container, "key") else {}))
    with box:
        row1 = st.columns([2.4] + [1] * 7)
        row1[0].text_input("Search", key=k("quick"), placeholder="Address or label…")
        row2 = st.columns(8)
        for (key, label, options, fmt, helptext), slot in zip(SELECTS, row1[1:] + row2[:7]):
            slot.selectbox(label.format(d=f"{days:g}"), options, key=k(key), help=helptext or None,
                           format_func=lambda v, fmt=fmt: v if v == ANY else fmt(v))
        row2[7].selectbox("Preset", list(BUILTIN) + saved_names, key=k("preset"), on_change=apply_preset,
                          args=(board,))
        row3 = st.columns([1.25] * len(CHECKS) + [1.1, 1, 1])
        for (key, label, helptext), slot in zip(CHECKS, row3):
            slot.checkbox(label.format(d=f"{days:g}"), key=k(key))
        with row3[len(CHECKS) + 1].popover("Save preset", **stretch(st.popover)):
            name = st.text_input("Name", placeholder="preset name", key=k("preset_name"))
            if st.button("Save", disabled=not name, key=k("save_preset")):
                con = conn()
                items = [p for p in Q.presets(con) if p["name"] != name]
                items.append({"name": name, "filters": {f: st.session_state[k(f)] for f in FILTER_DEFAULTS}})
                Q.save_presets(con, items)
                con.close()
                st.toast(f"Saved preset “{name}”")
            if st.session_state.get(k("preset")) in saved_names and st.button("Delete this preset",
                                                                                key=k("del_preset")):
                con = conn()
                Q.save_presets(con, [p for p in Q.presets(con) if p["name"] != st.session_state[k("preset")]])
                con.close()
        row3[len(CHECKS) + 2].button("Clear filters", key=k("clear"), on_click=clear_filters, args=(board,),
                                     **stretch(st.button))
    return st.empty()


def apply_filters(df, board):
    s = {name: st.session_state.get(fk(board, name), v) for name, v in {**FILTER_DEFAULTS, "quick": ""}.items()}
    s = {name: None if v == ANY else v for name, v in s.items()}
    m = pd.Series(True, index=df.index)
    if s["quick"]:
        q = s["quick"].strip().lower()
        m &= df["addr"].str.contains(q, regex=False) | df["label"].fillna("").str.lower().str.contains(q, regex=False)
    if not s["f_bots"]:
        m &= df["copy"] != "✗"
    at_least = {"f_closed": "closed_trades", "f_size_min": "size", "f_worth": "worth", "f_roi": "roi_nw",
                "f_mom": "momentum", "f_usdg": "usdg", "f_win": "win_pos"}
    for f, c in at_least.items():
        if s[f] is not None:
            m &= df[c] >= s[f]
    if s["f_active"] is not None:
        m &= df["last"] <= s["f_active"] * 86400
    if s["f_size_max"] is not None:
        m &= df["size"] <= s["f_size_max"]
    if s["f_float"] is not None:
        m &= df["float"].fillna(0) >= -s["f_float"]
    if s["f_dd"] is not None:
        m &= df["dd"] >= -s["f_dd"]
    if s["f_tier"]:
        m &= df["verdict"].isin(TIERS[:TIERS.index(s["f_tier"]) + 1])
    if s["f_follow"]:
        m &= df["copy"].isin(["✓"] if s["f_follow"] == "yes" else ["✓", "?"])
    if s["f_style"]:
        m &= df["style"].str.lower().str.contains(s["f_style"], regex=False)
    if s["f_p30"]:
        m &= df["net"] > 0
    if s["f_p7"]:
        m &= df["net_7d"] > 0
    if s["f_usdg_only"]:
        m &= df["usdg"] >= 99.95
    if s["f_watch"]:
        m &= df["starred"].fillna(False).astype(bool)
    if s["f_slf"]:
        m &= df["slf_rank"].notna() & (df["slf_rank"] <= 10)
    return df[m]


# -------------------------------------------------------------- header ----
def header(stt, w):
    logo = base64.b64encode(LOGO.read_bytes()).decode()
    now = stt["now"]
    hist = (now - stt["data_from"]) / 86400 if stt["data_from"] else 0
    hist_txt = (dur(hist * 86400) if hist < 1 else f"{hist:.{1 if hist < 3 else 0}f}d") + f"/{stt['backfill_days']:.0f}d" \
        if stt["data_from"] else "–"
    led_sync = "" if not stt["synced_at"] else "ok" if now - stt["synced_at"] < 1800 else "warn"
    led_rank = "" if not stt["scored_at"] else "ok" if now - stt["scored_at"] < 3 * 3600 else "warn"
    state = (w.state if w else "off")
    led_w = "bad" if (w and w.error) else "busy" if state in ("syncing", "scoring") else "ok" if stt["has_rpc"] else "warn"
    eth = f"${stt['eth_usd']:,.0f}" if stt["eth_usd"] else "–"
    slf = stt["smart_lp"]
    slf_cell = f'<span class="c"><b>SMART LP</b><a href="{slf["url"]}" target="_blank" style="color:#5aa2ff">open ↗</a></span>' \
        if slf.get("url") else ""
    st.markdown(f"""
<div class="bar">
  <div class="brand"><img src="data:image/svg+xml;base64,{logo}">
    <div><div class="word"><b>RH</b> WALLET FINDER</div><div class="tag">LP scanner // Robinhood Chain · Uniswap V4</div></div></div>
  <div class="rail">
    <span class="c"><i class="led {led_w}"></i><b>WORKER</b>{state}</span>
    <span class="c"><i class="led {led_sync}"></i><b>SYNC</b>{ago(stt['synced_at'], now)}</span>
    <span class="c"><i class="led {led_rank}"></i><b>RANK</b>{ago(stt['scored_at'], now)}</span>
    <span class="c"><b>HISTORY</b>{hist_txt}</span>
    <span class="c"><b>WINDOW</b>{stt['metric_days']:.0f}d</span>
    <span class="c"><b>ETH</b>{eth}</span>{slf_cell}
  </div>
</div>
<div class="kpis">
  <div class="kpi"><div class="k">Wallets</div><div class="v">{stt['discovered']:,}</div><div class="s">LP wallets in the database · {stt['scored']:,} ranked</div></div>
  <div class="kpi"><div class="k">Multiday</div><div class="v">{stt['kinds'].get('multiday', 0):,}</div><div class="s">ranked, median hold ≥ {stt['multiday_hours']:g} h</div></div>
  <div class="kpi"><div class="k">Degen</div><div class="v">{stt['kinds'].get('degen', 0):,}</div><div class="s">ranked, median hold &lt; {stt['multiday_hours']:g} h</div></div>
  <div class="kpi"><div class="k">Positions</div><div class="v">{stt['positions']:,}</div><div class="s">V4 position NFTs indexed</div></div>
</div>""", unsafe_allow_html=True)
    if not stt["has_rpc"]:
        st.markdown('<div class="notice">No RPC yet. Open the <b>Settings</b> tab and paste your Robinhood Chain RPC URL '
                    '(your SolidRPC link works). The first sync starts right after.</div>', unsafe_allow_html=True)
    elif w and w.error:
        st.markdown(f'<div class="notice">Worker error: {w.error}</div>', unsafe_allow_html=True)
    elif w and getattr(w, "notice", None):
        st.markdown(f'<div class="notice">{w.notice}</div>', unsafe_allow_html=True)
    if stt["scored"] and stt["rank_version"] != stt["rank_current"]:
        st.markdown('<div class="notice">These rankings were made by an older version of the app, so some figures '
                    'are off and every wallet sits in one class. The worker re-ranks with the current rules right '
                    'after it starts. If this note stays, the old worker is still running: close the Terminal window '
                    'and open <b>Buka Wallet Finder.command</b> again.</div>', unsafe_allow_html=True)
    elif not stt["scored"] and not stt["discovered"]:
        st.markdown('<div class="notice">First sync running: scanning Uniswap V4 LP activity from the newest blocks '
                    'backwards. Wallets appear after the first ranking pass.</div>', unsafe_allow_html=True)


@st.fragment(run_every=LIVE_SECONDS)
def live_header():
    con = conn()
    stt = Q.status(con)
    con.close()
    first = "status" not in st.session_state
    changed = not first and (stt["scored_at"], stt["discovered"]) != (
        st.session_state["status"]["scored_at"], st.session_state["status"]["discovered"])
    st.session_state["status"] = stt
    header(stt, background_worker())
    if changed:  # new ranking landed: redraw the tables too
        st.rerun()


# --------------------------------------------------------------- board ----
def board(view, key, kind=None, top=None):
    con = conn()
    stt = Q.status(con)
    rows, _ = Q.wallet_rows(con, view, kind)
    saved_names = [p["name"] for p in Q.presets(con)]
    con.close()
    days = 1 if view.startswith("d1") else stt["metric_days"]
    if kind:
        hist = (stt["now"] - stt["data_from"]) / 86400 if stt["data_from"] else 0
        cut = stt["data_from"] and stt["data_from"] > stt["launch"] + 86400
        st.caption(KIND_BLURB[kind].format(h=stt["multiday_hours"]) + f" Figures cover the last {days:g} days"
                   + (f" ({hist:.1f} loaded so far)" if hist < days - 0.5 else "")
                   + f"; ranked: {stt['settings']['MIN_CLOSED']}+ closed positions in them"
                   + (f" and an LP action within {stt['active_days']:g} days." if stt["active_days"] else ".")
                   + (f" PnL ALL / 3M / Avg per month see {hist:.0f} days back (since "
                      f"{time.strftime('%d %b', time.gmtime(stt['data_from']))}), not to the chain's launch: "
                      f"History to load in Settings." if cut else ""))
    df = frame(rows, stt["now"], stt["data_from"])
    what = f"{KIND_LABEL[kind].lower()} " if kind else ""
    pills = [f"<b>{len(df):,}</b> ranked {what}", f"<b>{stt['rated']:,}</b> rated of <b>{stt['discovered']:,}</b> found",
             f"<b>{stt['queue']:,}</b> positions in queue", f"ranked <b>{ago(stt['scored_at'], stt['now'])}</b>"]
    if stt.get("history_queue"):
        pills.insert(3, f"<b>{stt['history_queue']:,}</b> older ones for PnL ALL")
    st.markdown('<div class="pills">' + "".join(f"<span>{x}</span>" for x in pills) + "</div>", unsafe_allow_html=True)
    count_slot = filter_controls(key, saved_names, days)
    if df.empty:
        st.info("No wallet closed a priced position in the last 24 h yet." if top else
                f"No ranked {what}wallets yet. They appear after the first sync + ranking pass (see the Settings tab "
                "log).")
        return
    css_full = heat(df)
    shown = apply_filters(df, key)
    if top:
        shown = shown.head(top)
    hidden = len(df) - len(shown)
    count_slot.markdown(f"<div class='showing'>Showing <b>{len(shown):,}</b> of {len(df):,}"
                        + (f" · {hidden:,} hidden by filters" if hidden else "") + "</div>", unsafe_allow_html=True)
    shown = shown.head(1500)
    keys = [c[0] for c in COLS if not (kind and c[0] == "kind") and not (days == 1 and c[0] in SHORT_WINDOW_ONLY)]
    event = st.dataframe(styled(shown[keys], css_full), column_config=column_config(keys, days), hide_index=True,
                         **stretch(st.dataframe),
                         height=auto_height(len(shown), 620), on_select="rerun", selection_mode="single-row",
                         key=f"grid_{key}")
    sel = getattr(getattr(event, "selection", None), "rows", None) or []
    picked = shown.iloc[sel[0]]["addr"] if sel else None
    # the same dossier from a dropdown too: works on every Streamlit version and without a mouse
    names = {a: f"#{int(r)} {short(a)}{'  ' + lab if lab else ''}"
             for a, r, lab in zip(shown["addr"], shown["rank"], shown["label"].fillna(""))}
    c1, c2 = st.columns([3, 1])
    choice = c1.selectbox("Open wallet dossier", [""] + list(names), format_func=lambda a: names.get(a, "Open a wallet "
                          "dossier… (or tick a row above)"), key=f"pick_{key}", label_visibility="collapsed")
    addr = picked or choice or None
    again = c2.button("Open dossier", key=f"open_{key}", disabled=not addr)
    if addr and (again or st.session_state.get(f"shown_{key}") != addr):
        st.session_state[f"shown_{key}"] = addr
        dossier(addr)
    elif not addr:
        st.session_state.pop(f"shown_{key}", None)
    st.caption("Tick a row (or pick one in the list) to open its wallet dossier. Hover a column title for what it means.")


@st.fragment
def live_board(view, key, kind=None, top=None):
    board(view, key, kind, top)


# ------------------------------------------------------------- dossier ----
@st.dialog("Wallet dossier", width="large")
def dossier(addr):
    con = conn()
    d = Q.wallet_detail(con, addr)
    stt = Q.status(con)
    now = stt["now"]
    real = st.session_state.get("real", False)
    m = (d["metrics"] or {}).get("real" if real else "raw")
    st.markdown(f'<div class="addr">{addr}</div>', unsafe_allow_html=True)
    chips = []
    lab = d["label"] or {}
    if m:
        chips.append(f'<span class="tagv" style="background:{VERDICT_BG.get(m["verdict"], "")}">{m["verdict"]}</span>')
        if m.get("kind"):
            fit = "reference" if m.get("kind_set") else f"{m.get('kind_fit', 0)}% of trades fit"
            chips.append(f'<span class="chip" style="color:#ffc533">{KIND_LABEL[m["kind"]]} player · {fit}</span>')
        if m.get("style"):
            chips.append(f'<span class="chip">{m["style"]}</span>')
        chips.append(f'<span class="chip">{FOLLOW.get(m.get("copy"), "–")}</span>')
        if m.get("copiers"):
            chips.append(f'<span class="chip" style="color:#fab219">⚠ {m["copiers"]} copier(s)</span>')
    else:
        chips.append('<span class="chip">not ranked yet</span>')
    st.markdown(f'<div class="chips">{"".join(chips)}</div>'
                f'<div class="chips"><a class="chip" href="{d["lpagent_link"]}" target="_blank">LP Agent ↗</a>'
                f'<a class="chip" href="{d["explorer"]}" target="_blank">Explorer ↗</a></div>', unsafe_allow_html=True)

    refs = {"": "Not a reference", "multiday": "Multiday reference", "degen": "Degen reference"}
    with st.form(f"label_{addr}", border=False):
        a, b, r, c, e = st.columns([2, 2.6, 2, 1, 1])
        label = a.text_input("Label", value=lab.get("label") or "", placeholder="e.g. Cactus")
        notes = b.text_input("Notes", value=lab.get("notes") or "")
        ref = r.selectbox("Reference", list(refs), format_func=refs.get, index=list(refs).index(lab.get("ref") or ""),
                          help="A reference wallet stands for its class: every wallet of that class shows how close "
                               "it plays to it (Like ref), and the reference keeps this class.")
        star = c.checkbox("★ Watch", value=bool(lab.get("starred")))
        if e.form_submit_button("Save"):
            Q.set_label(con, addr, label.strip() or None, notes.strip() or None, star, ref=ref)
            st.toast("Saved" + (" · its class applies from the next ranking pass" if ref != (lab.get("ref") or "")
                                else ""))

    if m:
        recover = "–" if not m.get("recover") and not m.get("recover_open") else \
            (">" if m.get("recover_open") else "") + dur(m["recover"])
        trades = m.get("trades", m.get("pos"))
        days = f"{stt['metric_days']:g}D"
        mom = m.get("momentum")
        since = f" (since {time.strftime('%d %b', time.gmtime(m['hist_from']))})" if m.get("hist_from") else ""
        cells = [("Score", f1(m["score"])), ("Net worth", usd(m.get("worth"))), (f"PnL {days}", usd(m["net"])),
                 (f"ROI {days}", pct(m.get("roi_nw"), 1)), (f"Capital ROI {days}", pct(m.get("roi_win"), 1)),
                 ("ROI/mo", pct(m["roi"], 1)),
                 ("PnL 7D", usd(m.get("net_7d"))), (f"7D/{days}", "–" if mom is None else f"{mom:.2f}"),
                 ("PnL 24h", usd(m.get("net_1d"))), ("Open PnL", usd(m["float"])),
                 (f"Total {days}", usd(m.get("total", m["net"] + m["float"]))),
                 ("Avg / month", usd(m.get("avg_month"))), ("PnL 3M", usd(m.get("pnl_3m"))),
                 ("PnL ALL" + since, usd(m.get("pnl_all"))), ("ROI ALL", pct(m.get("roi_all"), 1)),
                 ("Win", pct(m.get("win_pos"))), ("Trade win", pct(m["win"])),
                 ("Profit factor", pf_fmt(m.get("pf"))), ("Green days", pct(m["daily"])),
                 ("Max DD", pct(m["dd"], 1)), ("Recovery", recover), (f"Fees {days}", usd(m["fees"])),
                 ("Avg size", usd(m["size"])), ("Hold", dur(m["duration"])), ("Range", range_fmt(m["range"])),
                 ("Trades", f"{trades} · {m.get('closed_trades', m['closed'])} closed"),
                 ("Flat trades", pct(m.get("flat"))), ("Open", f"{m['open']} positions"),
                 ("Deployed", usd(m["open_now"])), ("USDG %", pct(m.get("usdg"))),
                 ("Entry mcap", usd(m.get("mcap")))]
        st.markdown('<div class="dk">' + "".join(f"<div><small>{k}</small><span>{v}</span></div>" for k, v in cells)
                    + "</div>", unsafe_allow_html=True)
        if m.get("priced_from"):
            st.caption(f"PnL {days} covers only its last {(now - m['priced_from']) / 86400:.1f} days so far: its "
                       "older positions in the window are still in the pricing queue.")
        elif m.get("span_days") is not None and m["span_days"] < stt["metric_days"] - 0.5:
            st.caption(f"Measured over its last {m['span_days']:.1f} days: it has been active that long.")
        if m.get("hist_gap"):
            st.caption(f"PnL ALL leaves out {m['hist_gap']:,} older positions not priced yet.")

    pos = pd.DataFrame(d["positions"])
    if not pos.empty:
        pos["pnl"] = pos["pnl_usd"] - (pos["cost_usd"].fillna(0) if real else 0)
        closed = pos[pos["close_ts"].notna() & (pos["minted"] == 1) & (pos["valued"] == 1) & (pos["launch"] == 0)
                     & pos["pnl"].notna()].sort_values("close_ts")
        st.markdown(f"#### Realized PnL over time{' · real' if real else ''}")
        if len(closed) >= 2:
            closed = closed.assign(total=closed["pnl"].cumsum(), when=pd.to_datetime(closed["close_ts"], unit="s"))
            base = alt.Chart(closed)
            line = base.mark_line(interpolate="step-after", strokeWidth=2, color="#3987e5").encode(
                x=alt.X("when:T", title=None), y=alt.Y("total:Q", title="USD"),
                tooltip=[alt.Tooltip("when:T", title="closed"), alt.Tooltip("pair:N", title="pool"),
                         alt.Tooltip("pnl:Q", title="this close", format="$,.2f"),
                         alt.Tooltip("total:Q", title="total", format="$,.2f")])
            dots = base.mark_circle(size=60, color="#3987e5").encode(x="when:T", y="total:Q",
                                                                    tooltip=line.encoding.tooltip)
            zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#2b3a4f", strokeDash=[3, 3]).encode(y="y:Q")
            st.altair_chart((zero + line + dots).properties(height=200).configure_view(strokeWidth=0)
                            .configure_axis(gridColor="#1e2938", labelColor="#8592a6", domainColor="#2b3a4f"),
                            **stretch(st.altair_chart))
        else:
            st.caption("Needs at least two closed, priced positions.")

    if d.get("smart_lp"):
        st.markdown("#### In RH Smart LP Finder rankings")
        sl = pd.DataFrame(d["smart_lp"])
        sl["Pool"] = sl.apply(lambda r: r["pair"] or short(r["pool"]), axis=1)
        sl["Rank"] = sl.apply(lambda r: f"#{r['rank']}/{r['total']}", axis=1)
        sl["Window"] = ((sl["to_block"] - sl["from_block"]) * 0.1008).map(dur)
        sl["Scanned"] = (now - sl["computed_at"]).map(lambda s: dur(s) + " ago")
        st.dataframe(sl[["Pool", "Rank", "pnl_usd", "pnl_pct", "deposited_usd", "positions", "Window", "Scanned"]],
                     hide_index=True, **stretch(st.dataframe), column_config={
                         "pnl_usd": st.column_config.NumberColumn("PnL", format="$%.2f"),
                         "pnl_pct": st.column_config.NumberColumn("PnL %", format="%.1f%%"),
                         "deposited_usd": st.column_config.NumberColumn("Deposited", format="$%.0f"),
                         "positions": st.column_config.NumberColumn("Positions")})

    st.markdown("#### LP Agent check")
    key = config.load_env()["LPAGENT_API_KEY"]
    if not key:
        st.caption(f"No LP Agent API key yet, so only the [portfolio link]({d['lpagent_link']}) works. "
                   "Add a key in the Settings tab to compare their numbers with ours.")
    elif st.button("Check now", key=f"lpa_{addr}"):
        with st.spinner("Asking LP Agent…"):
            try:
                res = lpagent.check(con, addr, key, fresh=True)
            except lpagent.LPAgentError as e:
                res = {"errors": [str(e)]}
        for e in res.get("errors") or []:
            st.error(e)
        for proto, vals in (res.get("overview") or {}).items():
            ours = {"total_pnl": usd((m or {}).get("net", 0) + (m or {}).get("float", 0)) if m else "",
                    "win_rate": pct(m["win"]) if m else "", "total_fee": usd(m["fees"]) if m else "",
                    "closed_lp": str(m["closed"]) if m else ""}
            rows = []
            for f, label, kind in res["fields"]:
                v = vals.get(f)
                shown = "–" if v is None else usd(v) if kind == "usd" else \
                    pct(v * 100 if v <= 1 else v) if kind == "ratio" else f"{v:.0f}"
                rows.append({"Metric": label, "LP Agent": shown, f"This app ({stt['metric_days']:.0f}d)": ours.get(f, "")})
            st.markdown(f"**{proto}**")
            st.dataframe(pd.DataFrame(rows), hide_index=True, **stretch(st.dataframe))
        if res.get("opening"):
            st.markdown(f"**Open on LP Agent ({len(res['opening'])})**")
            st.dataframe(pd.DataFrame(res["opening"]), hide_index=True, **stretch(st.dataframe))
        if not res.get("overview") and not res.get("opening") and not res.get("errors"):
            st.caption("LP Agent has nothing on this wallet.")

    if not pos.empty:
        st.markdown(f"#### Positions ({len(pos)}{'+, newest' if len(pos) >= 300 else ''})")

        def status_of(r):
            if not r["minted"]:
                return "opened before history"
            if r["valued"] == 2:
                return "not priceable"
            if r["launch"]:
                return "token launch · not scored"
            return ("single-sided" if r["single"] else "two-sided") if r["valued"] == 1 else "not priced yet"
        legs = pos.groupby(["pool", "open_block"])["token_id"].transform("size")
        view = pd.DataFrame({
            "Pair": pos["pair"], "Opened": (now - pos["open_ts"]).map(lambda s: dur(s) + " ago"),
            "Trade": legs.map(lambda n: f"ladder ×{n}" if n > 1 else "single"),
            "Hold": pos.apply(lambda r: dur((r["close_ts"] if pd.notna(r["close_ts"]) else now) - r["open_ts"])
                              + ("" if pd.notna(r["close_ts"]) else " · open"), axis=1),
            "Range": pos["range"].map(range_fmt), "In": pos["dep_usd"],
            "Out": pos.apply(lambda r: (r["wd_usd"] or 0) + (r["now_usd"] or 0) if r["valued"] == 1 else None, axis=1),
            "Fees": pos["fee_usd"], "PnL": pos["pnl"], "Status": pos.apply(status_of, axis=1),
            "NFT": pos["token_id"].map(lambda t: f"{config.EXPLORER}/token/{config.POSITION_MANAGER}/instance/{t}"),
        })
        pnl_css = view["PnL"].map(lambda v: "" if pd.isna(v) else f"color: {'#5aa2ff' if v >= 0 else '#ff7b7b'}")
        sty = view.style.apply(lambda _: pd.DataFrame({"PnL": pnl_css}, index=view.index).reindex(columns=view.columns,
                                                                                                fill_value=""), axis=None)
        st.dataframe(sty.format({"In": usd, "Out": usd, "Fees": usd, "PnL": lambda v: ("+" if v > 0 else "") + usd(v)},
                                na_rep="–"),
                     hide_index=True, **stretch(st.dataframe), height=320,
                     column_config={"NFT": st.column_config.LinkColumn("NFT", display_text=r"instance/(\d+)$")})
    con.close()


# ------------------------------------------------------------ twin tab ----
def twins():
    con = conn()
    real = st.session_state.get("real", False)
    rows, _ = Q.wallet_rows(con, "real" if real else "raw")
    if not rows:
        st.info("No ranked wallets yet.")
        con.close()
        return
    st.caption("Matches how a wallet LPs: hold time, range width, ladder size, bid / ask side, ETH vs USDG, token "
               "size at entry, trade size, pace, overlap, pool fee, win rate. Scores are within each class.")
    options = {r["addr"]: f"#{r['rank']} {short(r['addr'])}{' · ' + r['label'] if r.get('label') else ''} · "
                          f"{r.get('style') or ''} · score {r['score']:.1f}" for r in rows}
    c1, c2 = st.columns([2, 3])
    pick = c1.selectbox("Ranked wallet", list(options), format_func=options.get, index=None,
                        placeholder="Pick a ranked wallet…")
    typed = c2.text_input("…or paste any address", placeholder="0x…")
    addr = (typed.strip() or pick or "").lower()
    if not addr:
        con.close()
        return
    res = Q.similar(con, addr, "real" if real else "raw")
    con.close()
    if res.get("error"):
        st.warning(res["error"])
        return
    t = res["target"]
    st.markdown(f"Matching **{short(t['addr'])}**{' (' + t['label'] + ')' if t.get('label') else ''}: "
                f"{KIND_LABEL.get(t.get('kind'), '')} · {t.get('style') or ''} · hold {dur(t['duration'])} · "
                f"range {range_fmt(t['range'])} · "
                f"size {usd(t['size'])} · ETH {pct(t['eth'])} · score {f1(t['score'])}")
    stt = st.session_state.get("status") or {}
    df = frame(res["rows"], stt.get("now", time.time()), stt.get("data_from"))
    df["match"] = [r["sim"] for r in res["rows"]]
    keys = ["match", "score", "wallet", "kind", "verdict", "net", "net_7d", "total", "roi", "win", "pf", "dd", "style",
            "duration", "range", "size", "usdg", "mcap", "trades", "concur", "star", "lp"]
    css = heat(df)
    css["match"] = ""
    sty = df[keys].style.apply(lambda _: css.loc[df.index, keys], axis=None)
    fm = formatters()
    fm["match"] = lambda v: f"{v:.0f}%"
    cc = column_config(keys, (st.session_state.get("status") or {}).get("metric_days", 30))
    cc["match"] = col("Column", "Match", help="How close its LP style is to the target.", pinned=True)
    st.dataframe(sty.format({k: v for k, v in fm.items() if k in keys}, na_rep="–"), column_config=cc,
                 hide_index=True, height=auto_height(len(df), 560), **stretch(st.dataframe))


# ------------------------------------------------------------ watchlist ---
def watchlist():
    con = conn()
    labs = [dict(r) for r in con.execute("SELECT * FROM labels ORDER BY added DESC")]
    rows, _ = Q.wallet_rows(con, "real" if st.session_state.get("real") else "raw")
    con.close()
    with st.expander("Add wallets", expanded=not labs):
        text = st.text_area("Paste wallets, one per line. Text after an address becomes its label "
                            "(a pasted LP Agent sheet works too).", height=110,
                            placeholder="0x74b26ec2754dcbd27e43a2a527e38005358a772e, Cactus")
        if st.button("Add to watchlist", disabled=not text.strip()):
            c = conn()
            got = Q.import_text(c, text, "paste")
            c.close()
            st.toast(f"Added {got['added']} wallet(s)")
            st.rerun()
    if not labs:
        st.info("Nothing here yet. Star a wallet in its dossier, or paste some above.")
        return
    scored = {r["addr"]: r for r in rows}
    data = []
    for lab in labs:
        r = scored.get(lab["addr"], {})
        slf = r.get("slf")
        data.append({
            "Wallet": lab["addr"], "★": bool(lab["starred"]), "Label": lab["label"] or "", "Notes": lab["notes"] or "",
            "From": "Smart LP Finder" if lab["source"] == "smart-lp-finder" else (lab["source"] or ""),
            "Smart LP": f"#{slf['rank']}/{slf['total']} · {slf.get('pair') or short(slf['pool'])}" if slf else "–",
            "Reference": lab.get("ref") or "", "Class": KIND_LABEL.get(r.get("kind"), "–"),
            "Score": r.get("score"), "Tier": r.get("verdict") or "not ranked yet", "Realized": r.get("net"),
            "ROI/mo": r.get("roi"), "Win %": r.get("win"), "Style": r.get("style") or "",
            "LP Agent": lpagent.link(lab["addr"]), "Remove": False,
        })
    df = pd.DataFrame(data)
    for col in ("Score", "Realized", "ROI/mo", "Win %"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    edited = st.data_editor(
        df, hide_index=True, key="watch_editor", **stretch(st.data_editor),
        disabled=["Wallet", "From", "Smart LP", "Class", "Score", "Tier", "Realized", "ROI/mo", "Win %", "Style",
                  "LP Agent"],
        column_config={
            "Reference": st.column_config.SelectboxColumn(
                options=["", "multiday", "degen"], help="Reference wallet of that class: wallets of the class show how "
                "close they play to it, and it keeps the class."),
            "Score": st.column_config.NumberColumn(format="%.1f"),
            "Realized": st.column_config.NumberColumn(format="$%.2f"),
            "ROI/mo": st.column_config.NumberColumn(format="%.1f%%"),
            "Win %": st.column_config.NumberColumn(format="%.0f%%"),
            "LP Agent": st.column_config.LinkColumn(display_text="open ↗"),
            "Remove": st.column_config.CheckboxColumn(help="Tick, then Save changes"),
        })
    if st.button("Save changes", type="primary"):
        c = conn()
        for (_, old), (_, new) in zip(df.iterrows(), edited.iterrows()):
            if new["Remove"]:
                Q.delete_label(c, new["Wallet"])
            elif (old["Label"], old["Notes"], old["★"], old["Reference"]) != \
                    (new["Label"], new["Notes"], new["★"], new["Reference"]):
                c.execute("UPDATE labels SET label=?, notes=?, starred=?, ref=? WHERE addr=?",
                          (new["Label"] or None, new["Notes"] or None, 1 if new["★"] else 0, new["Reference"] or None,
                           new["Wallet"]))
        c.commit()
        c.close()
        st.toast("Watchlist saved")
        st.rerun()
    pick = st.selectbox("Open a dossier", [""] + [d["Wallet"] for d in data],
                        format_func=lambda a: "Pick a wallet…" if not a else short(a) + "  " + next(
                            (x["Label"] for x in data if x["Wallet"] == a), ""), label_visibility="collapsed")
    if pick and st.session_state.get("shown_watch") != pick:
        st.session_state["shown_watch"] = pick
        dossier(pick)


# ------------------------------------------------------ vs RH Discovery ---
def rhd_check():
    import compare
    st.markdown("#### Check our figures against RH Discovery")
    st.caption("On RH Discovery, select rows of its table (the header row too, if you like), copy them (⌘C) and "
               "paste them here. Each wallet is matched to ours by its short address and its figures are set next "
               "to ours: ✓ within tolerance, ✗ further apart. Both sides count PnL, net worth, ROI and win rate "
               "the same way (see the field guide).")
    text = st.text_area("RH Discovery rows", key="rhd_text", height=150,
                        placeholder="46\t79.0\t0x5784…4ffd\t$2866\t+$3858\t+134.6%\t…")
    if not text.strip():
        return
    rows = compare.parse(text)
    if not rows:
        st.warning("No rows with a wallet (0x…) cell found in what you pasted.")
        return
    con = conn()
    res = compare.compare(con, rows)
    con.close()
    s = res["summary"]
    pills = [f"<b>{s['pasted']}</b> pasted", f"<b>{s['ranked']}</b> ranked by us",
             f"<b>{s['not_ranked']}</b> found, not ranked", f"<b>{s['not_in_data']}</b> not in our data"]
    if s["ambiguous"]:
        pills.append(f"<b>{s['ambiguous']}</b> ambiguous")
    pills += [f"{p['label']} <b>{p['same']}/{p['compared']}</b> ✓" for p in s["per_metric"].values() if p["compared"]]
    st.markdown('<div class="pills">' + "".join(f"<span>{x}</span>" for x in pills) + "</div>", unsafe_allow_html=True)
    fmt = {"win_pos": lambda v: pct(v, 1), "roi_nw": lambda v: pct(v, 1), "roi_all": lambda v: pct(v, 1)}
    recs, css = [], []
    for r in res["rows"]:
        rec = {"Wallet": r["wallet"], "Ours": r["status"] + (f": {r['reason']}" if r["reason"] else "")}
        mark = {"Wallet": "", "Ours": "" if r["status"] == "ranked" else "color: #fab219"}
        for c in r["checks"] or [{"key": k, "label": lab, "theirs": r["rhd"].get(k), "ours": None, "same": None}
                                 for k, lab, _ in compare.CHECKS]:
            f = fmt.get(c["key"], usd)
            rec[f"{c['label']} · RHD"] = f(c["theirs"]) if c["theirs"] is not None else "–"
            rec[f"{c['label']} · ours"] = (f(c["ours"]) if c["ours"] is not None else "–") +                 ("" if c["same"] is None else " ✓" if c["same"] else " ✗")
            mark[f"{c['label']} · RHD"] = ""
            mark[f"{c['label']} · ours"] = "" if c["same"] is None else \
                f"background-color: rgba({BLUE},0.22)" if c["same"] else f"background-color: rgba({RED},0.22)"
        rec["Notes"] = " · ".join(r["notes"])
        mark["Notes"] = ""
        recs.append(rec)
        css.append(mark)
    df, cdf = pd.DataFrame(recs), pd.DataFrame(css)
    st.dataframe(df.style.apply(lambda _: cdf[df.columns], axis=None), hide_index=True, **stretch(st.dataframe),
                 height=auto_height(len(df), 620))
    for note in compare.explain(s):
        st.caption(note)


# ------------------------------------------------------------- settings ---
def settings():
    stt = st.session_state.get("status") or {}
    cfg = config.load_env()
    src = f" (from {cfg['RPC_SOURCE']})" if cfg["RPC_SOURCE"] not in ("", "settings") else ""
    with st.form("settings"):
        st.caption("Saved to rh_wallet_finder/.env on this computer only (git-ignored). Leave a field empty to keep it.")
        rpc = st.text_input(f"RPC 1 · sync goes here first (SolidRPC) · now: {config.mask(cfg['RPC_URL']) or 'not set'}"
                            f"{src}", type="password", placeholder="https://rpc.solidrpc.io/ak_…/evm/4663")
        call = st.text_input(f"RPC 2 · works alongside RPC 1 (Alchemy API key, or any RPC link) · now: "
                             f"{config.mask(cfg['CALL_RPC_URL']) or 'not set: RPC 1 does everything'}",
                             type="password", placeholder="your Alchemy API key, or https://…",
                             help="With two RPCs, price reads are shared between them, and whichever one "
                                  "rate limits or runs out of quota rests while the other carries on (sync too).")
        key = st.text_input(f"LP Agent API key · {'set' if cfg['LPAGENT_API_KEY'] else 'not set'}", type="password")
        now_pub = "publish link set" if cfg["VERCEL_PUBLISH_URL"] else \
            "Blob token set (these expire: use the publish link)" if cfg["VERCEL_BLOB_TOKEN"] else "not set"
        blob = st.text_input(f"Web showcase · publish link · {now_pub}", type="password",
                             placeholder="https://<your site>.vercel.app/api/publish?key=…",
                             help="Your site's publish link (README: Web showcase). With it, the worker uploads a "
                                  "read-only copy of the boards after each ranking. Labels, notes, the watchlist "
                                  "and keys never go out. A Blob read-write token works here too, until it expires.")
        a, b, c = st.columns(3)
        since_launch = int((time.time() - config.CHAIN_LAUNCH_TS) / 86400) + 1
        vals = {
            "BACKFILL_DAYS": a.text_input("History to load (days)", cfg["BACKFILL_DAYS"],
                                          help=f"PnL ALL, PnL 3M and Avg / month only see this far back. RH "
                                               f"Discovery's go back to the chain's launch (1 Jul 2026): "
                                               f"{since_launch} days. More history = more blocks to sync and more "
                                               f"positions to price (after the window's own)."),
            "METRIC_DAYS": b.text_input("Performance window (days)", cfg["METRIC_DAYS"],
                                        help="Every figure covers this many days; a wallet needs Min closed "
                                             "positions inside it to be ranked at all."),
            "ACTIVE_DAYS": c.text_input("Leave out wallets idle for (days, 0 = never)", cfg["ACTIVE_DAYS"],
                                        help="Wallets with no LP action for this long aren't ranked or shown."),
            "MIN_CLOSED": a.text_input("Min closed positions", cfg["MIN_CLOSED"]),
            "MULTIDAY_HOURS": b.text_input("Multiday from (median hold, hours)", cfg["MULTIDAY_HOURS"],
                                           help="Wallets whose trades' median hold reaches this are Multiday "
                                                "players; below it, Degen players."),
            "SELL_SLIPPAGE_PCT": c.text_input("Real PnL slippage %", cfg["SELL_SLIPPAGE_PCT"]),
            "SCORE_POSITIONS_PER_RUN": a.text_input("Positions priced per run", cfg["SCORE_POSITIONS_PER_RUN"]),
            "SYNC_MINUTES": b.text_input("Minutes between syncs", cfg["SYNC_MINUTES"]),
            "RPC_WORKERS": c.text_input("Parallel RPC requests", cfg["RPC_WORKERS"]),
            "PUBLISH_MINUTES": a.text_input("Web showcase: upload every (minutes)", cfg["PUBLISH_MINUTES"],
                                            help="Each upload is one write to your Vercel Blob store; fewer "
                                                 "uploads keep you well inside the free plan."),
        }
        if st.form_submit_button("Save", type="primary"):
            upd = {k: v.strip() for k, v in vals.items() if v.strip() and v.strip() != cfg[k]}
            for k, v in (("RPC_URL", rpc), ("CALL_RPC_URL", call), ("LPAGENT_API_KEY", key)):
                if v.strip():
                    upd[k] = config.rpc_link(v) if k in ("RPC_URL", "CALL_RPC_URL") else v.strip()
            bad_blob = False
            if blob.strip():  # the site's publish link, or a Blob token
                link = config.publish_link(blob)
                if link and config.split_publish_link(link)[1]:
                    upd["VERCEL_PUBLISH_URL"] = link
                elif config.blob_store_id(config.blob_token(blob)):
                    upd["VERCEL_BLOB_TOKEN"] = config.blob_token(blob)
                else:
                    bad_blob = True
            bad = [k for k in ("RPC_URL", "CALL_RPC_URL") if k in upd and not upd[k].startswith(("https://", "http://"))]

            def not_number(v):
                try:
                    return float(v) < 0
                except ValueError:
                    return True
            nan = [k for k in vals if k in upd and not_number(upd[k])]
            if bad:
                st.error("An RPC needs a link starting with https:// (or, for RPC 2, just an Alchemy API key)")
            elif bad_blob:
                st.error("Paste the site's whole publish link, https://<your site>.vercel.app/api/publish?key=… "
                         "(or a Blob read-write token, vercel_blob_rw_…).")
            elif nan:
                st.error("These need a number (0 or more): " + ", ".join(nan))
            elif upd:
                config.save_env(upd)
                w = background_worker()
                if w:
                    w.kick()
                st.success("Saved. Sync restarts with the new settings.")
    w = background_worker()
    c1, c2 = st.columns([1, 5])
    if c1.button("Sync now", disabled=w is None):
        w.kick()
        st.toast("Sync started")
    c2.caption(f"Worker: {w.state if w else 'off'}" + (f" · error: {w.error}" if w and w.error else "")
               + f" · syncs every {cfg['SYNC_MINUTES']} min · page refreshes every {LIVE_SECONDS}s")
    if w:
        st.code("\n".join(list(w.logs)[-80:]) or "(no log yet)", language=None)
    slf = stt.get("smart_lp") or {}
    if slf.get("url") or slf.get("saved") or slf.get("ranked"):
        st.caption(f"RH Smart LP Finder: {slf.get('saved', 0)} saved wallets and {slf.get('ranked', 0)} wallets from "
                   f"its pool rankings imported.")
    pub = stt.get("published") or {}
    if stt.get("has_publish") or pub:
        c1, c2 = st.columns([1, 5])
        if c1.button("Publish now", disabled=not stt.get("has_publish")):
            import publish
            con = conn()
            try:
                publish.publish(con, cfg, log=lambda *a: None)
                st.toast("Published to the web showcase")
            except publish.PublishError as e:
                st.error(str(e))
            con.close()
        note = (f"Web showcase: last upload {ago(pub['at'], stt.get('now', time.time()))} · {pub.get('wallets', 0):,} "
                f"wallets · {pub.get('bytes', 0) / 1024:.0f} KB") if pub.get("at") else "Web showcase: nothing uploaded yet"
        if stt.get("publish_error"):
            note += f" · last try failed: {stt['publish_error']}"
        c2.caption(note)


def field_guide():
    legend = "".join(f'<span class="sw" style="background:rgba({RED},{a})"></span>' for a in reversed(STEPS)) + \
        '<span class="sw"></span>' + "".join(f'<span class="sw" style="background:rgba({BLUE},{a})"></span>' for a in STEPS)
    body = f'<div class="legend">Cell colour against the other ranked wallets (PnL / Open PnL / ROI: above or ' \
           f'below zero): <span>worse</span>{legend}<span>better</span> · uncoloured columns describe style, not quality</div>'
    groups = {}
    days = (st.session_state.get("status") or {}).get("metric_days", 30)
    for k, label, group, _, helptext in COLS:
        groups.setdefault(group, []).append((label.format(d=f"{days:g}"), helptext))
    d = f"{days:g}"
    groups["Filters"] = [(lab.format(d=d), h) for _, lab, _, _, h in SELECTS if h] + \
        [(lab.format(d=d), h) for _, lab, h in CHECKS]
    groups["Player classes"] = [
        ("Multiday", "Its trades' median hold is at least the line in Settings (2 h by default). Ranked only against "
                     "other multiday players, with open positions counted (total PnL)."),
        ("Degen", "Median hold under that line. Ranked only against other degen players, on realized PnL and how "
                  "reliably the trades pay (profit factor). Bots are hidden unless you tick Show bots."),
        ("Trades", "Ranges opened in one transaction (a ladder) are one trade for win rate, size, hold and pace. PnL, "
                   "fees and the PnL curve still add up every position."),
        ("Reference", "Make any wallet a reference of its class in its dossier (◆ on the board). The app starts with "
                      "your two examples.")]
    groups["Modes"] = [
        ("Real PnL", "Charges each position for selling back the token it returns (pool fee + slippage from Settings) "
                     "and re-ranks on those figures."),
        ("Last 24h", "The same board on positions closed in the last 24 hours."),
        ("Token launches", "Positions that seed 2%+ of a token's supply are launches, not trading, and are left out.")]
    body += '<div class="guide">' + "".join(
        f"<h4>{g}</h4>" + "".join(f"<p><b>{lab}</b>{h}</p>" for lab, h in items) for g, items in groups.items()) + \
        "</div>"
    st.markdown(body, unsafe_allow_html=True)


# ----------------------------------------------------------------- page ---
def main():
    css()
    background_worker()
    live_header()
    stt = st.session_state.get("status") or {}
    top = st.columns([6, 1.3])
    top[1].toggle("Real PnL", key="real", help="Charge every position for selling back the token it returns")
    if st.session_state.get("real"):
        fee = f"{stt['sell_fee'] * 100:.2f}" if stt.get("sell_fee") else "–"
        st.markdown(f'<div class="mode"><b>Real PnL on.</b> Every figure is charged for selling back the token side a '
                    f'position returns (median <b>{fee}%</b>: pool fee + {stt.get("slippage", 1)}% slippage), and the '
                    f'board is re-ranked on those numbers.</div>', unsafe_allow_html=True)
    real = st.session_state.get("real", False)
    t_multi, t_degen, t_day, t_twin, t_watch, t_rhd, t_set = st.tabs(
        ["Multiday", "Degen", "Last 24h", "Twin finder", "Watchlist", "vs RH Discovery", "Settings"])
    for tab, kind in ((t_multi, "multiday"), (t_degen, "degen")):
        with tab:
            live_board("real" if real else "raw", kind, kind=kind)
            with st.expander("Field guide: what every column means"):
                field_guide()
    with t_day:
        live_board("d1real" if real else "d1raw", "day", top=100)
    with t_twin:
        twins()
    with t_watch:
        watchlist()
    with t_rhd:
        rhd_check()
    with t_set:
        settings()


main()
