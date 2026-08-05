@echo off
rem 過去見積のAI検索インデックス取り込み(差分。初回や作り直しは --full を付ける)
rem ※毎晩0:00のデイリーラン(run_mie_logged.ps1 [MITSUMORI]工程)でも自動実行される。
rem   このbatは即時反映したいときの手動用。
cd /d "%~dp0"
git pull
set PYTHONIOENCODING=utf-8
py export_mitsumori_index.py %*
pause
