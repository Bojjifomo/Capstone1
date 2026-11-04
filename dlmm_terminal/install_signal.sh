#!/bin/sh
# Jalanin DLMM entry signal 24/7 di Mac mini lewat launchd: nyala sendiri tiap login, restart sendiri kalau mati,
# Mac nggak sleep selama jalan (caffeinate). Ping ke Telegram.
#   sh dlmm_terminal/install_signal.sh            pasang / update (habis git pull, jalanin lagi)
#   sh dlmm_terminal/install_signal.sh uninstall  copot
#
# Kode dikopi ke ~/Projects/dlmm_signal dan jalan dari situ (launchd nggak bisa start dari disk eksternal /Volumes).
# .env di sana CUMA berisi DLMM_TG_BOT_TOKEN + DLMM_TG_CHAT_ID: signal nggak butuh private key, jadi key nggak ikut dikopi.
# Data (benchmark.json, signals.jsonl, candidates.jsonl, signal_state.json) ada di ~/Projects/dlmm_signal/dlmm_terminal/data.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${DLMM_SIGNAL_HOME:-$HOME/Projects/dlmm_signal}"
APP="$HOME_DIR/dlmm_terminal"
LABEL="com.bojjifomo.dlmm-signal"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || echo /usr/bin/python3)"
LOG="$HOME_DIR/signal.log"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "uninstall" ]; then
  rm -f "$PLIST"
  echo "DLMM signal dicopot dari launchd. Data tetap ada di $APP/data."
  exit 0
fi

# Telegram token + chat id: from the env, the repo's dlmm_terminal/.env, the repo root .env, or an earlier install.
val() {
  eval "v=\${$1:-}"
  if [ -z "$v" ]; then
    for f in "$SRC/.env" "$SRC/../.env" "$APP/.env"; do
      [ -f "$f" ] || continue
      v="$(grep -E "^$1=" "$f" | tail -1 | cut -d= -f2- | tr -d '"'"'" | tr -d '[:space:]')"
      [ -n "$v" ] && break
    done
  fi
  printf %s "$v"
}
TOKEN="$(val DLMM_TG_BOT_TOKEN)"
CHAT="$(val DLMM_TG_CHAT_ID)"
if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "Isi dulu DLMM_TG_BOT_TOKEN dan DLMM_TG_CHAT_ID di $SRC/.env (template: .env.example), lalu jalanin lagi." >&2
  echo "  token: chat @BotFather → /newbot   ·   chat id: chat @userinfobot, lalu /start ke bot lu sekali" >&2
  exit 1
fi
if pgrep -f "dlmm_terminal signal" >/dev/null 2>&1; then
  echo "Ada 'dlmm_terminal signal' lain yang masih jalan (manual di Terminal). Matiin dulu biar ping nggak dobel:" >&2
  echo "  pkill -f 'dlmm_terminal signal'   lalu jalanin script ini lagi." >&2
  exit 1
fi

# Telegram check before installing, so a wrong token doesn't fail silently for days.
if ! "$PY" - "$TOKEN" "$CHAT" <<'PYEOF'
import sys, urllib.parse, urllib.request, ssl
try:
    import certifi; ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:
    ctx = ssl.create_default_context()
tok, chat = sys.argv[1], sys.argv[2]
body = urllib.parse.urlencode({"chat_id": chat, "text": "✅ DLMM signal: Telegram nyambung, watcher dipasang di Mac mini."}).encode()
import json, urllib.error
try:
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=body, timeout=15, context=ctx).read()
except urllib.error.HTTPError as e:
    try:
        why = json.load(e).get("description", "")
    except Exception:
        why = ""
    print(f"Telegram gagal: HTTP {e.code} {why}", file=sys.stderr)
    if "chat not found" in why:
        print("  → chat id salah, atau lu belum pernah chat bot-nya. Buka bot lu di Telegram, tekan Start / kirim /start,", file=sys.stderr)
        print("    lalu cek DLMM_TG_CHAT_ID = angka 'Id' dari @userinfobot (grup: diawali -100…).", file=sys.stderr)
    elif e.code == 401:
        print("  → token salah: copy ulang dari @BotFather (formatnya 123456789:AA…).", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Telegram gagal: {e}", file=sys.stderr); sys.exit(1)
PYEOF
then
  echo "Cek token / chat id (dan pastiin lu udah /start ke bot-nya), lalu jalanin lagi." >&2
  exit 1
fi

mkdir -p "$APP" "$HOME/Library/LaunchAgents"
if [ "$SRC" != "$APP" ]; then
  (cd "$SRC" && tar -cf - --exclude .env --exclude data --exclude node_modules --exclude __pycache__ .) | (cd "$APP" && tar -xf -)
fi
umask 077
printf 'DLMM_TG_BOT_TOKEN=%s\nDLMM_TG_CHAT_ID=%s\n' "$TOKEN" "$CHAT" > "$APP/.env"
chmod 600 "$APP/.env"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$PY</string><string>-u</string><string>-m</string><string>dlmm_terminal</string><string>signal</string>
  </array>
  <key>WorkingDirectory</key><string>$HOME_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PL
for i in 1 2 3 4 5 6 7 8 9 10; do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 1
done
for i in 1 2 3 4 5; do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
    echo "DLMM signal jalan 24/7 lewat launchd ($LABEL)."
    echo "Jalan dari: $APP   (habis git pull: sh dlmm_terminal/install_signal.sh lagi)"
    echo "Log:        tail -f $LOG     (tiap 5 menit ada baris 'scan done')"
    echo "Restart:    launchctl kickstart -k gui/\$(id -u)/$LABEL"
    echo "Copot:      sh dlmm_terminal/install_signal.sh uninstall"
    echo "Scan pertama ~1 menit lagi: satu pesan ringkasan ke Telegram, abis itu cuma pool yang baru masuk kriteria."
    exit 0
  fi
  sleep 2
done
echo "Gagal start. Coba:  launchctl bootstrap gui/\$(id -u) $PLIST" >&2
exit 1
