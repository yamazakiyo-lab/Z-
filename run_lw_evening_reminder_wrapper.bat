@echo off
REM run_lw_evening_reminder_wrapper.bat
REM Execute evening reminder task via lw_annotation_bot.py

cd /d "C:\Users\user\tseg_vscode\Zフォルダ整理"
python lw_annotation_bot.py --evening-reminder

exit /b %ERRORLEVEL%
