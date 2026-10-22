#!/bin/bash
# Double-click on the Mac: installs Streamlit the first time, then opens RH LP Analyzer in the browser.
cd "$(dirname "$0")" || exit 1
python3 -m pip install -q -r requirements.txt || exit 1
exec python3 -m streamlit run streamlit_app.py --server.port 8502
