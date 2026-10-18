#!/bin/bash
# Double-click di Finder: buka RH Wallet Finder (Streamlit) di browser.
# Biarin jendela Terminal ini kebuka: sync jalan di sini. caffeinate = Mac nggak sleep.
cd "$(dirname "$0")" || exit 1
if ! python3 -c "import streamlit" 2>/dev/null; then
  echo "Install Streamlit (sekali aja)…"
  python3 -m pip install --user -r requirements.txt || exit 1
fi
exec caffeinate -i python3 finder.py
