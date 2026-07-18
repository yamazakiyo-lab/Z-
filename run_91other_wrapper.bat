@echo off
set "SCRIPT_DIR=%~dp0"

REM Git 最新コード取得（GDX側と同じ。ローカル変更を一時退避してpull）
cd /d "%SCRIPT_DIR%"
git stash
git pull origin master
git stash pop

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_91other_logged.ps1"
