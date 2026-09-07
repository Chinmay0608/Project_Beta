@echo off
setlocal
echo =========================================================
echo   Disabling GCC Job Radar Bot Auto-Start
echo =========================================================

set SHORTCUT_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GCC Job Radar Bot.lnk

if exist "%SHORTCUT_PATH%" (
    del /f /q "%SHORTCUT_PATH%"
    echo.
    echo [SUCCESS] Auto-start shortcut removed.
) else (
    echo.
    echo [INFO] Auto-start shortcut was not found.
)

echo =========================================================
ping 127.0.0.1 -n 3 >nul
