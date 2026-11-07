@echo off
REM DLMM Terminal - double-click to start (Windows).
REM First run: checks Python + Node, installs the Meteora SDK, makes dlmm_terminal\.env and opens it in Notepad.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" (echo Python 3.9+ belum ada. Install dari https://www.python.org/downloads/ ^(centang "Add to PATH"^) & start https://www.python.org/downloads/ & pause & exit /b 1)
where node >nul 2>nul || (echo Node.js belum ada. Install versi LTS dari https://nodejs.org/ & start https://nodejs.org/ & pause & exit /b 1)
if not exist "dlmm_terminal\executor\node_modules\@meteora-ag\dlmm" (
  echo Install Meteora SDK ^(sekali aja^)...
  pushd dlmm_terminal\executor & call npm install --no-audit --no-fund & popd
)
if not exist "dlmm_terminal\.env" (
  copy "dlmm_terminal\.env.example" "dlmm_terminal\.env" >nul
  echo dlmm_terminal\.env dibuka di Notepad. Isi ^(lihat PANDUAN^), save, tutup Notepad.
  notepad "dlmm_terminal\.env"
)
echo DLMM Terminal jalan di http://localhost:8891 - biarin jendela ini kebuka. Ctrl+C buat stop.
start "" http://localhost:8891
%PY% -m dlmm_terminal web --port 8891
pause
