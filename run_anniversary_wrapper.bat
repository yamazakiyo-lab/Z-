@echo off
rem 誕生日・入社記念日の当日通知(該当日のみ山嵜喜隆・山嵜絵里へLW通知)
rem タスクスケジューラ TSEG_記念日通知 (毎日8:00) から実行される。
cd /d "%~dp0"
py tools\lw_anniversary_notify.py
exit /b %ERRORLEVEL%
