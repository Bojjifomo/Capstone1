#!/bin/sh
# Install (or update) Fund on this Mac as a launchd agent.
#   sh tools/install.sh      copy to ~/Projects/fund, (re)load
# config.json and fund.db already in the target are kept.
set -e

LABEL="com.bojjifomo.fund"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Projects/fund"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || echo /usr/bin/python3)"

mkdir -p "$DEST/logs" "$HOME/Library/LaunchAgents"
if [ "$SRC" != "$DEST" ]; then
  rsync -a --exclude 'config.json' --exclude '*.db' --exclude '*.db-*' --exclude 'logs/' \
    --exclude '__pycache__/' --exclude 'tests/' "$SRC/" "$DEST/"
fi

# First install: a config that already knows where the Wallet Finder database is (for LP).
if [ ! -f "$DEST/config.json" ]; then
  WF="$(cd "$SRC/.." && pwd)/rh_wallet_finder/data/wallets.db"
  cat > "$DEST/config.json" <<JSON
{
  "port": 7799,
  "token": "",
  "lp_wallets": [],
  "lp_db": "$WF"
}
JSON
  echo "config.json dibikin di $DEST (isi lp_wallets kalau mau LP auto-track)"
fi

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DEST/server.py</string></array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
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
TSIP="$("$PY" -c "import sys; sys.path.insert(0, '$DEST'); import server; print(server.tailscale_ip() or '')")"
sleep 2
echo
echo "Fund jalan."
echo "  Mac:            http://127.0.0.1:$PORT"
if [ -n "$TSIP" ]; then
  echo "  HP (Tailscale): http://$TSIP:$PORT"
else
  echo "  HP: nyalain Tailscale di Mac dulu, terus buka http://<ip-tailscale-mac>:$PORT"
fi
echo "  CLI:            python3 $DEST/fund.py report"
echo "  Log:            $DEST/logs/server.log"
tail -n 3 "$DEST/logs/server.log" 2>/dev/null || true
