#!/bin/bash
# Double-click di Finder: jalanin RH Terminal dan buka di browser.
# Biarin jendela Terminal ini kebuka selama dipakai. caffeinate = Mac nggak sleep.
# Dari HP / MacBook lewat Tailscale: http://<nama-mac>:<port>  (port = RH_WEB_PORT di .env, default 8892)
cd "$(dirname "$0")/.." || exit 1
echo "Folder: $(pwd)"
# Selalu ambil versi terbaru dari branch yang lagi dipakai (cuma fast-forward: perubahan lokal nggak ditimpa).
if command -v git >/dev/null 2>&1 && [ -d .git ]; then
  BR=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if git fetch -q origin "$BR" 2>/dev/null && git merge -q --ff-only FETCH_HEAD 2>/dev/null; then
    echo "Update: $(git log -1 --format='%h %cs') (branch $BR)"
  else
    echo "⚠ Update otomatis gagal (offline, atau ada perubahan lokal). Jalan pakai versi $(git log -1 --format='%h %cs') branch $BR."
  fi
fi
# First run: make rh_terminal/.env from the template and open it in TextEdit.
if [ ! -f rh_terminal/.env ]; then
  cp rh_terminal/.env.example rh_terminal/.env
  chmod 600 rh_terminal/.env
  open -e rh_terminal/.env
  echo "rh_terminal/.env dibuat dan dibuka di TextEdit. Isi (atau biarin kosong kalau key-nya mau dibaca dari RH Secrets di iCloud), save (Cmd+S), lalu tekan Enter di sini."
  read -r _
fi
# Node + ethers (buat tanda tangan & kirim transaksi): install sekali kalau belum ada.
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js belum ada: jalanin 'brew install node' (atau install LTS dari https://nodejs.org), lalu double-click file ini lagi."
  echo "(Tanpa Node, tabel pool + portfolio tetap jalan, tombol transaksi mati.)"
fi
if command -v node >/dev/null 2>&1 && [ ! -d rh_terminal/executor/node_modules/ethers ]; then
  echo "Install ethers (sekali aja, ±30 detik)…"
  (cd rh_terminal/executor && npm install --no-audit --no-fund) || echo "npm install gagal (lihat error di atas): tombol transaksi bakal mati."
fi
PORT=$(grep -E '^RH_WEB_PORT=[0-9]+' rh_terminal/.env | tail -1 | cut -d= -f2 | tr -d '[:space:]')
PORT=${PORT:-8892}
URL="http://localhost:$PORT"
PID=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ -n "$PID" ]; then
  CMD=$(ps -p "$PID" -o command= 2>/dev/null)
  if echo "$CMD" | grep -q "rh_terminal web"; then
    # A server that is already running keeps the code it started with: stop it so this one
    # (just updated) takes over. Auto exit picks up again from data/auto_exit.json.
    OLD=$(lsof -a -p "$PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    echo "Server lama masih jalan (folder: ${OLD:-?}, pid $PID): dimatiin, diganti versi ini…"
    kill "$PID" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1 || break; sleep 1; done
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then kill -9 "$PID" 2>/dev/null; sleep 1; fi
    PID=""
  fi
fi
if [ -n "$PID" ]; then
  echo ""
  echo "Port $PORT kepake app lain: $CMD"
  echo "Ganti port: buka rh_terminal/.env, isi misalnya RH_WEB_PORT=8894, save, lalu double-click file ini lagi."
  read -r -p "Enter buat tutup." _
  exit 1
fi
HOST=$(scutil --get LocalHostName 2>/dev/null || hostname -s)
echo "RH Terminal: $URL  ·  dari HP / MacBook (Tailscale): http://$HOST:$PORT  ·  biarin jendela ini kebuka."
(sleep 3; open "$URL") &
exec caffeinate -i python3 -m rh_terminal web --host 0.0.0.0 --port "$PORT"
