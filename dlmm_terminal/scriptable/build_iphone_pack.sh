#!/bin/bash
# Build DLMM-iPhone.zip: the four Scriptable scripts, named exactly as Scriptable needs them, plus the guide PDF.
#   bash dlmm_terminal/scriptable/build_iphone_pack.sh [OUT_DIR]      (default: current directory)
# "MET Secrets.js" is the blank template (no key); the key is typed in on the phone.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$PWD}"
TMP="$(mktemp -d)"
DEST="$TMP/DLMM-iPhone/Scriptable"
mkdir -p "$DEST"
cp "$HERE/DLMM Rules.js" "$HERE/MET Pool.js" "$HERE/MET Portfolio.js" "$DEST/"
cp "$HERE/MET Secrets.example.js" "$DEST/MET Secrets.js"
[ -f "$HERE/Panduan iPhone.pdf" ] && cp "$HERE/Panduan iPhone.pdf" "$TMP/DLMM-iPhone/"
# Nothing personal may ship: the template's key fields must be empty.
if grep -Eq '(SOLANA_PRIVATE_KEY|HELIUS_API_KEY|JUPITER_API_KEY): *"[^"]+"' "$DEST/MET Secrets.js"; then
  echo "refusing: MET Secrets.js has a key filled in" >&2; exit 1
fi
# Scriptable scripts use top-level await, which Node only parses as a module: check a .mjs copy.
i=0
for f in "$DEST"/*.js; do
  i=$((i+1)); cp "$f" "$TMP/check$i.mjs"
  node --check "$TMP/check$i.mjs" >/dev/null 2>&1 || { echo "refusing: $f does not parse" >&2; exit 1; }
done
( cd "$TMP" && rm -f "$OUT/DLMM-iPhone.zip" && zip -qr -X "$OUT/DLMM-iPhone.zip" DLMM-iPhone )
rm -rf "$TMP"
echo "$OUT/DLMM-iPhone.zip"
