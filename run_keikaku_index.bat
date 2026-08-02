@echo off
rem 経営計画書のAI検索インデックス取り込み(改定時に手動実行)
cd /d "%~dp0"
git pull
set PYTHONIOENCODING=utf-8
py export_keikaku_index.py %*
pause
