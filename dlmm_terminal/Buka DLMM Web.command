#!/bin/bash
# Double-click di Finder: buka DLMM Web (pool Meteora DLMM + entry check + portfolio) di browser.
# Biarin jendela Terminal ini kebuka selama dipakai. caffeinate = Mac nggak sleep.
# Dari HP lewat Tailscale: http://<nama-mac-mini>:8790
cd "$(dirname "$0")/.." || exit 1
PORT=8790
(sleep 2; open "http://localhost:$PORT") &
exec caffeinate -i python3 -m dlmm_terminal web --host 0.0.0.0 --port "$PORT"
