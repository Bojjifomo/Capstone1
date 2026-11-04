#!/bin/bash
# Build DLMM-Terminal.zip to send to someone: the dlmm_terminal package (no data, no .env,
# no node_modules, no phone scripts), the two launchers and the guide, under DLMM-Terminal/.
#   bash dlmm_terminal/share/build_share.sh [OUT_DIR]      (default: current directory)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PKG="$(dirname "$HERE")"
OUT="${1:-$PWD}"
TMP="$(mktemp -d)"
DEST="$TMP/DLMM-Terminal"
mkdir -p "$DEST"
python3 - "$PKG" "$DEST/dlmm_terminal" <<'PY'
import shutil, sys
skip = shutil.ignore_patterns("data", ".env", "node_modules", "__pycache__", "*.pyc", "scriptable", "share", "Buka DLMM Web.command")
shutil.copytree(sys.argv[1], sys.argv[2], ignore=skip)
PY
cp "$HERE/Mulai DLMM Terminal.command" "$HERE/Mulai DLMM Terminal.bat" "$HERE/PANDUAN.md" "$DEST/"
[ -f "$HERE/Panduan DLMM Terminal.pdf" ] && cp "$HERE/Panduan DLMM Terminal.pdf" "$DEST/"
chmod +x "$DEST/Mulai DLMM Terminal.command"
# Nothing personal may ship: no key file, no logs.
if find "$DEST" -name '.env' -o -name '*.jsonl' | grep -q .; then echo "refusing: secrets/logs in package" >&2; exit 1; fi
( cd "$TMP" && rm -f "$OUT/DLMM-Terminal.zip" && zip -qr -X "$OUT/DLMM-Terminal.zip" DLMM-Terminal )
rm -rf "$TMP"
echo "$OUT/DLMM-Terminal.zip"
