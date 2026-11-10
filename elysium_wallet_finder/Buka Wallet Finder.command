#!/bin/bash
# Double-click di Finder: buka Elysium Wallet Finder (Streamlit) di browser.
# Jendela Terminal ini harus tetap kebuka: sync jalan di sini.
# caffeinate = Mac nggak sleep. Python Homebrew (PEP 668) nolak pip di luar
# virtualenv, jadi dependensi cuma di-install ke .venv di folder ini.

# Parameter expansion, not dirname: $0 can contain spaces, and a double-clicked
# Terminal still has dirname, but we don't depend on it being on PATH.
src="${BASH_SOURCE[0]}"
case "$src" in
  */*) here="${src%/*}" ;;
  *) here="." ;;
esac
cd "$here" || {
  echo "Gagal masuk ke folder launcher: $here"
  echo "Tekan Enter buat nutup."
  read -r _
  exit 1
}
here="$(pwd)"

pause_fail() {
  echo ""
  echo "$1"
  echo ""
  echo "Tekan Enter buat nutup jendela ini."
  read -r _
  exit 1
}

py_boot=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    py_boot="$c"
    break
  fi
done
[ -n "$py_boot" ] || pause_fail "python3 nggak ketemu. Install Python 3.10+ dulu (Homebrew: brew install python)."

venv="$here/.venv"
# Pakai .venv yang udah ada (termasuk yang lu bikin sendiri). Bikin baru cuma
# kalau folder-nya kosong / python-nya hilang.
if [ ! -x "$venv/bin/python3" ] && [ ! -x "$venv/bin/python" ]; then
  if [ -e "$venv" ]; then
    echo ".venv ada tapi python-nya nggak ketemu. Bikin ulang…"
    rm -rf "$venv" || pause_fail "Gagal hapus .venv yang rusak: $venv"
  else
    echo "Bikin virtualenv di .venv (sekali aja)…"
  fi
  "$py_boot" -m venv "$venv" || pause_fail "Gagal bikin .venv. Dari Terminal: $py_boot -m venv \"$venv\""
fi

if [ -x "$venv/bin/python3" ]; then
  py="$venv/bin/python3"
else
  py="$venv/bin/python"
fi

if ! "$py" -m pip --version >/dev/null 2>&1; then
  "$py" -m ensurepip --upgrade || pause_fail "pip nggak ada di .venv dan ensurepip gagal."
fi

req="$here/requirements.txt"
stamp="$venv/.requirements.sha256"
req_hash="$("$py" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$req")" \
  || pause_fail "Gagal baca requirements.txt"
saved_hash=""
[ -f "$stamp" ] && saved_hash="$(cat "$stamp")"

streamlit_ok=0
if "$py" -c "import streamlit, sys; v = tuple(int(x) for x in streamlit.__version__.split('.')[:2]); sys.exit(0 if v >= (1, 40) else 1)" 2>/dev/null; then
  streamlit_ok=1
fi

# Install cuma kalau Streamlit belum ada / kekecilan, atau requirements.txt
# berubah sejak install terakhir. .venv yang udah lengkap (lu bikin sendiri)
# nggak di-pip lagi; hash-nya dicatat biar run berikutnya tetap diem.
if [ "$streamlit_ok" -ne 1 ] || { [ -n "$saved_hash" ] && [ "$saved_hash" != "$req_hash" ]; }; then
  echo "Install dependensi ke .venv (sekali, atau kalau requirements.txt berubah)…"
  "$py" -m pip install -U -r "$req" || pause_fail "pip install gagal di dalam .venv. Python sistem (Homebrew) nggak dipakai."
  printf '%s\n' "$req_hash" > "$stamp" || pause_fail "Gagal nulis $stamp"
elif [ "$saved_hash" != "$req_hash" ]; then
  printf '%s\n' "$req_hash" > "$stamp" || pause_fail "Gagal nulis $stamp"
fi

# caffeinate cuma ada di Mac. Tanpa itu, python-nya tetep jalan.
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i "$py" "$here/finder.py"
  status=$?
else
  "$py" "$here/finder.py"
  status=$?
fi

if [ "$status" -ne 0 ]; then
  pause_fail "Wallet Finder berhenti (exit $status)."
fi
exit 0
