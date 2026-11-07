#!/bin/bash
# Double-click di Finder (Mac mini): jalanin DLMM Web dan buka di browser.
# Biarin jendela Terminal ini kebuka selama dipakai. caffeinate = Mac nggak sleep.
# Dari HP / MacBook lewat Tailscale: http://<nama-mac-mini>:<port>  (port = DLMM_WEB_PORT di .env, default 8891)
cd "$(dirname "$0")/.." || exit 1
# First run: make dlmm_terminal/.env from the template and open it in TextEdit to fill in.
if [ ! -f dlmm_terminal/.env ]; then
  cp dlmm_terminal/.env.example dlmm_terminal/.env
  chmod 600 dlmm_terminal/.env
  open -e dlmm_terminal/.env
  echo "dlmm_terminal/.env dibuat dan dibuka di TextEdit. Isi, save (Cmd+S), lalu tekan Enter di sini."
  read -r _
fi
PORT=$(grep -E '^DLMM_WEB_PORT=[0-9]+' dlmm_terminal/.env | tail -1 | cut -d= -f2 | tr -d '[:space:]')
PORT=${PORT:-8891}
URL="http://localhost:$PORT"
# Port already taken? If it is DLMM Web itself, just open it; anything else: say which app and how to move.
PID=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ -n "$PID" ]; then
  CMD=$(ps -p "$PID" -o command= 2>/dev/null)
  if echo "$CMD" | grep -q "dlmm_terminal web"; then
    echo "DLMM Web udah jalan di $URL (jendela Terminal lain). Buka browser aja."
    open "$URL"
    exit 0
  fi
  echo ""
  echo "Port $PORT kepake app lain: $CMD"
  echo "Ganti port: buka dlmm_terminal/.env, isi misalnya DLMM_WEB_PORT=8892, save, lalu double-click file ini lagi."
  read -r -p "Enter buat tutup." _
  exit 1
fi
HOST=$(scutil --get LocalHostName 2>/dev/null || hostname -s)
echo "DLMM Web: $URL  ·  dari HP / MacBook (Tailscale): http://$HOST:$PORT  ·  biarin jendela ini kebuka."
(sleep 2; open "$URL") &
exec caffeinate -i python3 -m dlmm_terminal web --host 0.0.0.0 --port "$PORT"
