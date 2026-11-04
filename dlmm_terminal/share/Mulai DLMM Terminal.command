#!/bin/bash
# DLMM Terminal — double-click to start (macOS).
# First run: checks Python + Node, installs the Meteora SDK, makes dlmm_terminal/.env and
# opens it in TextEdit. Then starts the web on http://localhost:8790 and opens the browser.
# Keep this Terminal window open while you use it. Ctrl+C stops it.
cd "$(dirname "$0")" || exit 1
say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  say "Python 3.9+ belum ada. Install dari https://www.python.org/downloads/ lalu jalanin file ini lagi."
  open "https://www.python.org/downloads/"; read -r -p "Enter buat tutup." _; exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  say "Node.js belum ada. Install versi LTS dari https://nodejs.org/ lalu jalanin file ini lagi."
  open "https://nodejs.org/"; read -r -p "Enter buat tutup." _; exit 1
fi
if [ ! -d dlmm_terminal/executor/node_modules/@meteora-ag/dlmm ]; then
  say "Install Meteora SDK (sekali aja, ±1 menit)…"
  (cd dlmm_terminal/executor && npm install --no-audit --no-fund) || { read -r -p "npm install gagal. Enter buat tutup." _; exit 1; }
fi
# macOS Python from python.org sometimes lacks HTTPS certificates; certifi fixes that.
"$PY" -c "import certifi" 2>/dev/null || "$PY" -m pip install --user --quiet certifi 2>/dev/null
if [ ! -f dlmm_terminal/.env ]; then
  cp dlmm_terminal/.env.example dlmm_terminal/.env && chmod 600 dlmm_terminal/.env
  open -e dlmm_terminal/.env
  say "dlmm_terminal/.env dibuka di TextEdit. Isi (lihat PANDUAN), save Cmd+S, lalu tekan Enter di sini."
  read -r _
fi
say "DLMM Terminal jalan di http://localhost:8790 — biarin jendela ini kebuka. Ctrl+C buat stop."
(sleep 2; open "http://localhost:8790") &
exec "$PY" -m dlmm_terminal web --port 8790
