@echo off
REM check_mie_log.bat - GDX ログ確認用ラッパー（12:00実行）

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM PowerShell でログ検証スクリプトを実行
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%check_mie_log.ps1"

exit /b %ERRORLEVEL%
