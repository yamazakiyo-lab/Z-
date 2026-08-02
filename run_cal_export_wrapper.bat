@echo off
rem LINE WORKSカレンダーのスナップショットをBlobへ(AI Q&Aの予定回答用)
rem タスクスケジューラから毎時実行される。8:30(朝あいさつ)・0:00(夜間ラン)でも
rem 更新されるため、本タスクはその間を埋める役。
cd /d "%~dp0"
py lw_calendar.py --export
exit /b %ERRORLEVEL%
