@echo off
cd /d "%~dp0"
REM ログ出力用（エラー診断用）
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set date_ymd=%%c%%a%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set time_hms=%%a%%b)
set logfile=logs\lw_send_%date_ymd%_%time_hms%.log
if not exist logs mkdir logs

py -3 "%~dp0lw_annotation_bot.py" --send >> "%logfile%" 2>&1
set exitcode=%errorlevel%
echo Exit code: %exitcode% >> "%logfile%"
exit /b %exitcode%
