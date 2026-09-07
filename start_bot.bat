@echo off
setlocal
cd /d "%~dp0"
echo =========================================================
echo   Starting GCC Job Radar Bot in Background...
echo =========================================================
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0tools\bot_listener.py"
echo.
echo [OK] Bot process initiated!
echo  - Logs: bot.log
echo  - To check status: double-click status_bot.bat
echo  - To stop: double-click stop_bot.bat
echo =========================================================
ping 127.0.0.1 -n 3 >nul
