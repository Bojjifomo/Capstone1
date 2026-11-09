#!/bin/bash
# Double-click di Finder: jalanin Elysium Terminal dan buka di browser.
# Biarin jendela Terminal ini kebuka. caffeinate = Mac nggak sleep.
# Dari HP / MacBook lewat Tailscale: http://<nama-mac>:<port>  (ELYSIUM_WEB_PORT, default 8893)
# Bisa jalan bareng RH Terminal (port 8892). Jangan pakai private key yang sama kalau nggak memang mau.
cd "$(dirname "$0")/.." || exit 1
if [ ! -f elysium_terminal/.env ]; then
  cp elysium_terminal/.env.example elysium_terminal/.env
  chmod 600 elysium_terminal/.env
  open -e elysium_terminal/.env
  echo "elysium_terminal/.env dibuat dan dibuka di TextEdit. Isi ELYSIUM_PRIVATE_KEY (wallet LP Elysium, bukan key Robinhood), save (Cmd+S), lalu tekan Enter di sini."
  read -r _
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js belum ada: 'brew install node'. Tabel pool tetap jalan, tombol transaksi mati."
fi
if command -v node >/dev/null 2>&1 && [ ! -d elysium_terminal/executor/node_modules/ethers ]; then
  echo "Install ethers (sekali aja)…"
  (cd elysium_terminal/executor && npm install --no-audit --no-fund) || echo "npm install gagal: tombol transaksi bakal mati."
fi
PORT=$(grep -E '^ELYSIUM_WEB_PORT=[0-9]+' elysium_terminal/.env | tail -1 | cut -d= -f2 | tr -d '[:space:]')
PORT=${PORT:-8893}
URL="http://localhost:$PORT"
PID=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ -n "$PID" ]; then
  CMD=$(ps -p "$PID" -o command= 2>/dev/null)
  if echo "$CMD" | grep -q "elysium_terminal web"; then
    echo "Elysium Terminal udah jalan di $URL. Buka browser aja."
    open "$URL"
    exit 0
  fi
  echo "Port $PORT kepake app lain: $CMD"
  echo "Ganti port: isi ELYSIUM_WEB_PORT=8895 di elysium_terminal/.env, save, double-click lagi."
  read -r -p "Enter buat tutup." _
  exit 1
fi
HOST=$(scutil --get LocalHostName 2>/dev/null || hostname -s)
echo "Elysium Terminal: $URL  ·  dari HP (Tailscale): http://$HOST:$PORT  ·  biarin jendela ini kebuka."
(sleep 3; open "$URL") &
exec caffeinate -i python3 -m elysium_terminal web --host 0.0.0.0 --port "$PORT"
