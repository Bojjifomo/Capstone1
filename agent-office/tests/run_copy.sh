#!/bin/sh
# Run a throwaway copy of Agent Office on :7790 against a fake $HOME (never touches the live one).
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${1:-${TMPDIR:-/tmp}/agent-office-test}"
rm -rf "$WORK"
mkdir -p "$WORK/home"
cp -R "$HERE" "$WORK/app"
rm -f "$WORK/app"/*.json
python3 "$HERE/tests/fixtures.py" "$WORK/home" >/dev/null
cd "$WORK/app"
HOME="$WORK/home" exec python3 server.py --port 7790 --bind 127.0.0.1 --dev
