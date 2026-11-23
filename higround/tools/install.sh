#!/bin/sh
# Install (or update) HiGround Run Club on this Mac as a launchd agent.
#   sh tools/install.sh      copy to ~/Projects/higround, (re)load
# config.json and higround.db already in the target are kept.
set -e

LABEL="com.bojjifomo.higround"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Projects/higround"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || echo /usr/bin/python3)"

mkdir -p "$DEST/logs" "$HOME/Library/LaunchAgents"
if [ "$SRC" != "$DEST" ]; then
  rsync -a --exclude 'config.json' --exclude '*.db' --exclude '*.db-*' --exclude 'logs/' \
    --exclude '__pycache__/' --exclude 'tests/' "$SRC/" "$DEST/"
fi

if [ ! -f "$DEST/config.json" ]; then
  cp "$SRC/config.example.json" "$DEST/config.json"
  echo "config.json dibikin di $DEST: isi public_url, intervals_client_id/secret, dan admins"
fi
chmod 600 "$DEST/config.json"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DEST/server.py</string></array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DEST/logs/server.log</string>
  <key>StandardErrorPath</key><string>$DEST/logs/server.log</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for i in 1 2 3 4 5 6 7 8 9 10; do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 1
done
launchctl bootstrap "gui/$(id -u)" "$PLIST"

PORT="$("$PY" -c "import sys; sys.path.insert(0, '$DEST'); import server; print(server.load_config()['port'])")"
sleep 2
echo
echo "HiGround Run Club jalan di http://127.0.0.1:$PORT"
echo "Biar member bisa buka dari internet (sekali aja):"
echo "  tailscale funnel --bg $PORT"
echo "Log: $DEST/logs/server.log"
tail -n 3 "$DEST/logs/server.log" 2>/dev/null || true
