@echo off
REM ============================================================
REM  JARVIS one-click voice setup for Windows.
REM  Double-click this file. It installs everything and verifies.
REM  Everything printed is ALSO saved to setup-log.txt next to
REM  this file - if anything fails, send that log for diagnosis.
REM ============================================================

REM ---- re-launch with live output + log file ----
if not defined JARVIS_LOG (
    set JARVIS_LOG=1
    where powershell >nul 2>&1
    if not errorlevel 1 (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%~f0' | Tee-Object -FilePath '%~dp0setup-log.txt'"
        echo.
        echo ------------------------------------------------------------
        echo  Log saved to: %~dp0setup-log.txt
        echo  If something failed, send that file (or its last lines).
        echo ------------------------------------------------------------
        pause
        exit /b
    )
)

setlocal enabledelayedexpansion
title JARVIS setup

REM ---- find the Jarvis root (works from root or from packaging/) ----
set ROOT=
if exist "%~dp0sidecar\jarvis\models.py" set "ROOT=%~dp0"
if not defined ROOT if exist "%~dp0..\sidecar\jarvis\models.py" set "ROOT=%~dp0.."
if not defined ROOT (
    echo [FAIL] Cannot find the Jarvis files.
    echo Put this file INSIDE the extracted Jarvis folder,
    echo right next to the "sidecar" folder, then double-click it.
    echo (Do NOT run it from inside the zip - extract the zip first.)
    pause
    exit /b 1
)
cd /d "%ROOT%"
echo Working folder: %CD%
echo.

echo ============================================================
echo  JARVIS voice setup - sit back, this takes a few minutes
echo ============================================================
echo.

REM ---- 1. Find a REAL Python 3.9+ (rejects the Store stub) ----
set PY=
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3" "python" "python3") do (
    if not defined PY call :try_python %%~C
)
if not defined PY (
    echo No usable Python found. Trying to install Python 3.11...
    where winget >nul 2>&1
    if errorlevel 1 goto :nopython
    winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements
    if errorlevel 1 goto :nopython
    set "PY=%LocalAppData%\Programs\Python\Python311\python.exe"
    call :try_python "%PY%"
    if not defined PY goto :nopython
)
echo [1/4] PASS - Python: %PY%
%PY% --version
echo.
goto :step2

:nopython
echo [FAIL] Could not get Python automatically. Do this once manually:
echo   1. Open https://www.python.org/downloads/
echo   2. Install Python 3.11 or newer
echo   3. IMPORTANT: tick "Add python.exe to PATH" during install
echo   4. Run this file again
pause
exit /b 1

:try_python
set "CAND=%*"
%CAND% -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%CAND%"
exit /b 0

:step2
REM ---- 2. Install packages ----
echo [2/4] Installing voice packages (a few minutes, watch progress)...
%PY% -m pip install --disable-pip-version-check piper-tts faster-whisper openwakeword sounddevice fastapi uvicorn
if errorlevel 1 (
    echo [FAIL] Package install failed. Usual causes:
    echo   - internet dropped mid-download: just run this file again, it resumes
    echo   - broken pip: run "%PY% -m pip install --upgrade pip" then retry
    pause
    exit /b 1
)
echo [2/4] PASS - packages installed
echo.

REM ---- 3. voice.setup: download models + self-test ----
echo [3/4] Downloading voice models and running self-test...
if exist setup_result.json del setup_result.json
%PY% -c "import sys; sys.path.insert(0, 'sidecar'); from jarvis.tools.builtin.voice_pack import setup_handler; import json; r = setup_handler({'install': True, 'download': True}); print('RESULT:' + json.dumps({'voice_ready': r['voice_ready'], 'tts_ready': r['tts_ready'], 'listen_ready': r['listen_ready'], 'self_test': r['self_test']})); open('setup_result.json','w').write(json.dumps(r))"
if errorlevel 1 (
    echo [FAIL] Voice setup crashed (see messages above).
    echo Run this file again; if it repeats, send setup-log.txt.
    pause
    exit /b 1
)
if not exist setup_result.json (
    echo [FAIL] Voice setup produced no result. Send setup-log.txt.
    pause
    exit /b 1
)
%PY% -c "import json,sys; r=json.load(open('setup_result.json')); sys.exit(0 if r.get('voice_ready') else 1)" >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Voice setup finished but voice is NOT ready. Details:
    %PY% -c "import json; r=json.load(open('setup_result.json')); [print(' ', k, '=', json.dumps(v)[:300]) for k, v in r.items() if k != 'backends']"
    echo Send setup-log.txt for diagnosis.
    pause
    exit /b 1
)
echo [3/4] PASS - voice ready, self-test generated real speech
echo.

REM ---- 4. Speak out loud ----
echo [4/4] Speaking test - you should HEAR Jarvis now...
if exist speak_result.json del speak_result.json
%PY% -c "import sys; sys.path.insert(0, 'sidecar'); from jarvis.tools.builtin.voice_pack import speak_handler; import json; r=speak_handler({'text':'Jarvis systems online. Voice setup complete.'}); print(json.dumps(r)); open('speak_result.json','w').write(json.dumps(r))"
%PY% -c "import json,sys; r=json.load(open('speak_result.json')); sys.exit(0 if r.get('played') else 1)" >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Jarvis could not play sound on your speakers. Reason:
    %PY% -c "import json; print(' ', json.load(open('speak_result.json')).get('error','unknown error'))"
    echo Fix: speakers/headphones plugged in, volume up,
    echo Windows sound output set to the right device, then run this again.
    pause
    exit /b 1
)
echo [4/4] PASS - you heard Jarvis speak
echo.
echo ============================================================
echo  DONE. Voice setup complete.
echo  To start Jarvis, run:  start-jarvis.bat
echo  Then open the app and say "Jarvis".
echo ============================================================
pause
exit /b 0
