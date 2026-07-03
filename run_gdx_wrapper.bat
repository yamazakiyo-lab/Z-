@echo off
set "SCRIPT_DIR=%~dp0"

REM Git 最新コード取得（ローカル変更を一時保存）
cd /d "%SCRIPT_DIR%"
git stash
git pull origin master
git stash pop

REM PowerShell スクリプト実行
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_gdx_logged.ps1"
