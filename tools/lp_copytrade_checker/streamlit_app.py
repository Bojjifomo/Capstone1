"""Streamlit web UI for the LP copy-trade checker.

Run locally from the repo root (so the theme in .streamlit/config.toml is picked up):
  ALCHEMY_API_KEY=xxx streamlit run tools/lp_copytrade_checker/streamlit_app.py
On Streamlit Community Cloud, ALCHEMY_API_KEY in Secrets is optional:
visitors can also paste their own key in the form.
"""
import html
import os
import re
import sys
import threading
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checker import (CheckerError, RPCError, analyze, fmt_amounts, plural, short,  # noqa: E402
                     verdict_text)

NETWORKS = {
    "Robinhood Chain": "robinhood-mainnet",
    "Base": "base-mainnet",
    "Ethereum": "eth-mainnet",
    "Arbitrum": "arb-mainnet",
    "Unichain": "unichain-mainnet",
    "BNB Chain": "bnb-mainnet",
}
CACHE_TTL = 600  # seconds; the same wallet isn't rescanned within 10 minutes

# Dark teal look matching the LP Web widget. Colors are also set in
# .streamlit/config.toml, but this CSS keeps the look even if that file isn't read.
CSS = """
<style>
:root {
  --bg: #0c1312; --card: #121a1a; --card-2: #0f1717; --line: #1f2c2b;
  --text: #e6eeec; --muted: #8a9b98; --mint: #34d399; --mint-soft: #7ee2b8;
  --red: #f87171; --amber: #fbbf24;
}
.stApp {
  background: radial-gradient(1100px 520px at 78% -12%, rgba(52, 211, 153, .07), transparent 60%), var(--bg);
  color: var(--text);
}
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1100px; padding-top: 2.6rem; }
.eyebrow {
  display: flex; align-items: center; gap: .7rem; color: var(--mint);
  font-size: .78rem; font-weight: 700; letter-spacing: .18em; text-transform: uppercase;
}
.pill {
  color: #a3b3b0; background: #16201f; border: 1px solid #223030; border-radius: 999px;
  padding: .12rem .65rem; font-size: .74rem; font-weight: 500; letter-spacing: 0; text-transform: none;
}
.title { font-size: 2.3rem; font-weight: 800; color: var(--text); margin: .35rem 0 .2rem; line-height: 1.15; }
.sub { color: var(--muted); font-size: 1.02rem; margin-bottom: 1.2rem; max-width: 46rem; }
[data-testid="stForm"] { background: var(--card); border: 1px solid var(--line); border-radius: 14px; }
[data-testid="stFormSubmitButton"] button, .stButton button {
  background: #15302a; color: var(--mint-soft); border: 1px solid #25493f; font-weight: 600;
}
[data-testid="stFormSubmitButton"] button:hover, .stButton button:hover {
  background: #1a3a33; color: #a7f3d0; border-color: var(--mint);
}
[data-testid="stExpander"] details { background: var(--card); border: 1px solid var(--line); border-radius: 12px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow-x: auto; margin: .4rem 0 1rem; }
.card-head {
  display: flex; justify-content: space-between; gap: 1rem; padding: 1.1rem 1.6rem;
  background: linear-gradient(180deg, #152020, var(--card));
}
.big { font-size: 1.7rem; font-weight: 800; color: var(--text); line-height: 1.2; }
.small { color: var(--muted); font-size: .86rem; }
.right { text-align: right; }
.pos { color: var(--mint); } .neg { color: var(--red); } .warn { color: var(--amber); } .muted { color: var(--muted); }
.strip { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--line); }
.strip div {
  padding: .8rem; text-align: center; font-weight: 700; letter-spacing: .06em; font-size: .9rem;
  border-right: 1px solid var(--line);
}
.strip div:last-child { border-right: 0; }
.note { padding: .75rem 1.6rem; border-top: 1px solid var(--line); color: var(--text); font-size: .95rem; }
.note.muted { color: var(--muted); font-size: .88rem; }
.note b { color: var(--text); }
table.lp { width: 100%; border-collapse: collapse; font-size: .9rem; }
table.lp th {
  text-align: left; color: var(--muted); font-size: .7rem; font-weight: 600; letter-spacing: .1em;
  text-transform: uppercase; padding: .6rem 1.6rem; border-bottom: 1px solid var(--line); background: var(--card-2);
  white-space: nowrap;
}
table.lp td { padding: .7rem 1.6rem; border-bottom: 1px solid var(--line); color: var(--text); vertical-align: top; }
table.lp th, table.lp td { padding-left: .8rem; padding-right: .8rem; }
table.lp th:first-child, table.lp td:first-child { padding-left: 1.6rem; }
table.lp th:last-child, table.lp td:last-child { padding-right: 1.6rem; min-width: 11rem; }
table.lp tr:last-child td { border-bottom: 0; }
table.lp .num { text-align: right; font-variant-numeric: tabular-nums; }
table.lp td.num { white-space: nowrap; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; white-space: nowrap; }
.badge { font-size: .74rem; font-weight: 700; letter-spacing: .04em; padding: .15rem .55rem; border-radius: 6px; }
.badge.COPIER { color: var(--red); background: #3a1c1f; }
.badge.SUSPECT { color: var(--amber); background: #3a2f12; }
.badge.WEAK { color: var(--muted); background: #1a2424; }
.section { color: var(--mint); font-size: .78rem; font-weight: 700; letter-spacing: .18em; text-transform: uppercase; margin: 1.6rem 0 .4rem; }
.legend { color: var(--muted); font-size: .84rem; line-height: 1.6; margin: -.4rem 0 .8rem; }
.legend b { color: var(--text); font-weight: 600; }
.meta { color: var(--muted); font-size: .86rem; margin: .1rem 0 .6rem; }
.meta b { color: var(--text); font-weight: 600; }
@media (max-width: 640px) {
  .card-head { flex-direction: column; } .right { text-align: left; }
  table.lp th:first-child, table.lp td:first-child, .card-head, .note { padding-left: .9rem; }
  table.lp th:last-child, table.lp td:last-child, .card-head, .note { padding-right: .9rem; }
}
</style>
"""

VERDICT_STYLE = {
    "COPIED": ("neg", "COPY-TRADED"),
    "SUSPECT": ("warn", "SUSPICIOUS"),
    "WEAK": ("muted", "PROBABLY CHANCE"),
    "CLEAN": ("pos", "ALL CLEAR"),
}
STATUS_LABEL = {"COPIER": "Copier", "SUSPECT": "Suspect", "WEAK": "Weak"}

st.set_page_config(page_title="LP Copy-Trade Checker", page_icon="🕵️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value))


def server_key():
    try:
        key = st.secrets.get("ALCHEMY_API_KEY")
    except Exception:  # no secrets.toml
        key = None
    return key or os.environ.get("ALCHEMY_API_KEY")


@st.cache_resource
def shared():
    # One scan at a time so the free-tier Alchemy limit holds up when traffic spikes
    return {"lock": threading.Lock(), "cache": {}}


def when(ts):
    return time.strftime("%b %d, %H:%M:%S UTC", time.gmtime(ts))


def seconds(v):
    return f"{v:.1f} s"


def run_check(wallet, network, events, window, api_key, own_key):
    store = shared()
    cache_key = (wallet.lower(), network, events, window)
    hit = store["cache"].get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    url = f"https://{network}.g.alchemy.com/v2/{api_key}"
    bar = st.progress(0.0, text="Starting…")
    # Only scans on the shared key wait in line; visitors with their own key go straight through
    lock = None if own_key else store["lock"]
    if lock and not lock.acquire(blocking=False):
        bar.progress(0.0, text="Someone else is running a check. You're next in line…")
        lock.acquire()
    try:
        res = analyze(url, wallet, events=events, window=window,
                      progress=lambda frac, msg: bar.progress(min(frac, 1.0), text=msg))
    finally:
        if lock:
            lock.release()
        bar.empty()
    store["cache"][cache_key] = (time.time(), res)
    return res


def rpc_error_text(err, network):
    msg = str(err)
    if "authenticated" in msg.lower() or "api key" in msg.lower() or "HTTP 401" in msg:
        return "Alchemy rejected this API key. Double-check it on dashboard.alchemy.com."
    if "not enabled" in msg:
        return (f"{network} isn't enabled on your Alchemy app. Open dashboard.alchemy.com → "
                "your app → Networks, turn it on, then try again.")
    return f"Something went wrong talking to Alchemy: {msg}"


def wallet_cell(addr):
    return f'<span class="mono" title="{esc(addr)}">{esc(short(addr))}</span>'


def table(headers, rows):
    """headers: [(label, is_num)], rows: lists of cell HTML (already escaped)."""
    head = "".join(f'<th class="{"num" if num else ""}">{esc(label)}</th>' for label, num in headers)
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if num else ""}">{cell}</td>'
                         for (_, num), cell in zip(headers, row)) + "</tr>"
        for row in rows)
    return f'<table class="lp"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def render(res):
    tone, label = VERDICT_STYLE[res["verdict"]]
    n_follow = res["n_copy"] + res["n_sus"]
    delay = res["median_delay"]
    react = f"they react in about {seconds(delay)}" if delay is not None else ""
    dumped = (f'<div class="note muted"><b>Dumped by copiers after your closes:</b> '
              f'{esc(fmt_amounts(res["dumped_total"]))}</div>' if res["dumped_total"] else "")
    st.html(f"""
<div class="card">
  <div class="card-head">
    <div>
      <div class="big">{plural(n_follow, 'copier')}</div>
      <div class="small">{esc(short(res['wallet']))} · last {plural(res['n_actions'], 'LP action')} ·
        watched {res['window']:.0f} s after each</div>
    </div>
    <div class="right">
      <div class="big {tone}">{label}</div>
      <div class="small {tone}">{esc(react)}</div>
    </div>
  </div>
  <div class="strip">
    <div class="neg">COPIERS · {res['n_copy']}</div>
    <div class="warn">SUSPECTS · {res['n_sus']}</div>
    <div class="pos">TYPICAL DELAY · {seconds(delay) if delay is not None else "–"}</div>
  </div>
  <div class="note">{esc(verdict_text(res))}</div>
  {dumped}
</div>""")

    if res["followers"]:
        rows = [[
            wallet_cell(f["wallet"]),
            f'<span class="badge {esc(f["status"])}">{STATUS_LABEL[f["status"]]}</span>',
            f'{f["copied"]} of {res["n_actions"]}',
            f'<span class="pos">{f["opens"]}</span> / <span class="neg">{f["closes"]}</span>',
            seconds(f["median_delay"]),
            esc(fmt_amounts(f["dumped"])),
        ] for f in res["followers"]]
        st.html('<div class="section">◆ Who\'s copying</div><div class="card">'
                + table([("Wallet", False), ("Status", False), ("Copied", True),
                         ("Opens / Closes", True), ("Typical delay", True),
                         ("Dumped after your closes", False)], rows)
                + "</div>"
                '<div class="legend"><b>Copier</b>: mirrored 2 or more of your LP actions. '
                "<b>Suspect</b>: copied 2+ of your exact ranges, once. "
                "<b>Weak</b>: a single matching range, could be chance. "
                "<b>Delay</b>: time between your transaction and theirs.</div>")
        with st.expander("Full wallet addresses (copy)"):
            st.code("\n".join(f["wallet"] for f in res["followers"]), language=None)

    st.html('<div class="section">◆ Your recent LP actions</div>')
    for a in reversed(res["actions"]):
        n = len(a["followers"])
        pool = re.sub(r"[^\w./$ -]", "", a["pool"])  # expander labels are rendered as markdown
        verb = "Opened" if a["side"] == "OPEN" else "Closed"
        flag = f"🚨 {plural(n, 'wallet')} copied" if n else "✅ nobody copied"
        with st.expander(f"{when(a['ts'])} · {verb} {pool} · {plural(a['ranges'], 'range')} · {flag}"):
            info = f'<div class="meta">Transaction <span class="mono">{esc(a["tx"])}</span></div>'
            if a["received"]:
                info += f'<div class="meta">You received <b>{esc(fmt_amounts(a["received"]))}</b></div>'
            rows = [[
                wallet_cell(f["wallet"]),
                f'+{seconds(f["delay"])}',
                f'{f["matched"]} of {a["ranges"]}',
                esc(fmt_amounts(f["dumped"])),
            ] for f in a["followers"]]
            table_html = table([("Wallet", False), ("Delay", True), ("Same ranges", True),
                                ("Dumped", False)], rows) if rows else ""
            st.html(info + (f'<div class="card">{table_html}</div>' if rows else ""))

    with st.expander("How to avoid getting dumped on"):
        st.markdown(
            "- **Close and swap in one transaction** (multicall). Bots can only react after your "
            "transaction lands, so they can't dump before your sell.\n"
            "- **Rotate wallets.** The bots follow your wallet address.\n"
            "- **No rush? Wait it out.** Let the dump finish before selling what you got from the close."
        )
    st.caption(f"Block time ≈ {res['block_time']:.3f} s · {res['rpc_calls']} RPC calls")


st.html("""
<div class="eyebrow">◆ LP Copy-Trade Checker <span class="pill">Uniswap V4</span></div>
<div class="title">Is someone copying your LP?</div>
<div class="sub">Find bots that mirror a wallet's LP opens and closes, then dump the token right after it closes.</div>
""")

has_server_key = bool(server_key())
with st.form("check"):
    wallet = st.text_input("Wallet address", placeholder="0x…").strip()
    user_key = st.text_input(
        "Alchemy API key" + (" (optional)" if has_server_key else ""),
        type="password",
        placeholder="Leave empty to use the built-in key" if has_server_key else "Paste your Alchemy API key",
        help="Get a free key at dashboard.alchemy.com and enable the chain you want to check "
             "(for example Robinhood Chain). Your key is only used for this check and is never stored.",
    ).strip()
    c1, c2, c3 = st.columns(3)
    network = c1.selectbox("Chain", list(NETWORKS))
    events = c2.slider("LP actions to check", 3, 15, 6,
                       help="How many of the wallet's most recent LP opens and closes to scan.")
    window = c3.slider("Seconds to watch after each action", 15, 120, 60, step=5,
                       help="Copy bots usually react within 10 seconds.")
    submitted = st.form_submit_button("Check wallet", type="primary")

if submitted:
    key = user_key or server_key()
    if not key:
        st.error("Add an Alchemy API key first. You can get one free at dashboard.alchemy.com "
                 "(enable Robinhood Chain on the app).")
    elif not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
        st.error("That API key doesn't look right. Copy it again from your Alchemy dashboard.")
    else:
        try:
            st.session_state["result"] = run_check(wallet, NETWORKS[network], events, window,
                                                   key, own_key=bool(user_key))
        except CheckerError as e:
            st.session_state.pop("result", None)
            st.warning(str(e))
        except RPCError as e:
            st.session_state.pop("result", None)
            st.error(rpc_error_text(e, network))

if "result" in st.session_state:
    render(st.session_state["result"])
else:
    st.caption("A check takes about 1–3 minutes on Alchemy's free tier. Results are cached for "
               "10 minutes per wallet. Your API key is only used for this check and is never stored.")
