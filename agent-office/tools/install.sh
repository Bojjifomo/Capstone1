#!/bin/sh
# Install (or update) Agent Office on this Mac as a launchd agent.
#   sh tools/install.sh            copy to ~/Projects/agent-office and (re)load the agent
# State files (*.json) already in the target are kept.
set -e

LABEL="com.bojjifomo.agent-office"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Projects/agent-office"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || echo /usr/bin/python3)"

mkdir -p "$DEST/logs" "$HOME/Library/LaunchAgents" "$HOME/Agents"
if [ "$SRC" != "$DEST" ]; then
  rsync -a --exclude '*.json' --exclude 'logs/' --exclude '__pycache__/' --exclude 'docs/' "$SRC/" "$DEST/"
fi
chmod +x "$DEST/tools/office" 2>/dev/null || true

# Find claude for the launchd PATH (launchd starts with a bare PATH).
CLAUDE="$(command -v claude || true)"
for c in "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
  if [ -z "$CLAUDE" ] && [ -x "$c" ]; then CLAUDE="$c"; fi
done
CLAUDE_DIR="$(dirname "${CLAUDE:-/usr/local/bin/claude}")"
NODE_DIR="$(dirname "$(command -v node || echo /opt/homebrew/bin/node)")"
LAUNCH_PATH="$CLAUDE_DIR:$NODE_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Remember the Tailscale MagicDNS name so `tailscale serve` (https) passes the Host allowlist.
TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
TS_NAME=""
if [ -x "$TS" ]; then
  TS_NAME="$("$TS" status --json 2>/dev/null | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
fi
"$PY" - "$DEST" "$CLAUDE" "$TS_NAME" <<'EOF'
import json, sys
from pathlib import Path
dest, claude, ts_name = sys.argv[1:4]
sys.path.insert(0, dest)
import server, store
cfg = server.load_config()
if claude and not cfg.get("claude_bin"):
    cfg["claude_bin"] = claude
if ts_name and ts_name not in cfg["extra_hosts"]:
    cfg["extra_hosts"].append(ts_name)
store.save("config.json", cfg)
print("config:", json.dumps({k: cfg[k] for k in ("bind", "extra_hosts", "claude_bin")}))
EOF

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DEST/server.py</string></array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$LAUNCH_PATH</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DEST/logs/server.log</string>
  <key>StandardErrorPath</key><string>$DEST/logs/server.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo
echo "Agent Office is running:  http://127.0.0.1:7777   and   http://100.89.176.76:7777"
if [ -n "$TS_NAME" ]; then
  echo "For push + home-screen app over https:  tailscale serve --bg --https=443 http://127.0.0.1:7777   ->  https://$TS_NAME"
fi
echo "Restart:  launchctl kickstart -k gui/\$(id -u)/$LABEL"
echo "Logs:     tail -f $DEST/logs/server.log"
