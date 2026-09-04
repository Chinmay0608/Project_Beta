@echo off
REM Quick launcher for gcc-job-radar without manual venv activation
setlocal
set SCRIPT_DIR=%~dp0
"%SCRIPT_DIR%.venv\Scripts\gcc-job-radar.exe" %*
