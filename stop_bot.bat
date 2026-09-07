@echo off
setlocal
cd /d "%~dp0"
echo =========================================================
echo   Stopping GCC Job Radar Background Bot...
echo =========================================================

set STOPPED=0

if exist "bot.pid" (
    for /f "usebackq tokens=*" %%a in ("bot.pid") do set BOT_PID=%%a
    if defined BOT_PID (
        echo Found PID file (PID: %BOT_PID%)
        taskkill /PID %BOT_PID% /F >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            echo [OK] Stopped bot process with PID: %BOT_PID%
            set STOPPED=1
        )
    )
    del /f /q "bot.pid" >nul 2>&1
)

:: Also clean up any lingering bot_listener.py processes via PowerShell
powershell -NoProfile -Command ^
    "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bot_listener.py*' }; " ^
    "if ($procs) { foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force; Write-Host ('[OK] Stopped lingering bot process (PID: ' + $p.ProcessId + ')') }; exit 0 } else { exit 1 }" >nul 2>&1

if %ERRORLEVEL% EQU 0 (
    set STOPPED=1
)

if %STOPPED% EQU 1 (
    echo.
    echo [SUCCESS] GCC Job Radar bot is stopped.
) else (
    echo.
    echo [INFO] No running bot process was found.
)

echo =========================================================
ping 127.0.0.1 -n 3 >nul
