#!/bin/bash
# Double-click di Finder: buka dlmm_terminal/.env di TextEdit buat isi Telegram, lalu pasang DLMM signal 24/7 (launchd).
cd "$(dirname "$0")/.." || exit 1
ENV=dlmm_terminal/.env
[ -f "$ENV" ] || cp dlmm_terminal/.env.example "$ENV"
chmod 600 "$ENV"
# .env lama (sebelum ada signal) belum punya baris Telegram: tambahin yang kosong.
for k in DLMM_TG_BOT_TOKEN DLMM_TG_CHAT_ID; do
  grep -q "^$k=" "$ENV" || printf '\n%s=' "$k" >> "$ENV"
done
open -e "$ENV"
echo "dlmm_terminal/.env kebuka di TextEdit."
echo "Isi DLMM_TG_BOT_TOKEN (dari @BotFather) dan DLMM_TG_CHAT_ID (dari @userinfobot), save (Cmd+S),"
echo "lalu tekan Enter di sini buat pasang signal 24/7. Ctrl+C = batal."
read -r _
sh dlmm_terminal/install_signal.sh
echo
read -r -p "Selesai. Tekan Enter buat nutup jendela ini." _
