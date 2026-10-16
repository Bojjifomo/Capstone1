"""Web UI Streamlit untuk LP copy-trade checker.

Jalanin lokal:
  ALCHEMY_API_KEY=xxx streamlit run streamlit_app.py
Deploy ke Streamlit Community Cloud: isi ALCHEMY_API_KEY di Secrets.
"""
import os
import sys
import threading
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checker import CheckerError, RPCError, analyze, fmt_amounts, verdict_text  # noqa: E402

NETWORKS = {
    "Robinhood Chain": "robinhood-mainnet",
    "Base": "base-mainnet",
    "Ethereum": "eth-mainnet",
    "Arbitrum": "arb-mainnet",
    "Unichain": "unichain-mainnet",
    "BNB Chain": "bnb-mainnet",
}
CACHE_TTL = 600  # detik; wallet yang sama nggak di-scan ulang dalam 10 menit

st.set_page_config(page_title="LP Copy-Trade Checker", page_icon="🕵️", layout="wide")


def api_key():
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


def run_check(wallet, network, events, window):
    store = shared()
    key = (wallet.lower(), network, events, window)
    hit = store["cache"].get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    url = f"https://{network}.g.alchemy.com/v2/{api_key()}"
    bar = st.progress(0.0, text="Mulai...")
    if not store["lock"].acquire(blocking=False):
        bar.progress(0.0, text="Lagi ada yang ngecek wallet lain, lu masuk antrian...")
        store["lock"].acquire()
    try:
        res = analyze(url, wallet, events=events, window=window,
                      progress=lambda frac, msg: bar.progress(min(frac, 1.0), text=msg))
    finally:
        store["lock"].release()
        bar.empty()
    store["cache"][key] = (time.time(), res)
    return res


def render(res):
    show = {"COPY": st.error, "CURIGA": st.warning, "LEMAH": st.info, "AMAN": st.success}
    show[res["verdict"]](verdict_text(res))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Wallet copy-trader", res["n_copy"], border=True)
    m2.metric("Wallet curiga", res["n_sus"], border=True)
    m3.metric("Median delay bot",
              f"{res['median_delay']:.1f} dtk" if res["median_delay"] is not None else "-", border=True)
    m4.metric("Aksi LP dicek", res["n_actions"], border=True)
    if res["dumped_total"]:
        st.markdown(f"**Total token yang dilepas follower setelah lu close:** {fmt_amounts(res['dumped_total'])}")

    if res["followers"]:
        st.subheader("Wallet yang ngikutin")
        st.dataframe([{
            "Wallet": f["wallet"],
            "Status": f["status"],
            "Ikut": f"{f['copied']}/{res['n_actions']}",
            "Open": f["opens"],
            "Close": f["closes"],
            "Median delay (dtk)": round(f["median_delay"], 1),
            "Dump setelah lu close": fmt_amounts(f["dumped"]),
        } for f in res["followers"]], hide_index=True)

    st.subheader("Timeline aksi LP")
    for a in reversed(res["actions"]):
        n = len(a["followers"])
        flag = f"🚨 {n} wallet ngikutin" if n else "✅ aman"
        with st.expander(f"{utc(a['ts'])} UTC · {a['side']} {a['pool']} · {a['ranges']} range · {flag}"):
            st.caption(f"tx {a['tx']}")
            if a["received"]:
                st.write("Lu terima: " + ", ".join(f"{v:,.2f} {s}" for s, v in a["received"].items()))
            if n:
                st.dataframe([{
                    "Wallet": f["wallet"],
                    "Delay (dtk)": round(f["delay"], 1),
                    "Range sama": f"{f['matched']}/{a['ranges']}",
                    "Token dilepas": fmt_amounts(f["dumped"]),
                } for f in a["followers"]], hide_index=True)

    with st.expander("Tips biar nggak diguyur bot"):
        st.markdown(
            "- **Close LP + swap dalam 1 transaksi** (multicall). Bot baru bisa bereaksi setelah tx lu "
            "masuk block, jadi kalau jualnya di tx yang sama, mereka nggak bisa dump duluan.\n"
            "- **Ganti wallet** secara berkala. Bot ngikutin alamat wallet lu.\n"
            "- Kalau nggak buru-buru, **tunggu dump-nya selesai** baru jual token hasil close."
        )
    st.caption(f"Window {res['window']:.0f} dtk setelah tiap aksi · block time ~{res['block_time']:.3f} dtk "
               f"· {res['rpc_calls']} RPC call")


st.title("🕵️ LP Copy-Trade Checker")
st.write("Cek apakah ada bot yang ngikutin **open/close LP Uniswap V4** sebuah wallet, "
         "lalu dump token begitu wallet itu close.")

with st.form("check"):
    wallet = st.text_input("Wallet address", placeholder="0x...").strip()
    c1, c2, c3 = st.columns(3)
    network = c1.selectbox("Chain", list(NETWORKS))
    events = c2.slider("Jumlah aksi LP terakhir", 3, 15, 6)
    window = c3.slider("Window scan (detik setelah aksi)", 15, 120, 60, step=5)
    submitted = st.form_submit_button("Cek wallet", type="primary")

if submitted:
    if not api_key():
        st.error("ALCHEMY_API_KEY belum diset (Secrets di Streamlit Cloud atau env var).")
    else:
        try:
            st.session_state["result"] = run_check(wallet, NETWORKS[network], events, window)
        except CheckerError as e:
            st.session_state.pop("result", None)
            st.warning(str(e))
        except RPCError as e:
            st.session_state.pop("result", None)
            st.error(f"RPC error: {e}. Pastikan network {network} udah di-enable di app Alchemy lu.")

if "result" in st.session_state:
    render(st.session_state["result"])
else:
    st.caption("Scan butuh ~1–2 menit di Alchemy free tier. Hasil disimpan 10 menit per wallet.")
