#!/bin/sh
# Install (or update) RPC Monitor on this Mac as a launchd agent.
#   sh rpc_monitor/tools/install.sh
# Runs in place (not copied): it reads the bots' .env files next to it in this repo.
set -e

LABEL="com.bojjifomo.rpc-monitor"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || echo /usr/bin/python3)"
LOG="$HERE/logs/server.log"

mkdir -p "$HERE/logs" "$HOME/Library/LaunchAgents" "$HOME/.rpc-monitor"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$HERE/server.py</string></array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PL

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for i in 1 2 3 4 5 6 7 8 9 10; do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 1
done
launchctl bootstrap "gui/$(id -u)" "$PLIST"

PORT="$("$PY" -c "import sys; sys.path.insert(0, '$HERE'); import server; print(server.load_config()['port'])")"
TSIP="$("$PY" -c "import sys; sys.path.insert(0, '$HERE'); import server; print(server.tailscale_ip() or '')")"
sleep 3
echo
echo "RPC Monitor jalan."
echo "  Mac:            http://127.0.0.1:$PORT"
if [ -n "$TSIP" ]; then
  echo "  HP (Tailscale): http://$TSIP:$PORT"
else
  echo "  HP: nyalain Tailscale di Mac dulu, terus buka http://<ip-tailscale-mac>:$PORT"
fi
echo "  Log:            $LOG"
echo
echo "Penting: restart bot-bot yang lagi jalan biar meter-nya kepake (lihat README)."
tail -n 3 "$LOG" 2>/dev/null || true
