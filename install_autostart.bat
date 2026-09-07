@echo off
setlocal
cd /d "%~dp0"
echo =========================================================
echo   Enabling GCC Job Radar Bot Auto-Start on Windows Boot
echo =========================================================

set PYTHONW_PATH=%~dp0.venv\Scripts\pythonw.exe
set SCRIPT_ARG=tools\bot_listener.py
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT_PATH=%STARTUP_DIR%\GCC Job Radar Bot.lnk

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$wsh = New-Object -ComObject WScript.Shell; " ^
    "$shortcut = $wsh.CreateShortcut('%SHORTCUT_PATH%'); " ^
    "$shortcut.TargetPath = '%PYTHONW_PATH%'; " ^
    "$shortcut.Arguments = '%SCRIPT_ARG%'; " ^
    "$shortcut.WorkingDirectory = '%~dp0'; " ^
    "$shortcut.Description = 'GCC Job Radar Background Telegram Bot'; " ^
    "$shortcut.Save()"

if exist "%SHORTCUT_PATH%" (
    echo.
    echo [SUCCESS] Auto-start configured!
    echo The bot will now start automatically in the background whenever your PC boots / logs in.
    echo Shortcut installed to:
    echo "%SHORTCUT_PATH%"
) else (
    echo.
    echo [ERROR] Failed to create startup shortcut.
)

echo =========================================================
echo Press any key to close...
if "%1" neq "--no-pause" pause >nul
