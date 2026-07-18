@echo off
rem 重要タスクの健康診断＋LW通知（毎日1回）。異常があれば山嵜さんへLW通知。
cd /d "%~dp0"

rem 最新コードを取得してから実行
git pull origin master >> task_check.log 2>&1

rem 点検（異常時のみ通知。正常でも送りたい場合は --always を付ける）
python check_tasks_notify.py >> task_check.log 2>&1
