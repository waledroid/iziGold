@echo off
title XAU Assistant Launcher
setlocal

rem repo location: prefer the repo this script lives in, else the default path
set "REPO_WIN=C:\Users\%USERNAME%\Desktop\xau"
if exist "%~dp0..\service\app\main.py" for %%I in ("%~dp0..") do set "REPO_WIN=%%~fI"

rem --- 0. WSL distro present? ---
wsl.exe -d Ubuntu-24.04 -e true >nul 2>&1
if errorlevel 1 (
  echo [!] WSL distro Ubuntu-24.04 not found.
  echo     Run:  wsl --install -d Ubuntu-24.04   then reboot and re-run this launcher.
  pause
  exit /b 1
)

rem --- 1. repo present? clone on first install ---
if not exist "%REPO_WIN%\service\app\main.py" (
  echo [*] Project not found - cloning into %REPO_WIN% ...
  wsl.exe -d Ubuntu-24.04 -e bash -c "git clone https://github.com/waledroid/iziGold.git $(wslpath -u %REPO_WIN:\=/%)"
)
if not exist "%REPO_WIN%\service\app\main.py" (
  echo [!] Clone failed - check network/GitHub access, then re-run.
  pause
  exit /b 1
)

rem --- 2. MetaTrader 5 installed? running? ---
if not exist "C:\Program Files\MetaTrader 5\terminal64.exe" (
  echo [!] MetaTrader 5 is not installed - opening the download page.
  start https://www.metatrader5.com/en/download
  echo     Install MT5, run it once and log in to your broker account,
  echo     then re-run this launcher.
  pause
  exit /b 1
)
tasklist /FI "IMAGENAME eq terminal64.exe" | find /I "terminal64.exe" >nul
if errorlevel 1 start "" "C:\Program Files\MetaTrader 5\terminal64.exe"

rem --- 3. service: idempotent setup (first run: venv+deps+telegram; later: skips) ---
start "XAU Service" wsl.exe -d Ubuntu-24.04 --cd "%REPO_WIN%" -e bash -c "bash scripts/setup.sh; echo; read -p Done._Press_Enter_to_close _"
