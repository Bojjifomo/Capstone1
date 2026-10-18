#!/bin/bash
# Double-click di Finder: buka dashboard RH Wallet Finder di browser.
cd "$(dirname "$0")" || exit 1
exec python3 finder.py
