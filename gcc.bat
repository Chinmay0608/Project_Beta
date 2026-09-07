@echo off
REM Quick launcher for gcc-job-radar without manual venv activation
setlocal
set SCRIPT_DIR=%~dp0

if "%1"=="sync-skillbridge" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" "%SCRIPT_DIR%tools\sync_to_skillbridge.py" %2 %3 %4 %5
    exit /b %ERRORLEVEL%
)

"%SCRIPT_DIR%.venv\Scripts\gcc-job-radar.exe" %*
