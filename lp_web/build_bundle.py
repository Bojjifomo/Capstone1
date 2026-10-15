#!/usr/bin/env python3
"""Pack LP Web and the RH scripts into one ZIP to hand to someone else.

    python3 build_bundle.py --scripts "RH Pool.js" "RH Portfolio.js" \\
        --secrets "RH Secrets.js" --out "LP Web.zip"

The recipient unzips it, fills in RH Secrets.js, and double-clicks the
launcher. The scripts travel inside the ZIP (scripts/), so nothing has to be
in their iCloud.

This refuses to build if any key in the secrets template or in a script's own
key constants is filled in, so a personal key cannot leave by accident.
"""

import argparse
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "LP Web"

KEY_NAMES = ("ALCHEMY_API_KEY", "PRIVATE_KEY", "BLOCKSCOUT_API_KEY", "UNISWAP_API_KEY")
# KEY: "value"  (secrets file)   or   const KEY = "value";  (script constant)
KEY_RE = re.compile(r'\b(%s)\b\s*[:=]\s*(["\'`])(.*?)\2' % "|".join(KEY_NAMES))
HEX64_RE = re.compile(r"\b(0x)?[0-9a-fA-F]{64}\b")

README = """LP Web
======

Tabel LP Uniswap V4 di Robinhood Chain (RH Portfolio dan RH Pool), dibuka
dari browser di laptop.

1. Isi RH Secrets.js
   Buka "RH Secrets.js" pakai TextEdit / Notepad, isi key lu sendiri di
   antara tanda kutip, terus save. Penjelasan tiap key ada di file itu.
   PRACTICE_MODE biarin true dulu: semua tombol cuma simulasi, nggak ada
   transaksi yang dikirim. Ganti ke false kalau udah yakin.

   JANGAN kirim RH Secrets.js yang udah diisi ke siapa pun. Siapa pun yang
   pegang PRIVATE_KEY bisa ngabisin isi wallet lu.

2. Jalanin
   Mac:     double-click "Buka LP Web.command".
            Kalau diblok ("cannot be opened"), klik kanan > Open > Open.
            Kalau diminta install "command line developer tools", klik
            Install, tunggu, terus double-click lagi.
   Windows: install Python dari python.org (centang "Add python.exe to
            PATH"), terus double-click "Buka LP Web.bat".

   Browser kebuka sendiri ke http://127.0.0.1:4663. Pilih RH Portfolio atau
   RH Pool. Jendela Terminal / Command Prompt-nya jangan ditutup selama
   dipake; ditutup = webnya mati.

Catatan
- Semua jalan di laptop lu sendiri. Key nggak dikirim ke mana-mana selain
  ke API yang dipake script-nya (Alchemy, Blockscout, Uniswap).
- Tiap zap / close selalu lewat dialog konfirmasi. Tombol yang beneran kirim
  transaksi warnanya merah dan harus diklik, Enter nggak bisa.
- Tombol "Log" di kanan atas nunjukin log script kalau ada yang error.
"""

BAT = ('@echo off\r\ncd /d "%~dp0"\r\n'
       'where py >nul 2>nul && (py server.py) || (python server.py)\r\npause\r\n')


def check_keys(path, text, label):
    bad = []
    for m in KEY_RE.finditer(text):
        if m.group(3).strip():
            bad.append(m.group(1))
    for line in text.splitlines():
        if "PRIVATE_KEY" in line and HEX64_RE.search(line):
            bad.append("PRIVATE_KEY")
    if bad:
        sys.exit("refusing to build: %s %s has %s filled in. Blank it first."
                 % (label, path, ", ".join(sorted(set(bad)))))


def add(z, arc, data=None, src=None, mode=0o644):
    info = zipfile.ZipInfo(ROOT + "/" + arc, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100000 | mode) << 16
    if src is not None:
        with open(src, "rb") as f:
            data = f.read()
    if isinstance(data, str):
        data = data.encode("utf-8")
    z.writestr(info, data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scripts", nargs="+", required=True, help="Scriptable .js files to include")
    ap.add_argument("--secrets", required=True, help="an RH Secrets.js with every key blank")
    ap.add_argument("--out", default="LP Web.zip")
    a = ap.parse_args()

    with open(a.secrets, "r", encoding="utf-8") as f:
        secrets_text = f.read()
    check_keys(a.secrets, secrets_text, "secrets template")
    if re.search(r"\bPRACTICE_MODE\s*:\s*false\b", secrets_text):
        sys.exit("refusing to build: the secrets template has PRACTICE_MODE: false. "
                 "Ship it as true so a first run never sends anything.")

    scripts = []
    for p in a.scripts:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
        check_keys(p, text, "script")
        name = os.path.basename(p)
        # Uploaded copies can arrive as "RH_Pool.js"; Scriptable names have spaces.
        name = re.sub(r"^RH_", "RH ", name)
        if name.lower().startswith(("rh secrets", "rhsecrets")):
            sys.exit("pass the secrets file with --secrets, not --scripts")
        scripts.append((name, text))

    with zipfile.ZipFile(a.out, "w") as z:
        add(z, "BACA DULU.txt", README)
        add(z, "RH Secrets.js", secrets_text)
        add(z, "Buka LP Web.command", src=os.path.join(HERE, "Buka LP Web.command"), mode=0o755)
        add(z, "Buka LP Web.bat", BAT)
        add(z, "server.py", src=os.path.join(HERE, "server.py"), mode=0o755)
        for f in sorted(os.listdir(os.path.join(HERE, "static"))):
            add(z, "static/" + f, src=os.path.join(HERE, "static", f))
        for name, text in scripts:
            add(z, "scripts/" + name, text)
    print("wrote %s (%d scripts)" % (a.out, len(scripts)))


if __name__ == "__main__":
    main()
