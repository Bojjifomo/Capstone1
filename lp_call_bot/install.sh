#!/bin/sh
# Jalanin LP Call Bot terus-terusan di Mac lewat launchd: nyala sendiri tiap login, restart sendiri kalau mati.
#   sh lp_call_bot/install.sh            pasang / update
#   sh lp_call_bot/install.sh uninstall  copot
#
# Bot dikopi ke ~/Projects/lp_call_bot dan jalan dari situ. launchd nggak bisa membuka log atau folder di disk
# eksternal (/Volumes/...): job-nya gagal start dengan "spawn failed", exit code 78 (EX_CONFIG).
# .env dan state.json (subscriber + posisi) cuma dikopi kalau di tujuan belum ada, jadi update nggak menimpanya.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
HERE="${LP_CALL_BOT_HOME:-$HOME/Projects/lp_call_bot}"
LABEL="com.bojjifomo.lp-call-bot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || echo /usr/bin/python3)"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "uninstall" ]; then
  rm -f "$PLIST"
  echo "LP Call Bot dicopot dari launchd."
  exit 0
fi
if [ ! -f "$HERE/.env" ] && [ ! -f "$SRC/.env" ]; then
  echo "Isi dulu $SRC/.env (cp .env.example .env, isi TELEGRAM_BOT_TOKEN dan OWNER_IDS)." >&2
  exit 1
fi
# Bot lama yang jalan pakai nohup harus dimatiin dulu, biar nggak dobel narik update Telegram.
if pgrep -f "lp_call_bot/bot.py" >/dev/null 2>&1; then
  echo "Bot lama (nohup) masih jalan. Matiin dulu:  pkill -f lp_call_bot/bot.py   lalu jalanin script ini lagi." >&2
  exit 1
fi
mkdir -p "$HERE" "$HOME/Library/LaunchAgents"
if [ "$SRC" != "$HERE" ]; then
  (cd "$SRC" && tar -cf - --exclude .env --exclude state.json --exclude heartbeat --exclude bot.log \
    --exclude __pycache__ .) | (cd "$HERE" && tar -xf -)
fi
# watcher.py decodes chain logs with four stdlib-only modules from rh_wallet_finder. Copy just those (no database,
# no .env, no keys): the installed copy has no rh_wallet_finder folder next to it.
RH_SRC="$SRC/../rh_wallet_finder"
for f in chain config rpc keccak; do
  if [ ! -f "$RH_SRC/$f.py" ]; then
    echo "Nggak nemu $RH_SRC/$f.py. Jalanin install.sh dari folder lp_call_bot di dalam repo Capstone1." >&2
    exit 1
  fi
done
mkdir -p "$HERE/rh_wallet_finder"
for f in chain config rpc keccak; do cp "$RH_SRC/$f.py" "$HERE/rh_wallet_finder/$f.py"; done
[ -f "$HERE/.env" ] || cp "$SRC/.env" "$HERE/.env"
[ -f "$HERE/state.json" ] || { [ -f "$SRC/state.json" ] && cp "$SRC/state.json" "$HERE/state.json" || true; }
chmod 600 "$HERE/.env"
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
    echo "Jalan dari: $HERE   (kode ada di repo; habis git pull jalanin sh lp_call_bot/install.sh lagi)"
    echo "Restart:  launchctl kickstart -k gui/\$(id -u)/$LABEL"
    echo "Log:      tail -f $HERE/bot.log"
    echo "Sehat kalau $HERE/heartbeat diperbarui tiap ~1 menit."
    exit 0
  fi
  sleep 2
done
echo "Gagal start. Coba:  launchctl bootstrap gui/\$(id -u) $PLIST" >&2
exit 1
