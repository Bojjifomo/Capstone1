#!/bin/sh
# Jalanin LP Call Bot terus-terusan di Mac lewat launchd: nyala sendiri tiap login, restart sendiri kalau mati.
#   sh lp_call_bot/install.sh            pasang / update
#   sh lp_call_bot/install.sh uninstall  copot
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.bojjifomo.lp-call-bot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || echo /usr/bin/python3)"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "uninstall" ]; then
  rm -f "$PLIST"
  echo "LP Call Bot dicopot dari launchd."
  exit 0
fi
if [ ! -f "$HERE/.env" ]; then
  echo "Isi dulu $HERE/.env (cp .env.example .env, isi TELEGRAM_BOT_TOKEN dan OWNER_IDS)." >&2
  exit 1
fi
# Bot lama yang jalan pakai nohup harus dimatiin dulu, biar nggak dobel narik update Telegram.
if pgrep -f "lp_call_bot/bot.py" >/dev/null 2>&1; then
  echo "Bot lama (nohup) masih jalan. Matiin dulu:  pkill -f lp_call_bot/bot.py   lalu jalanin script ini lagi." >&2
  exit 1
fi
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$HERE/bot.py</string></array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$HERE/bot.log</string>
  <key>StandardErrorPath</key><string>$HERE/bot.log</string>
</dict>
</plist>
PL
for i in 1 2 3 4 5 6 7 8 9 10; do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 1
done
for i in 1 2 3 4 5; do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
    echo "LP Call Bot jalan lewat launchd ($LABEL)."
    echo "Restart:  launchctl kickstart -k gui/\$(id -u)/$LABEL"
    echo "Log:      tail -f $HERE/bot.log"
    echo "Sehat kalau $HERE/heartbeat diperbarui tiap ~1 menit."
    exit 0
  fi
  sleep 2
done
echo "Gagal start. Coba:  launchctl bootstrap gui/\$(id -u) $PLIST" >&2
exit 1
