@echo off
REM Double-click this file to start the Camera App.
cd /d "%~dp0"

REM Find a working, up-to-date Python (newest first). The test "list[str]" fails on
REM very old or pre-release Pythons, which can't run modern pip/OpenCV.
set "PY="
for %%V in (3.16 3.15 3.14 3.13 3.12 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "list[str]" >nul 2>nul && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "list[str]" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo [!] No suitable Python found. Please install the latest Python from
    echo     https://www.python.org/downloads/  and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)
echo Using: %PY%
%PY% --version

echo Checking required packages (first time may take a minute)...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo [!] Could not install the required packages. Please send a screenshot of this window.
    pause
    exit /b 1
)

echo Starting Camera App...
%PY% camera_ui.py
if errorlevel 1 pause
