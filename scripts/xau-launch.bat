@echo off
title XAU Assistant Launcher
rem --- 1. MetaTrader 5 (skip if already running) ---
tasklist /FI "IMAGENAME eq terminal64.exe" | find /I "terminal64.exe" >nul
if errorlevel 1 start "" "C:\Program Files\MetaTrader 5\terminal64.exe"
rem --- 2. WSL: service via the idempotent setup script (also wakes cron) ---
start "XAU Service" wsl.exe -d Ubuntu-24.04 --cd /mnt/c/Users/aatanda/Desktop/xau -e bash -c "bash scripts/setup.sh; echo; read -p 'Done - press Enter to close this window' _"
