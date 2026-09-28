@echo off
REM Start the JARVIS sidecar server. Run setup-windows.bat first.
setlocal
title JARVIS sidecar

set SDIR=
if exist "%~dp0sidecar\jarvis\ipc\server.py" set "SDIR=%~dp0sidecar"
if not defined SDIR if exist "%~dp0..\sidecar\jarvis\ipc\server.py" set "SDIR=%~dp0..\sidecar"
if not defined SDIR (
    echo [FAIL] Cannot find the Jarvis files.
    echo Put this file INSIDE the extracted Jarvis folder,
    echo right next to the "sidecar" folder, then double-click it.
    pause
    exit /b 1
)
cd /d "%SDIR%"

REM ---- find a REAL Python 3.9+ (rejects the Store stub) ----
set PY=
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3" "python" "python3") do (
    if not defined PY call :try_python %%~C
)
if not defined PY (
    echo [FAIL] No usable Python found. Run JARVIS-setup.bat first.
    pause
    exit /b 1
)
goto :deps

:try_python
set "CAND=%*"
%CAND% -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%CAND%"
exit /b 0

:deps
%PY% -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Server packages are missing. Run JARVIS-setup.bat first,
    echo then run this file again.
    pause
    exit /b 1
)

echo Starting JARVIS sidecar on http://localhost:8765 ...
echo Keep this window open. Close it to stop Jarvis.
echo.
%PY% -m jarvis.ipc.server
echo.
echo Sidecar stopped.
pause
exit /b 0
