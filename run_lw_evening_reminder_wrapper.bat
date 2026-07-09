@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_lw_evening_logged.ps1"
exit /b %ERRORLEVEL%
