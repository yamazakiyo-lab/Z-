@echo off
rem 過去見積のAI検索インデックス取り込み(差分。初回や作り直しは --full を付ける)
cd /d "%~dp0"
git pull
set PYTHONIOENCODING=utf-8
py export_mitsumori_index.py %*
pause
