#!/bin/bash
# Double-click di Finder: buka DLMM Web (pool Meteora DLMM + entry check + portfolio) di browser.
# Biarin jendela Terminal ini kebuka selama dipakai. caffeinate = Mac nggak sleep.
# Dari HP lewat Tailscale: http://<nama-mac-mini>:8790
cd "$(dirname "$0")/.." || exit 1
# First run: make dlmm_terminal/.env from the template and open it in TextEdit to fill in.
if [ ! -f dlmm_terminal/.env ]; then
  cp dlmm_terminal/.env.example dlmm_terminal/.env
  chmod 600 dlmm_terminal/.env
  open -e dlmm_terminal/.env
  echo "dlmm_terminal/.env dibuat dan dibuka di TextEdit. Isi, save (Cmd+S), lalu tekan Enter di sini."
  read -r _
fi
PORT=8790
(sleep 2; open "http://localhost:$PORT") &
exec caffeinate -i python3 -m dlmm_terminal web --host 0.0.0.0 --port "$PORT"
