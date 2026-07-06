@echo off
cd /d "%~dp0"
REM ドライランモード判定
set "isDryRun=%1"
if "%isDryRun%"=="--dry-run" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_lw_send_logged.ps1" -DryRun
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_lw_send_logged.ps1"
)
exit /b %ERRORLEVEL%
