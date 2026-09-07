@echo off
setlocal
cd /d "%~dp0"
echo =========================================================
echo   GCC Job Radar - Background Bot Status
echo =========================================================

set RUNNING=0

if exist "bot.pid" (
    for /f "usebackq tokens=*" %%a in ("bot.pid") do set BOT_PID=%%a
    if defined BOT_PID (
        tasklist /FI "PID eq %BOT_PID%" 2>nul | findstr /i "%BOT_PID%" >nul
        if %ERRORLEVEL% EQU 0 (
            echo STATUS: [RUNNING] Active in background
            echo   - Process ID: %BOT_PID%
            set RUNNING=1
        )
    )
)

if %RUNNING% EQU 0 (
    echo STATUS: [STOPPED] Not running.
    echo   - To start: double-click start_bot.bat or start_bot_background.vbs
)

if exist "bot.log" (
    echo.
    echo ----------------- Recent Logs: bot.log -----------------
    powershell -NoProfile -Command "Get-Content 'bot.log' -Tail 10"
    echo ---------------------------------------------------------
)

echo.
echo Press any key to close this window...
if "%1" neq "--no-pause" pause >nul
