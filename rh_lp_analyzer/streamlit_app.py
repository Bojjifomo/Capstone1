"""RH LP Analyzer: paste a Robinhood Chain wallet, get its Uniswap V4 LP game
dissected for copy-trading (PnL, capital ROI, risk, profit quality, how it LPs).

Run:  streamlit run streamlit_app.py      (from this folder)
Needs an RPC (RPC_URL, optional CALL_RPC_URL) in .env, the Settings panel, or
Streamlit secrets. Data is read straight from the chain per wallet and cached
in data/cache.db.
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

import analysis as A  # noqa: E402
import chain as C  # noqa: E402
import config  # noqa: E402
import fetch as F  # noqa: E402

LOGO = HERE / "assets" / "logo.svg"
st.set_page_config(page_title="RH LP Analyzer", page_icon=str(LOGO), layout="wide",
                   initial_sidebar_state="collapsed")

ST_VER = tuple(int(x) for x in re.findall(r"\d+", st.__version__)[:2])
if ST_VER < (1, 37):
    st.error(f"Streamlit {st.__version__} is too old for this app. In Terminal run:\n\n"
             "`python3 -m pip install -U -r requirements.txt`\n\nthen start it again.")
    st.stop()

# st.secrets (Streamlit Cloud, or .streamlit/secrets.toml) feed the same settings as .env
if any(p.exists() for p in (Path.home() / ".streamlit" / "secrets.toml", HERE / ".streamlit" / "secrets.toml")) \
        or os.environ.get("STREAMLIT_SHARING_MODE") or Path("/mount/src").exists():
    try:
        for _k in config.DEFAULTS:
            if _k in st.secrets and not os.environ.get(_k):
                os.environ[_k] = str(st.secrets[_k])
    except Exception:  # noqa: BLE001 - no or broken secrets: .env still works
        pass

VERDICT_COLOR = {"COPY": "45,212,170", "WATCH": "250,178,25", "REJECT": "238,96,96"}
TONE_ICON = {"good": "▲", "bad": "▼", "info": "●"}
FOLLOW = {"✓": "✓ yes", "✗": "✗ bot", "?": "? maybe"}
KIND_LABEL = {"multiday": "Multiday", "degen": "Degen"}
MODES = ("Single wallet", "Batch & compare")
OOR_LABEL = {"above": "out of range ↑", "below": "out of range ↓", None: "in range", "?": "–"}
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")


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
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root { --acc:#2dd4aa; --acc2:#7c5cff; --ink:#e9eef5; --ink2:#b3bdcb; --muted:#7f8ba0; --panel:#0e141d; --panel2:#121a26;
        --line:#1d2735; --line2:#2a384b; --pos:#2dd4aa; --neg:#ee6060; --warn:#fab219; }
.stApp { background: radial-gradient(1000px 480px at 10% -10%, rgba(45,212,170,.09), transparent 60%),
  radial-gradient(900px 420px at 100% 0%, rgba(124,92,255,.08), transparent 60%), #080c12; }
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1180px; padding-top: 1.4rem; padding-bottom: 3rem; }
h1, h2, h3, h4 { font-family: "Space Grotesk", sans-serif !important; }
.brand { display:flex; align-items:center; gap:14px; margin-bottom: 4px; }
.brand img { width:48px; height:48px; filter: drop-shadow(0 0 12px rgba(45,212,170,.25)); }
.word { font: 700 26px/1 "Space Grotesk", sans-serif; letter-spacing: .08em; color: var(--ink); }
.word b { color: var(--acc); }
.tag { font: 500 10.5px "JetBrains Mono", monospace; color: var(--muted); letter-spacing: .28em; margin-top: 6px; text-transform: uppercase; }
.lead { color: var(--muted); font-size: 13.5px; margin: 6px 0 10px; }
.rail { display:flex; gap:6px; flex-wrap:wrap; margin: 0 0 10px; }
.rail span { border:1px solid var(--line2); background: var(--panel); padding: 3px 10px; font: 11.5px "JetBrains Mono", monospace; color: var(--muted); border-radius: 4px; }
.rail b { color: var(--ink); font-weight: 600; }
.empty { text-align:center; padding: 46px 20px; margin-top: 14px; border: 1px solid rgba(45,212,170,.35); border-radius: 10px;
       background: linear-gradient(160deg, var(--panel2), var(--panel)); box-shadow: 0 0 34px rgba(45,212,170,.07); }
.empty b { display:block; font: 600 21px "Space Grotesk", sans-serif; color: var(--ink); margin-bottom: 6px; }
.empty span { font-size: 13.5px; color: var(--muted); } .empty i { color: var(--acc); font-style: normal; }
.done { border: 1px solid var(--line); background: var(--panel); padding: 9px 14px; margin: 8px 0; border-radius: 6px;
       font: 12.5px "JetBrains Mono", monospace; color: var(--ink2); }
.card { border: 1px solid var(--line); border-top: 2px solid var(--acc); background: linear-gradient(160deg, var(--panel2), var(--panel));
       padding: 16px 20px 14px; margin: 10px 0 14px; border-radius: 8px; }
.title { display:flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap;
       font: 700 23px "Space Grotesk", sans-serif; color: var(--ink); }
.tags { display:flex; gap:6px; } .tags i { font: 700 11px "JetBrains Mono", monospace; letter-spacing:.14em; padding: 4px 10px; font-style: normal; border-radius: 4px; }
.chips { display:flex; gap:6px; flex-wrap: wrap; margin-top: 10px; }
.chips span, .chips a { border: 1px solid var(--line2); padding: 2px 9px; font: 500 11px "JetBrains Mono", monospace; color: var(--muted);
       text-transform: uppercase; letter-spacing: .06em; border-radius: 4px; text-decoration: none; }
.chips span.addr { color: var(--acc); border-color: rgba(45,212,170,.45); }
.big { display:grid; grid-template-columns: repeat(3, 1fr); gap:12px; margin-bottom: 12px; }
.big > div { position:relative; overflow:hidden; padding:16px 18px 14px; background: linear-gradient(160deg, var(--panel2), var(--panel));
       border: 1px solid var(--line); border-top: 2px solid var(--c, var(--acc)); border-radius: 8px; }
.big > div::after { content:""; position:absolute; right:-30px; top:-30px; width:110px; height:110px; border-radius:50%;
       background: radial-gradient(circle, var(--glow, rgba(45,212,170,.18)), transparent 70%); }
.big .k { font: 600 11px "JetBrains Mono", monospace; letter-spacing:.2em; text-transform:uppercase; color: var(--ink2); }
.big .v { font: 700 32px/1.2 "Space Grotesk", sans-serif; color: var(--ink); margin-top: 4px; }
.big .s { font-size: 12px; color: var(--muted); }
.qual { display:grid; grid-template-columns: repeat(3, 1fr); gap:1px; background: var(--line); border: 1px solid var(--line); border-radius: 8px; overflow:hidden; margin-bottom: 8px; }
.qual > div { background: var(--panel); padding: 12px 14px; }
.qual small, .dk small { display:block; font: 600 10px "JetBrains Mono", monospace; letter-spacing:.18em; text-transform:uppercase; color: var(--muted); }
.qual b { display:block; font: 700 19px "Space Grotesk", sans-serif; margin: 2px 0 4px; color: var(--ink); }
.qual p, .qual li { font-size: 12.5px; color: var(--ink2); margin: 0; }
.qual ul { margin: 6px 0 0 16px; padding: 0; }
.flag { font-size: 13px; color: var(--ink2); padding: 5px 12px; border-left: 3px solid var(--line2); margin: 3px 0; background: var(--panel); border-radius: 0 4px 4px 0; }
.flag.good { border-color: var(--pos); } .flag.good b { color: var(--pos); }
.flag.bad { border-color: var(--neg); } .flag.bad b { color: var(--neg); }
.sec { margin: 24px 0 6px; padding-bottom: 5px; border-bottom: 1px solid var(--line); }
.sec b { font: 600 16px "Space Grotesk", sans-serif; color: var(--ink); margin-right: 10px; }
.sec span { font: 12px "JetBrains Mono", monospace; color: var(--muted); }
.dk { display:grid; grid-template-columns: repeat(4, 1fr); gap:1px; background: var(--line); border:1px solid var(--line); border-radius: 8px; overflow: hidden; margin: 8px 0 4px; }
.dk.dk3 { grid-template-columns: repeat(3, 1fr); }
.dk div { background: var(--panel); padding: 9px 12px; }
.dk span { font: 600 16px "JetBrains Mono", monospace; color: var(--ink); }
.dk em { display:block; font: normal 11px "JetBrains Mono", monospace; color: var(--muted); margin-top: 2px; }
[data-testid="stExpander"] details { background: var(--panel); border: 1px solid var(--line); }
@media (max-width: 900px) { .big, .qual { grid-template-columns: 1fr; } .dk, .dk.dk3 { grid-template-columns: 1fr 1fr; } }
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


# --------------------------------------------------------------- pieces ---
def grid(cells, cols=4):
    """A grid of (title, value, small print) cells."""
    return (f'<div class="dk{" dk3" if cols == 3 else ""}">'
            + "".join(f"<div><small>{k}</small><span>{v}</span>" + (f"<em>{s}</em>" if s else "") + "</div>"
                      for k, v, s in cells) + "</div>")


def section(title, sub=""):
    st.markdown(f'<div class="sec"><b>{title}</b>{f"<span>{sub}</span>" if sub else ""}</div>',
                unsafe_allow_html=True)


AXIS = dict(gridColor="#1d2735", labelColor="#7f8ba0", domainColor="#2a384b", labelFont="JetBrains Mono")


def bars(rows, label, value="share", fmt=".0f", suffix="%", color="#2dd4aa"):
    """Horizontal bars, one row per (name, value), in the given order."""
    df = pd.DataFrame(rows, columns=[label, value])
    if df.empty:
        st.caption("Nothing to show.")
        return
    base = alt.Chart(df).encode(y=alt.Y(f"{label}:N", sort=list(df[label]), title=None,
                                        axis=alt.Axis(labelOverlap=False, labelLimit=170, ticks=False)),
                                x=alt.X(f"{value}:Q", title=None, axis=None))
    chart = base.mark_bar(color=color, cornerRadiusEnd=3) + base.mark_text(
        align="left", dx=4, color="#b3bdcb", font="JetBrains Mono", fontSize=11).encode(
        text=alt.Text(f"{value}:Q", format=fmt))
    st.altair_chart(chart.properties(height=30 * len(df) + 30, title=alt.TitleParams(
        f"{label} ({suffix})" if suffix else label, anchor="start", color="#7f8ba0", fontSize=11,
        font="JetBrains Mono")).configure_view(strokeWidth=0).configure_axis(**{**AXIS, "grid": False}),
        **stretch(st.altair_chart))


def pnl_chart(daily, height=240):
    """Daily realized PnL (bars) and the running total (line)."""
    df = pd.DataFrame(daily)
    df["when"] = pd.to_datetime(df["day"], unit="s")
    base = alt.Chart(df).encode(x=alt.X("when:T", title=None))
    tip = [alt.Tooltip("when:T", title="day"), alt.Tooltip("pnl:Q", title="that day", format="$,.2f"),
           alt.Tooltip("cum:Q", title="running total", format="$,.2f")]
    day = base.mark_bar(opacity=.85).encode(
        y=alt.Y("pnl:Q", title="USD"), tooltip=tip,
        color=alt.condition("datum.pnl >= 0", alt.value("#2dd4aa"), alt.value("#ee6060")))
    cum = base.mark_line(color="#a78bfa", strokeWidth=2.2, interpolate="monotone").encode(y="cum:Q", tooltip=tip)
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#2a384b", strokeDash=[3, 3]).encode(y="y:Q")
    st.altair_chart((zero + day + cum).properties(height=height).configure_view(strokeWidth=0).configure_axis(**AXIS),
                    **stretch(st.altair_chart))


# ----------------------------------------------------------------- data ---
def conn():
    return F.connect()


def rpcs():
    from rpc import make_rpcs
    cfg = config.load_env()
    return make_rpcs(cfg) if cfg["RPC_URL"] else (None, None)


def load(addrs, per):
    """Read and price these wallets from the chain for this period (once per session
    and period, or again on Refresh), with a progress bar."""
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
    try:
        F.Loader(c, rpc, call_rpc, cfg, progress).load(addrs, start)
    except Exception as e:  # noqa: BLE001 - show it, keep the page
        bar.empty()
        msg = str(e)
        if "quota" in msg.lower() or "plan limit" in msg.lower():
            msg += ". Your RPC's allowance is used up: wait for it to reset, or add a second RPC in Settings."
        st.error(f"Couldn't read the chain: {msg}")
        return False
    finally:
        c.close()
    bar.empty()
    return True


def report_data(addrs, per, real):
    cfg = config.load_env()
    now = time.time()
    start, end = A.period(per, now)
    c = conn()
    pos, skipped = F.positions(c, addrs, start, cfg)
    first = F.first_seen(c, addrs)
    eth = F.get_meta(c, "eth_usd")
    c.close()
    a = A.analyze(pos, now, start, end, real, float(cfg.get("MULTIDAY_HOURS") or 2) * 3600)
    if a:
        a["filters"] = A.recommend_filters([p for p in pos if p["close_ts"] and p["close_ts"] >= start
                                            and (end is None or p["close_ts"] <= end)],
                                           (lambda p: p["pnl"] - (p["cost"] or 0)) if real else (lambda p: p["pnl"]))
    return {"analysis": a, "skipped": skipped, "positions": pos, "first_seen": first, "start": start, "eth": eth}


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


# ----------------------------------------------------------------- page ---
def header():
    logo = base64.b64encode(LOGO.read_bytes()).decode()
    st.markdown(f"""
<div class="brand"><img src="data:image/svg+xml;base64,{logo}">
  <div><div class="word">RH <b>LP ANALYZER</b></div><div class="tag">Robinhood Chain · Uniswap V4 LP intelligence</div></div>
</div>
<div class="lead">Paste a wallet to dissect its LP PnL, risk &amp; edge, and whether it's worth copying.</div>""",
                unsafe_allow_html=True)


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
    with st.expander("⚙ Settings — RPC" + ("" if cfg["RPC_URL"] else "  (needed: paste your RPC here)"),
                     expanded=not cfg["RPC_URL"]):
        st.caption(f"RPC 1: `{config.mask(cfg['RPC_URL']) or 'not set'}` · RPC 2: "
                   f"`{config.mask(cfg['CALL_RPC_URL']) or 'not set'}`. Two RPCs share the work, and when one runs "
                   "out of quota the other carries on. Saved to rh_lp_analyzer/.env on this computer (on Streamlit "
                   "Cloud, put RPC_URL / CALL_RPC_URL in the app's Secrets instead).")
        with st.form("rpc_form", border=False):
            c1, c2 = st.columns(2)
            r1 = c1.text_input("RPC 1 (eth_getLogs + calls)", type="password",
                               placeholder="https://rpc.solidrpc.io/ak_…/evm/4663")
            r2 = c2.text_input("RPC 2 (optional, an Alchemy API key works)", type="password",
                               placeholder="Alchemy key or RPC link")
            look = st.number_input("Lookback days: positions opened this long before the period still count",
                                   min_value=0, max_value=120, value=int(float(cfg["LOOKBACK_DAYS"])))
            if st.form_submit_button("Save & test"):
                upd = {"LOOKBACK_DAYS": str(look)}
                if r1.strip():
                    upd["RPC_URL"] = config.rpc_link(r1)
                if r2.strip():
                    upd["CALL_RPC_URL"] = config.rpc_link(r2)
                config.save_env(upd)
                for k, v in upd.items():
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
    saved = saved_wallets()
    if saved:
        with st.expander("★ Saved wallets — quick load & compare"):
            names = {x["addr"]: f"{short(x['addr'])}  {x['label'] or ''}" for x in saved}
            pick = st.multiselect("Saved wallets", list(names), format_func=names.get, label_visibility="collapsed",
                                  placeholder="Pick one (or several to combine / compare)…")
            c1, c2 = st.columns(2)
            c1.button("Load into the box", disabled=not pick, on_click=fill, args=(pick,))
            if c2.button("Remove from saved", disabled=not pick):
                for a in pick:
                    forget_wallet(a)
                st.rerun()
    with st.form("an_form", border=False):
        c1, c2, c3 = st.columns([5, 1.6, 1.2], vertical_alignment="bottom")
        c1.text_input("Wallet", key="an_text", label_visibility="collapsed",
                      placeholder="0x… (or paste several, comma/space, to combine)" if mode == MODES[0]
                      else "0x…, 0x…, 0x…  (up to 20 wallets: each analysed, compared, and your capital split)")
        c2.selectbox("Period", A.PERIODS, key="an_period",
                     **({} if "an_period" in st.session_state else {"index": A.PERIODS.index("30 days")}))
        go = c3.form_submit_button("Analyze", type="primary", **stretch(st.form_submit_button))
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
        st.markdown('<div class="empty"><b>Run a wallet through the analyzer</b><span>Paste a Robinhood Chain wallet '
                    'above and hit <i>Analyze</i> to break down its Uniswap V4 LP PnL, risk profile, capital '
                    'deployment and gameplay over the selected period. The first read of a wallet comes straight '
                    'from the chain and can take a minute; after that it only reads what is new.</span></div>',
                    unsafe_allow_html=True)
        return
    addrs, per, mode = run
    addrs = list(addrs)[:20]
    loaded = st.session_state.setdefault("loaded_runs", set())
    if mode == MODES[1] and len(addrs) > 1:
        note = st.empty()
        for n, a in enumerate(addrs, 1):
            if ((a,), per) not in loaded:
                note.caption(f"Reading wallet {n}/{len(addrs)}: {short(a)}…")
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


def report(addrs, per):
    real = st.toggle("Real PnL — charge the cost of selling the token back", key="real",
                     help="Each position pays for selling back the token side it returns: the pool fee + "
                          f"{config.load_env()['SELL_SLIPPAGE_PCT']}% slippage.")
    res = report_data(addrs, per, real)
    a, sk = res["analysis"], res["skipped"]
    now = time.time()
    who = " + ".join(short(x) for x in addrs)
    if res["first_seen"] is None:
        st.warning(f"{who}: no Uniswap V4 LP position (PositionManager NFT) found from "
                   f"{time.strftime('%d %b %Y', time.gmtime(max(config.CHAIN_LAUNCH_TS, res['start'] - 30 * 86400)))}"
                   " on. Check the address, or pick a longer period.")
        return
    notes = []
    if sk["unpriced"]:
        notes.append(f"{sk['unpriced']:,} not priced (RPC read failed — hit Refresh)")
    if sk["launch"]:
        notes.append(f"{sk['launch']:,} token launch(es) left out")
    if sk["unpriceable"]:
        notes.append(f"{sk['unpriceable']:,} in pools without ETH/USDG")
    if sk["before"]:
        notes.append(f"{sk['before']:,} opened before the lookback")
    c1, c2 = st.columns([6, 1], vertical_alignment="center")
    c1.markdown(f'<div class="done">✓ Analysis complete · {a["positions"] if a else 0:,} positions'
                + (" · " + " · ".join(notes) if notes else "") + "</div>", unsafe_allow_html=True)
    if c2.button("↻ Refresh", help="Read new blocks from the chain and re-value open positions"):
        st.session_state["loaded_runs"].discard((tuple(addrs), per))
        st.rerun()
    if not a:
        st.info("Nothing closed or open in this period. Pick a longer period.")
        return

    age = (now - res["first_seen"]) / 86400
    vc = VERDICT_COLOR[a["verdict"]]
    chips = [f'<span class="addr">{who}</span>', f"<span>{per}</span>", f"<span>{a['positions']} positions</span>",
             f"<span>V4 LP age · {age:.0f}d</span>"]
    if len(addrs) == 1:
        chips.append(f'<a href="{config.EXPLORER}/address/{addrs[0]}" target="_blank">explorer ↗</a>')
    netc = "45,212,170" if a["net"] >= 0 else "238,96,96"
    st.markdown(f"""
<div class="card">
  <div class="title">Wallet Analysis<div class="tags"><i style="background:rgba(124,92,255,.18);color:#a78bfa">
  {KIND_LABEL[a['kind']].upper()}</i><i style="background:rgba({vc},.18);color:rgb({vc})">{a['verdict']}</i></div></div>
  <div class="chips">{''.join(chips)}</div>
</div>
<div class="big">
  <div style="--c:rgb({netc});--glow:rgba({netc},.2)"><div class="k">Net PnL</div><div class="v">{money(a['net'], True)}</div>
    <div class="s">{a['wins']}W / {a['losses']}L · {a['flat']} flat</div></div>
  <div style="--c:#a78bfa;--glow:rgba(124,92,255,.2)"><div class="k">Capital ROI</div><div class="v">{pct(a['roi'], 2)}</div>
    <div class="s">over {a['active_days']:.1f}d active · ≈{pct(a['roi_30d'], 1)}/30d</div></div>
  <div style="--c:#e9eef5;--glow:rgba(255,255,255,.1)"><div class="k">Win rate</div><div class="v">{pct(a['win'], 1)}</div>
    <div class="s">{a['closed']} closed positions · {pct(a['trade_win'])} per trade</div></div>
</div>""", unsafe_allow_html=True)

    why = "".join(f"<li>{w}</li>" for w in a["why"])
    fl = "".join(f'<div class="flag {t}"><b>{TONE_ICON[t]} {h}</b> — {x}</div>' for t, h, x in a["flags"])
    st.markdown(f"""
<div class="qual">
  <div><small>Quality</small><b style="color:rgb({vc})">{a['verdict']}</b><p>{A.VERDICT_TEXT[a['verdict']]}</p>
    {f'<ul>{why}</ul>' if why else ''}</div>
  <div><small>Trader type</small><b style="color:#a78bfa">{KIND_LABEL[a['kind']]}</b>
    <p>{A.KIND_TEXT[a['kind']]} {a['kind_fit']}% of its trades fit.</p></div>
  <div><small>Gameplay</small><b>{a['play']}</b><p>{a['play_text']}</p></div>
</div>{fl}""", unsafe_allow_html=True)

    section("Performance & returns", "USD")
    st.markdown(grid([
        ("Total profit", money(a["profit"]), f"{a['wins']} winning positions"),
        ("Total loss", money(-a["loss"]), f"{a['losses']} losing positions"),
        ("Profit / position", money(a["per_position"], True), "net ÷ closed positions"),
        ("Profit factor", pf_fmt(a["pf"]), "$ made per $1 lost · > 2 strong"),
        ("Median deployed", money(a["capital"]), "typical capital in positions (time-weighted)"),
        ("Peak deployed", money(a["peak"]), "most it had in at once"),
        ("Currently open", money(a["open_now"]), f"{a['open']} open now" if a["live"] else "period already over"),
        ("Floating PnL", money(a["float"], True), "open positions, incl. unclaimed fees"),
        ("Net PnL incl. floating", money(a["total"], True), f"closed {money(a['net'], True)} · floating "
                                                            f"{money(a['float'], True)}"),
        ("Fees earned", money(a["fees"]), "LP fees over the period"),
        ("Median trade size", money(a["size"]), f"{a['trades']} trades · ladders count once"),
        ("ETH-paired", pct(a["eth_share"]), "of deposits (rest USDG pools)"),
    ]), unsafe_allow_html=True)

    section("Daily & cumulative PnL", "bars = realized that day · purple line = running total")
    if len(a["daily"]) >= 2:
        pnl_chart(a["daily"])
    else:
        st.caption("Needs closes on at least two days.")

    rec = lambda v: "–" if v is None or not a["avg_green"] else f"≈ {abs(v) / a['avg_green']:.1f} green days to recover"  # noqa: E731
    section("Risk management", "how big and how often the losses get")
    st.markdown(grid([
        ("Max PnL drawdown", pct(a["dd"], 1), f"biggest drop from a high ({money(a['dd_usd'])}), of median capital"),
        ("Worst single day", money(a["worst_day"]), rec(a["worst_day"])),
        ("Out-of-range rate", pct(a["oor_rate"], 1), f"{a['oor_n']} of {a['oor_of']} closed out of range at a loss"),
        ("Worst OOR loss", money(a["oor_worst"]), rec(a["oor_worst"])),
        ("Median OOR loss", money(a["oor_median"]), rec(a["oor_median"])),
        ("Followable", FOLLOW.get(a["follow"], "–"), f"median hold {dur(a['hold'])} · {a['per_day']:.1f} trades/day"),
    ], 3), unsafe_allow_html=True)

    section("Profit quality", "is the profit steady or carried by a few lucky days")
    st.markdown(grid([
        ("Avg daily profit", money(a["avg_green"]), f"across {a['green_days']} green days"),
        ("Avg daily loss", money(a["avg_red"]), f"across {a['red_days']} red days"),
        ("Profit concentration", pct(a["concentration"], 1), "from its top 2 days"),
    ], 3), unsafe_allow_html=True)

    section("Recommended filter", "entry filters a copier can apply: which would have kept the most PnL")
    f = a["filters"]
    if f["best"]:
        rows = [{"Filter": r["name"], "Trades": r["trades"], "PnL": r["pnl"], "Win": r["win"],
                 "vs everything": r["pnl"] - f["all"]["pnl"]} for r in [f["all"]] + f["best"]]
        st.dataframe(pd.DataFrame(rows).style.format({"PnL": lambda v: money(v, True), "Win": lambda v: pct(v),
                                                      "vs everything": lambda v: money(v, True)}, na_rep="–"),
                     hide_index=True, **stretch(st.dataframe))
        st.caption(f"Entry market cap × deposit side × ETH/USDG pool, combinations with {A.FILTER_MIN_TRADES}+ "
                   "closed trades. Past fit, not a promise.")
    else:
        st.caption(f"No filter beats taking every trade (combinations need {A.FILTER_MIN_TRADES}+ closed trades).")

    sizing(a)

    section("How it LPs", f"{a['style'] or ''} · median range {range_fmt(a['range'])} · hold {dur(a['hold'])} · "
                          f"entry mcap {usd(a['mcap'])}")
    c1, c2 = st.columns(2)
    with c1:
        bars(a["sides"], "Deposit side")
        bars(a["range_dist"], "Range width")
    with c2:
        bars(a["hold_dist"], "Hold time", color="#a78bfa")
        bars(a["mcap_dist"], "Entry market cap", color="#a78bfa")
    st.caption("Range = upper / lower price − 1 of a trade (a ladder: lowest to highest). Entry market cap = token "
               f"supply × pool price when it opened ({a['mcap_known']} of {a['trades']} trades known; the rest use "
               "today's).")

    section("Pools", "where the PnL came from")
    pools = pd.DataFrame(a["pools"])
    pools["Win"] = pools.apply(lambda r: r["wins"] / r["closed"] * 100 if r["closed"] else None, axis=1)
    st.dataframe(pools[["pair", "positions", "closed", "open", "pnl", "fees", "dep", "Win"]].style.format(
        {"pnl": lambda v: money(v, True), "fees": usd, "dep": usd, "Win": lambda v: pct(v)}, na_rep="–"),
        hide_index=True, **stretch(st.dataframe), height=auto_height(len(pools), 300),
        column_config={"pair": "Pool", "positions": "Positions", "closed": "Closed", "open": "Open", "pnl": "PnL",
                       "fees": "Fees", "dep": "Deposited"})

    with st.expander(f"Per-position detail ({len(res['positions'])})"):
        pos = pd.DataFrame(res["positions"])
        pos["PnL"] = pos["pnl"] - (pos["cost"].fillna(0) if real else 0)
        view = pd.DataFrame({
            "Pool": pos["pair"], "Opened": pd.to_datetime(pos["open_ts"], unit="s"),
            "Hold": pos.apply(lambda r: dur((r["close_ts"] if pd.notna(r["close_ts"]) else now) - r["open_ts"])
                              + ("" if pd.notna(r["close_ts"]) else " · open"), axis=1),
            "Range": pos.apply(lambda r: range_fmt(A.M.range_pct(r)), axis=1),
            "Side": pos["side"].map(lambda s: A.SIDE_LABEL.get(s, "–")),
            "Mcap": pos["mcap"], "In": pos["dep"], "Fees": pos["fee"], "PnL": pos["PnL"],
            "Closed": [OOR_LABEL.get(p.get("oor", "?")) if p["close_ts"] else "open" for p in res["positions"]],
            "NFT": pos["token_id"].map(lambda t: f"{config.EXPLORER}/token/{config.POSITION_MANAGER}/instance/{t}"),
        }).sort_values("Opened", ascending=False)
        st.dataframe(view.style.format({"Mcap": usd, "In": usd, "Fees": usd, "PnL": lambda v: money(v, True)},
                                       na_rep="–"), hide_index=True, **stretch(st.dataframe), height=360,
                     column_config={"NFT": st.column_config.LinkColumn("NFT", display_text=r"instance/(\d+)$")})
        st.download_button("Download positions (CSV)", view.to_csv(index=False),
                           f"{'_'.join(x[:8] for x in addrs)}_{per.replace(' ', '')}.csv", "text/csv")

    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    if len(addrs) == 1:
        with c1.popover("★ Save wallet"):
            lab = st.text_input("Label (optional)", key=f"lab_{addrs[0]}")
            if st.button("Save", key=f"save_{addrs[0]}"):
                save_wallet(addrs[0], lab.strip() or None)
                st.toast("Saved")
    c2.caption("Share: this page's address opens this analysis (wallet + period are in the link).")


def sizing(a):
    section("Position sizing", "scale its trades to your capital")
    c1, c2 = st.columns(2)
    mine_addr = c2.text_input("Use my wallet (reads ETH + USDG + its open positions)", key="mine_addr",
                              placeholder="0x… your wallet").strip().lower()
    worth = None
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
                held = c.execute("SELECT SUM(now_usd) FROM positions WHERE owner=? AND close_ts IS NULL AND valued=1",
                                 (mine_addr,)).fetchone()[0] or 0.0
                c.close()
                eth = int(eb, 16) / 1e18 if eb else 0.0
                usdg = int(ub, 16) / 1e6 if ub else 0.0
                worth = {"eth": eth, "usdg": usdg, "held": held, "worth": eth * eth_usd + usdg + held}
                st.session_state[f"worth_{mine_addr}"] = worth
            except Exception as e:  # noqa: BLE001
                c2.caption(f"Couldn't read it: {e}")
        if worth:
            c2.caption(esc(f"{worth['eth']:.4f} ETH + {usd(worth['usdg'])} USDG"
                           + (f" + {usd(worth['held'])} in positions we've read" if worth["held"] else "")))
    mine = c1.number_input("Your capital (USD)", min_value=0.0, step=100.0,
                           value=float(round(worth["worth"])) if worth else 0.0, key=f"mine_{bool(worth)}")
    st.caption(esc(f"Its median deployed {money(a['capital'])} · peak {money(a['peak'])} · largest position "
                   f"{money(a['largest'])} · median trade {money(a['size'])} · open now {money(a['open_now'])}"))
    sz = A.sizing(a, mine)
    if sz:
        st.markdown(grid([("Sizing ratio", f"{sz['ratio']:.3f}×", "your capital ÷ its median deployed"),
                          ("Per trade", money(sz["per_trade"]), "its median trade, scaled"),
                          ("Max per position", money(sz["max_position"]), "its largest position, scaled"),
                          ("Peak at once", money(sz["peak"]), "its peak deployed, scaled")]), unsafe_allow_html=True)


def compare(addrs, per):
    real = st.toggle("Real PnL — charge the cost of selling the token back", key="real")
    results = {x: report_data([x], per, real)["analysis"] for x in addrs}
    order = sorted(addrs, key=lambda x: (results[x] is None, {"COPY": 0, "WATCH": 1, "REJECT": 2}.get(
        (results[x] or {}).get("verdict"), 3), -((results[x] or {}).get("roi_30d") or -1e9)))
    st.markdown(f'<div class="done">✓ Batch of {len(addrs)} wallets · {per} · each analysed on its own, best first'
                '</div>', unsafe_allow_html=True)
    recs = []
    for x in order:
        a = results[x]
        if not a:
            recs.append({"Wallet": short(x), "Verdict": "no data"})
            continue
        recs.append({"Wallet": short(x), "Verdict": a["verdict"], "Class": KIND_LABEL[a["kind"]], "Gameplay": a["play"],
                     "Net PnL": a["net"], "Floating": a["float"], "ROI/30d": a["roi_30d"], "Win": a["win"],
                     "PF": min(a["pf"], 99) if a["pf"] is not None else None, "Max DD": a["dd"],
                     "Worst day": a["worst_day"], "OOR loss": a["oor_rate"], "Top-2 days": a["concentration"],
                     "Deployed": a["capital"], "Trade size": a["size"], "Hold": dur(a["hold"]),
                     "Range": range_fmt(a["range"]), "Trades/day": a["per_day"], "Closed": a["closed"],
                     "Follow": FOLLOW[a["follow"]]})
    df = pd.DataFrame(recs)
    vc = {k: f"color: rgb({v}); font-weight: 600" for k, v in VERDICT_COLOR.items()}
    sty = df.style.format({"Net PnL": lambda v: money(v, True), "Floating": lambda v: money(v, True),
                           "ROI/30d": lambda v: pct(v, 1), "Win": lambda v: pct(v), "PF": pf_fmt,
                           "Max DD": lambda v: pct(v, 1), "Worst day": money, "OOR loss": lambda v: pct(v),
                           "Top-2 days": lambda v: pct(v), "Deployed": money, "Trade size": money,
                           "Trades/day": "{:.1f}"}, na_rep="–")
    sty = sty.map(lambda v: vc.get(v, ""), subset=["Verdict"])
    st.dataframe(sty, hide_index=True, **stretch(st.dataframe), height=auto_height(len(df), 560))
    curves = [{"wallet": short(x), "when": pd.to_datetime(d["day"], unit="s"), "cum": d["cum"]}
              for x in order if results[x] for d in results[x]["daily"]]
    if curves:
        section("Cumulative realized PnL")
        st.altair_chart(alt.Chart(pd.DataFrame(curves)).mark_line(strokeWidth=2, interpolate="monotone").encode(
            x=alt.X("when:T", title=None), y=alt.Y("cum:Q", title="USD"), color=alt.Color("wallet:N", title=None),
            tooltip=["wallet", alt.Tooltip("when:T"), alt.Tooltip("cum:Q", format="$,.2f")])
            .properties(height=260).configure_view(strokeWidth=0).configure_axis(**AXIS)
            .configure_legend(labelColor="#b3bdcb"), **stretch(st.altair_chart))
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    pick = c1.selectbox("Open one wallet's full analysis", [""] + order,
                        format_func=lambda x: "Pick a wallet…" if not x else short(x))
    c2.button("Open analysis", disabled=not pick, on_click=open_one, args=(pick,))
    allocation({x: a for x, a in results.items() if a})


def allocation(results):
    section("Split your capital", "how to spread your money over this batch: safety, balanced or aggressive")
    capital = st.number_input("Your capital (USD)", min_value=0.0, value=2500.0, step=100.0, key="alloc_capital")
    if not results or capital <= 0:
        return
    tabs = st.tabs(list(A.PROFILES))
    for tab, name in zip(tabs, A.PROFILES):
        with tab:
            r = A.allocate(results, capital, name)
            st.caption(r["text"])
            if not r["rows"]:
                st.info(f"No wallet in this batch passes the {name} bar: keep the money in cash, or try another "
                        "profile / a bigger batch.")
                continue
            st.markdown(grid([
                ("Into wallets", money(r["deployed"]), f"{len(r['rows'])} wallet(s)"),
                ("Kept in cash", money(r["reserve"]), f"{r['reserve'] / capital * 100:.0f}% buffer"),
                ("If the last 30d repeat", money(r["exp_30d"], True), f"≈{r['exp_30d'] / capital * 100:.1f}% "
                                                                       "of your capital / 30d"),
                ("Drawdown to stomach", money(r["dd_usd"]), "each wallet's max drawdown, on your share"),
            ]), unsafe_allow_html=True)
            df = pd.DataFrame([{"Wallet": short(x["addr"]), "Verdict": x["verdict"], "Share": x["share"],
                                "USD": x["usd"], "Per trade": x["per_trade"], "Max per position": x["max_position"],
                                "ROI/30d": x["roi_30d"], "Max DD": x["dd"],
                                "Note": "trades get tiny: copy only its bigger trades" if x["small"] else ""}
                               for x in r["rows"]])
            st.dataframe(df.style.format({"Share": lambda v: pct(v, 1), "USD": money, "Per trade": money,
                                          "Max per position": money, "ROI/30d": lambda v: pct(v, 1),
                                          "Max DD": lambda v: pct(v, 1)}, na_rep="–"),
                         hide_index=True, **stretch(st.dataframe))
            p = A.PROFILES[name]
            if r["reserve"] > capital * p["reserve"] + 1:
                st.caption(f"More stays in cash than the {p['reserve'] * 100:.0f}% buffer: one wallet gets at most "
                           f"{p['cap'] * 100:.0f}% of the money that goes in, and only {len(r['rows'])} wallet(s) "
                           "qualify. Add more good wallets to the batch to put more to work.")
            if r["skipped"]:
                st.caption("Left out: " + ", ".join(short(x) for x in r["skipped"]))
    ov = A.overlaps(results)
    if ov:
        st.markdown("".join(f'<div class="flag bad"><b>▼ Same pools</b> — {short(x)} and {short(y)} share {j:.0f}% of '
                            f"their pools: copying both doubles the same bets.</div>" for x, y, j in ov),
                    unsafe_allow_html=True)
    st.caption("Per trade / max per position = each wallet's median trade and largest position, scaled by your share "
               "÷ its median deployed capital. Built from past results only: not financial advice, a wallet's next "
               "month can look nothing like its last.")

main()
