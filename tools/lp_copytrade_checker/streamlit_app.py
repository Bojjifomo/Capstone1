"""Web UI Streamlit untuk LP copy-trade checker.

Jalanin lokal (dari root repo, biar tema di .streamlit/config.toml kebaca):
  ALCHEMY_API_KEY=xxx streamlit run tools/lp_copytrade_checker/streamlit_app.py
Deploy ke Streamlit Community Cloud: isi ALCHEMY_API_KEY di Secrets (opsional;
pengunjung juga bisa isi key sendiri di form).
"""
import html
import os
import re
import sys
import threading
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checker import CheckerError, RPCError, analyze, fmt_amounts, short, verdict_text  # noqa: E402

NETWORKS = {
    "Robinhood Chain": "robinhood-mainnet",
    "Base": "base-mainnet",
    "Ethereum": "eth-mainnet",
    "Arbitrum": "arb-mainnet",
    "Unichain": "unichain-mainnet",
    "BNB Chain": "bnb-mainnet",
}
CACHE_TTL = 600  # detik; wallet yang sama nggak di-scan ulang dalam 10 menit

# Gaya gelap hijau-teal ala LP Web. Warna juga diset di .streamlit/config.toml,
# tapi CSS ini yang jaga tampilan tetap sama walau config nggak kebaca.
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
.sub { color: var(--muted); font-size: 1.02rem; margin-bottom: 1.2rem; }
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
.note { padding: .75rem 1.6rem; border-top: 1px solid var(--line); color: var(--muted); font-size: .88rem; }
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
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .8rem; white-space: nowrap; }
table.lp td.num { white-space: nowrap; }
.badge { font-size: .72rem; font-weight: 700; letter-spacing: .06em; padding: .15rem .5rem; border-radius: 6px; }
.badge.COPY { color: var(--red); background: #3a1c1f; }
.badge.CURIGA { color: var(--amber); background: #3a2f12; }
.badge.lemah { color: var(--muted); background: #1a2424; }
.section { color: var(--mint); font-size: .78rem; font-weight: 700; letter-spacing: .18em; text-transform: uppercase; margin: 1.4rem 0 .4rem; }
@media (max-width: 640px) {
  .card-head { flex-direction: column; } .right { text-align: left; }
  table.lp th, table.lp td, .card-head, .note { padding-left: .9rem; padding-right: .9rem; }
}
</style>
"""

VERDICT_STYLE = {
    "COPY": ("neg", "DI-COPYTRADE"),
    "CURIGA": ("warn", "CURIGA"),
    "LEMAH": ("muted", "KEMUNGKINAN KEBETULAN"),
    "AMAN": ("pos", "AMAN"),
}

st.set_page_config(page_title="LP Copy-Trade Checker", page_icon="🕵️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value))


def server_key():
    try:
        key = st.secrets.get("ALCHEMY_API_KEY")
    except Exception:  # belum ada secrets.toml
        key = None
    return key or os.environ.get("ALCHEMY_API_KEY")


@st.cache_resource
def shared():
    # Satu scan sekali jalan biar limit Alchemy free tier nggak jebol kalau rame
    return {"lock": threading.Lock(), "cache": {}}


def utc(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


def run_check(wallet, network, events, window, api_key, own_key):
    store = shared()
    cache_key = (wallet.lower(), network, events, window)
    hit = store["cache"].get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    url = f"https://{network}.g.alchemy.com/v2/{api_key}"
    bar = st.progress(0.0, text="Mulai...")
    # Antrian cuma buat key server; yang bawa key sendiri langsung jalan
    lock = None if own_key else store["lock"]
    if lock and not lock.acquire(blocking=False):
        bar.progress(0.0, text="Lagi ada yang ngecek wallet lain, lu masuk antrian...")
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
        return "API key Alchemy ditolak. Cek lagi key-nya (copy dari dashboard.alchemy.com)."
    if "not enabled" in msg:
        return (f"Network {network} belum di-enable di app Alchemy lu. "
                "Buka dashboard.alchemy.com → app lu → Networks, aktifin, terus coba lagi.")
    return f"RPC error: {msg}"


def table(headers, rows):
    """headers: [(label, is_num)], rows: list of cell HTML (sudah di-escape)."""
    head = "".join(f'<th class="{"num" if num else ""}">{esc(label)}</th>' for label, num in headers)
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if num else ""}">{cell}</td>'
                         for (_, num), cell in zip(headers, row)) + "</tr>"
        for row in rows)
    return f'<table class="lp"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def render(res):
    tone, label = VERDICT_STYLE[res["verdict"]]
    n_follow = res["n_copy"] + res["n_sus"]
    delay = f"median {res['median_delay']:.1f} dtk setelah aksi lu" if res["median_delay"] is not None else ""
    dumped = (f'<div class="note"><b>Dilepas follower setelah lu close:</b> {esc(fmt_amounts(res["dumped_total"]))}</div>'
              if res["dumped_total"] else "")
    st.html(f"""
<div class="card">
  <div class="card-head">
    <div>
      <div class="big">{n_follow} wallet ngikutin</div>
      <div class="small">{esc(short(res['wallet']))} · {res['n_actions']} aksi LP dicek · window {res['window']:.0f} dtk</div>
    </div>
    <div class="right">
      <div class="big {tone}">{label}</div>
      <div class="small {tone}">{esc(delay)}</div>
    </div>
  </div>
  <div class="strip">
    <div class="neg">COPY · {res['n_copy']}</div>
    <div class="warn">CURIGA · {res['n_sus']}</div>
    <div class="pos">MEDIAN · {f"{res['median_delay']:.1f} dtk" if res['median_delay'] is not None else "-"}</div>
  </div>
  <div class="note">{esc(verdict_text(res))}</div>
  {dumped}
</div>""")

    if res["followers"]:
        rows = [[
            f'<span class="mono">{esc(f["wallet"])}</span>',
            f'<span class="badge {esc(f["status"])}">{esc(f["status"])}</span>',
            f'{f["copied"]}/{res["n_actions"]}',
            f'<span class="pos">{f["opens"]}</span> / <span class="neg">{f["closes"]}</span>',
            f'{f["median_delay"]:.1f} dtk',
            esc(fmt_amounts(f["dumped"])),
        ] for f in res["followers"]]
        st.html('<div class="section">◆ Wallet yang ngikutin</div><div class="card">'
                + table([("Wallet", False), ("Status", False), ("Ikut", True),
                         ("Open / Close", True), ("Median delay", True), ("Dump setelah lu close", False)], rows)
                + "</div>")

    st.html('<div class="section">◆ Timeline aksi LP</div>')
    for a in reversed(res["actions"]):
        n = len(a["followers"])
        pool = re.sub(r"[^\w./$ -]", "", a["pool"])  # label expander dirender markdown
        flag = f"🚨 {n} wallet ngikutin" if n else "✅ aman"
        with st.expander(f"{utc(a['ts'])} UTC · {a['side']} {pool} · {a['ranges']} range · {flag}"):
            info = f'<div class="small mono">tx {esc(a["tx"])}</div>'
            if a["received"]:
                got = ", ".join(f"{v:,.2f} {s}" for s, v in a["received"].items())
                info += f'<div class="small">Lu terima: {esc(got)}</div>'
            rows = [[
                f'<span class="mono">{esc(f["wallet"])}</span>',
                f'+{f["delay"]:.1f} dtk',
                f'{f["matched"]}/{a["ranges"]}',
                esc(fmt_amounts(f["dumped"])),
            ] for f in a["followers"]]
            st.html(info + (f'<div class="card">{table([("Wallet", False), ("Delay", True), ("Range sama", True), ("Token dilepas", False)], rows)}</div>' if rows else ""))

    with st.expander("Tips biar nggak diguyur bot"):
        st.markdown(
            "- **Close LP + swap dalam 1 transaksi** (multicall). Bot baru bisa bereaksi setelah tx lu "
            "masuk block, jadi kalau jualnya di tx yang sama, mereka nggak bisa dump duluan.\n"
            "- **Ganti wallet** secara berkala. Bot ngikutin alamat wallet lu.\n"
            "- Kalau nggak buru-buru, **tunggu dump-nya selesai** baru jual token hasil close."
        )
    st.caption(f"Block time ~{res['block_time']:.3f} dtk · {res['rpc_calls']} RPC call")


st.html("""
<div class="eyebrow">◆ LP Copy-Trade Checker <span class="pill">Uniswap V4</span></div>
<div class="title">Ada yang nyontek LP lu?</div>
<div class="sub">Cek bot yang ngikutin open/close LP sebuah wallet, lalu dump token begitu wallet itu close.</div>
""")

has_server_key = bool(server_key())
with st.form("check"):
    wallet = st.text_input("Wallet address", placeholder="0x...").strip()
    user_key = st.text_input(
        "Alchemy API key" + (" (opsional)" if has_server_key else ""),
        type="password",
        placeholder="Kosongin buat pakai key bawaan" if has_server_key else "Paste API key Alchemy lu",
        help="Bikin gratis di dashboard.alchemy.com, lalu enable network yang mau dicek "
             "(misalnya Robinhood Chain). Key cuma dipakai buat scan ini dan nggak disimpan.",
    ).strip()
    c1, c2, c3 = st.columns(3)
    network = c1.selectbox("Chain", list(NETWORKS))
    events = c2.slider("Jumlah aksi LP terakhir", 3, 15, 6)
    window = c3.slider("Window scan (detik setelah aksi)", 15, 120, 60, step=5)
    submitted = st.form_submit_button("Cek wallet", type="primary")

if submitted:
    key = user_key or server_key()
    if not key:
        st.error("Isi Alchemy API key dulu. Bikin gratis di dashboard.alchemy.com "
                 "dan enable network Robinhood Chain di app-nya.")
    elif not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
        st.error("Format API key nggak valid. Copy ulang key-nya dari dashboard Alchemy.")
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
    st.caption("Scan butuh ~1–3 menit di Alchemy free tier. Hasil disimpan 10 menit per wallet. "
               "API key yang lu isi cuma dipakai buat scan ini, nggak disimpan.")
