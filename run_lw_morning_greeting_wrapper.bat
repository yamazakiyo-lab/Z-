@echo off
REM run_lw_morning_greeting_wrapper.bat
REM Execute morning greeting task via lw_annotation_bot.py

cd /d "C:\Users\user\tseg_vscode\Zフォルダ整理"
python lw_annotation_bot.py --morning-greeting

exit /b %ERRORLEVEL%
