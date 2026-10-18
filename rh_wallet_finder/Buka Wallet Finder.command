#!/bin/bash
# Double-click di Finder: buka RH Wallet Finder (Streamlit) di browser.
# Biarin jendela Terminal ini kebuka: sync jalan di sini. caffeinate = Mac nggak sleep.
cd "$(dirname "$0")" || exit 1
# Streamlit belum ada, atau versinya kelamaan (mis. bawaan Anaconda): install / upgrade dulu.
if ! python3 -c "import streamlit, sys; v = tuple(int(x) for x in streamlit.__version__.split('.')[:2]); sys.exit(v < (1, 40))" 2>/dev/null; then
  echo "Install / upgrade Streamlit (sekali aja)…"
  python3 -m pip install -U -r requirements.txt || exit 1
fi
exec caffeinate -i python3 finder.py
