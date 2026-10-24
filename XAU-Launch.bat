@echo off
rem Windows compatibility entrypoint. The shared cross-platform launcher is
rem launch.py; use `py -3 launch.py` on Windows or `python3 launch.py` on Linux.
set "PYTHON="
where py >nul 2>&1 && set "PYTHON=py -3"
if not defined PYTHON where python >nul 2>&1 && set "PYTHON=python"
if not defined PYTHON (
  where winget >nul 2>&1 || (
    echo [!] Python and winget are unavailable. Install Python 3.12, then re-run.
    exit /b 1
  )
  echo [*] Python not found - installing Python 3.12 via winget ...
  winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
  for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python311\python.exe") do (
    if not defined PYTHON if exist "%%~P" set "PYTHON=%%~P"
  )
)
if not defined PYTHON (
  echo [!] Python installation was not found. Open a new terminal and re-run.
  exit /b 1
)
%PYTHON% "%~dp0launch.py" %*
exit /b %errorlevel%
