@echo off
rem 検索アプリ未利用者への週次LW通知（毎週月曜 8:00 に実行）
rem %~dp0 = このbatのあるフォルダ（＝リポジトリ）。マシンが変わっても動くよう相対で動作。
cd /d "%~dp0"

rem 最新のコード・対応表(name_upn_map.json)を取得してから実行
git pull origin master >> app_usage_reminder.log 2>&1

rem 本番送信（--dry-run は付けない）
python lw_annotation_bot.py --app-usage-reminder >> app_usage_reminder.log 2>&1
